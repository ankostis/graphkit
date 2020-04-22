# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""About :term:`operation` nodes (but not net-ops to break cycle)."""

import abc
import itertools as itt
import logging
import textwrap
from collections import abc as cabc
from collections import namedtuple
from typing import Any, Callable, List, Mapping, Tuple, Union

from boltons.setutils import IndexedSet as iset

from .base import (
    NO_RESULT,
    UNSET,
    Items,
    MultiValueError,
    Plottable,
    aslist,
    astuple,
    func_name,
    jetsam,
)
from .config import is_debug, is_reschedule_operations, is_solid_true
from .modifiers import kw, optional, sideffect, sideffected, vararg, varargs

log = logging.getLogger(__name__)


def _dict_without(kw, *todel):
    return {k: v for k, v in kw.items() if k not in todel}


def as_renames(i, argname):
    """
    Parses a list of (source-->destination) from dict, list-of-2-items, single 2-tuple.

    .. Note::
        The same `source` may be repeatedly renamed to multiple `destinations`.
    """
    if not i:
        return ()

    def is_list_of_2(i):
        try:
            return all(len(ii) == 2 for ii in i)
        except Exception:
            pass  # Let it be, it may be a dictionary...

    if isinstance(i, tuple) and len(i) == 2:
        i = [i]
    elif not isinstance(i, cabc.Collection):
        raise ValueError(
            f"Argument {argname} must be a list of 2-element items, was: {i!r}"
        ) from None
    elif not is_list_of_2(i):
        try:
            i = list(dict(i).items())
        except Exception as ex:
            raise ValueError(f"Cannot dict-ize {argname}({i!r}) due to: {ex}") from None

    return i


def reparse_operation_data(
    name, needs, provides
) -> Tuple[cabc.Hashable, cabc.Collection, cabc.Collection]:
    """
    Validate & reparse operation data as lists.

    :return:
        name, needs, provides,

    As a separate function to be reused by client building operations,
    to detect errors early.
    """

    if not isinstance(name, cabc.Hashable):
        raise ValueError(f"Operation `name` must be hashable, got: {name}")

    # Allow single string-value for needs parameter
    needs = astuple(needs, "needs", allowed_types=cabc.Collection)
    if not all(isinstance(i, str) for i in needs):
        raise ValueError(f"All `needs` must be str, got: {needs!r}")

    # Allow single value for provides parameter
    provides = astuple(provides, "provides", allowed_types=cabc.Collection)
    if not all(isinstance(i, str) for i in provides):
        raise ValueError(f"All `provides` must be str, got: {provides!r}")

    return name, needs, provides


class Operation(abc.ABC):
    """An abstract class representing an action with :meth:`.compute()`."""

    @property
    def __name__(self) -> str:
        return self.name  # pylint: disable=no-member

    @abc.abstractmethod
    def compute(self, named_inputs, outputs=None):
        """
        Compute (optional) asked `outputs` for the given `named_inputs`.

        It is called by :class:`.Network`.
        End-users should simply call the operation with `named_inputs` as kwargs.

        :param named_inputs:
            the input values with which to feed the computation.
        :returns list:
            Should return a list values representing
            the results of running the feed-forward computation on
            ``inputs``.
        """


def _spread_sideffects(
    deps: cabc.Collection,
) -> Tuple[cabc.Collection, cabc.Collection]:
    """
    Build fn/op dependencies from user ones by spreading any :term:`sideffects`.

    :return:
        the given `deps` duplicated as ``(fn_deps,  op_deps)``, where any instances of
        :class:`.sideffects` and :class:`.sideffected` are processed like this:

        `fn_deps`
            - :class:`.sideffected` are replaced by the pure :attr:`.sideffected.fn_dependency`
              consumed/produced by underlying functions, in the order it is first met
              (any duplicates are discarded).
            - :class:`.sideffects` are simply dropped;

        `op_deps`
            :class:`.sideffected` are replaced by a sequence of "singular" `sideffected`
            instances, one for each item in their :attr:`.sideffected.sideffects` attribute,
            in the order they are first met
            (any duplicates are discarded);
    """

    def to_fn_deps(dep):
        return (
            (dep.fn_dependency,)
            if isinstance(dep, sideffected)
            else ()
            if isinstance(dep, sideffect)
            else (dep,)
        )

    def to_op_deps(dep):
        return (
            (sideffected(dep.fn_dependency, s) for s in dep.sideffects)
            if isinstance(dep, sideffected)
            else (dep,)
        )

    assert deps is not None

    if deps:
        fn_deps = iset(nn for n in deps for nn in to_fn_deps(n))
        op_deps = iset(nn for n in deps for nn in to_op_deps(n))
        return fn_deps, op_deps
    else:
        return deps, deps


class FunctionalOperation(Operation):
    """
    An :term:`operation` performing a callable (ie a function, a method, a lambda).

    .. Tip::
        Use :class:`operation()` builder class to build instances of this class instead.
    """

    def __init__(
        self,
        # def __new__(
        # cls,
        fn: Callable,
        name,
        needs: Items = None,
        provides: Items = None,
        aliases: Mapping = None,
        *,
        parents: Tuple = None,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        returns_dict=None,
        node_props: Mapping = None,
    ):
        """
        Build a new operation out of some function and its requirements.

        See :class:`.operation` for the full documentation of parameters.

        .. attribute:: FunctionalOperation.name

            a name for the operation (e.g. `'conv1'`, `'sum'`, etc..);
            it will be prefixed by `parents`.
        .. attribute:: FunctionalOperation.needs

            Names of input data objects this operation requires.
        .. attribute:: FunctionalOperation.provides

            Names of the **real output values** the underlying function provides
            (without `aliases`, with(!) `sideffects`)

            NOTE that the instance attribute eventually includes aliases & sideffects.
        .. attribute:: FunctionalOperation.fn_provides

            Value names the underlying function provides (without aliases, with(!) sideffects).

            FIXME: `fn_provides` not sure what it does with sideffects.
        .. attribute:: FunctionalOperation.aliases

            an optional mapping of real `provides` to additional ones, together
            comprising this operations :term:`provides`.
        .. attribute:: FunctionalOperation.parents

            a tuple wth the names of the parents, prefixing `name`,
            but also kept for equality/hash check.
        .. attribute:: FunctionalOperation.rescheduled

            If true, underlying *callable* may produce a subset of `provides`,
            and the :term:`plan` must then :term:`reschedule` after the operation
            has executed.  In that case, it makes more sense for the *callable*
            to `returns_dict`.
        .. attribute:: FunctionalOperation.endured

            If true, even if *callable* fails, solution will :term:`reschedule`;
            ignored if :term:`endurance` enabled globally.
        .. attribute:: FunctionalOperation.parallel

            execute in :term:`parallel`
        .. attribute:: FunctionalOperation.marshalled

            If true, operation will be :term:`marshalled <marshalling>` while computed,
            along with its `inputs` & `outputs`.
            (usefull when run in `parallel` with a :term:`process pool`).
        .. attribute:: FunctionalOperation.returns_dict

            if true, it means the `fn` returns a dictionary with all `provides`,
            and no further processing is done on them
            (i.e. the returned output-values are not zipped with `provides`)
        .. attribute:: FunctionalOperation.node_props

            Added as-is into NetworkX graph, and you may filter operations by
            :meth:`.NetworkOperation.withset()`.
            Also plot-rendering affected if they match `Graphviz` properties,
            unless they start with underscore(``_``).
        """
        super().__init__()
        node_props = node_props = node_props if node_props else {}

        if not fn or not callable(fn):
            raise ValueError(f"Operation was not provided with a callable: {fn}")
        if parents and not isinstance(parents, tuple):
            raise ValueError(
                f"Operation `parents` must be tuple, was {type(parents).__name__!r}: {parents}"
            )
        if node_props is not None and not isinstance(node_props, cabc.Mapping):
            raise ValueError(
                f"Operation `node_props` must be a dict, was {type(node_props).__name__!r}: {node_props}"
            )

        ## Overwrite reparsed op-data.
        if name is None:
            name = func_name(fn, None, mod=0, fqdn=0, human=0)
        name = ".".join(str(pop) for pop in ((parents or ()) + (name,)))
        name, needs, provides = reparse_operation_data(name, needs, provides)
        fn_needs, op_needs = needs, needs
        fn_provides, op_provides = provides, provides
        # fn_needs, op_needs = _spread_sideffects(needs)
        # fn_provides, op_provides = _spread_sideffects(provides)

        if aliases:
            aliases = as_renames(aliases, "aliases")
            if any(1 for src, dst in aliases if dst in op_provides):
                bad = ", ".join(
                    f"{src} -> {dst}" for src, dst in aliases if dst in op_provides
                )
                raise ValueError(
                    f"The `aliases` ({bad}) clash with provides {list(op_provides)}!"
                )

            alias_src, alias_dst = list(zip(*aliases))
            if not set(alias_src) <= set(op_provides):
                raise ValueError(
                    f"The `aliases` for {list(alias_src)} rename {list(iset(alias_src) - op_provides)}"
                    f", not found in provides {list(op_provides)}!"
                )
            if any(isinstance(i, sideffect) for i in alias_src) or any(
                isinstance(i, sideffect) for i in alias_dst
            ):
                raise ValueError(
                    f"Operation `aliases` must not contain `sideffects`: {aliases}"
                    "\n  Simply add any extra `sideffects` in the `provides`."
                )
        else:
            op_provides = fn_provides

        self.fn = fn
        self.name = name
        self.needs = op_needs
        self.provides = op_provides
        self.fn_provides = fn_provides
        self.aliases = aliases
        self.parents = parents
        self.rescheduled = rescheduled
        self.endured = endured
        self.parallel = parallel
        self.marshalled = marshalled
        self.returns_dict = returns_dict
        self.node_props = node_props

    def __eq__(self, other):
        """Operation identity is based on `name` and `parents`."""
        return bool(
            self.name == getattr(other, "name", UNSET)
            and self.parents == getattr(other, "parents", UNSET)
        )

    def __hash__(self):
        """Operation identity is based on `name` and `parents`."""
        return hash(self.name) ^ hash(self.parents)

    def __repr__(self):
        """
        Display more informative names for the Operation class
        """
        needs = aslist(self.needs, "needs")
        provides = aslist(self.provides, "provides")
        aliases = aslist(self.aliases, "aliases")
        aliases = f", aliases={aliases!r}" if aliases else ""
        fn_name = self.fn and func_name(self.fn, None, mod=0, fqdn=0, human=0)
        returns_dict_marker = self.returns_dict and "{}" or ""
        nprops = f", x{len(self.node_props)}props" if self.node_props else ""
        resched = "?" if self.rescheduled else ""
        endured = "!" if self.endured else ""
        parallel = "|" if self.parallel else ""
        marshalled = "$" if self.marshalled else ""
        return (
            f"FunctionalOperation{endured}{resched}{parallel}{marshalled}(name={self.name!r}, needs={needs!r}, "
            f"provides={provides!r}{aliases}, fn{returns_dict_marker}={fn_name!r}{nprops})"
        )

    def withset(self, **kw) -> "FunctionalOperation":
        """
        Make a clone with the some values replaced.

        .. ATTENTION::
            Using :meth:`.namedtuple._replace()` would not pass through cstor,
            so would not get a nested `name` with `parents`, not arguments validation.
        """
        kw2 = vars(self).copy()
        kw2["provides"] = kw2.pop("fn_provides")
        kw2.update(kw)

        return FunctionalOperation(**kw2)

    def _prepare_plot_args_error(
        self,
        exceptions: List[Tuple[Any, Exception]],
        missing: List,
        varargs_bad: List,
        named_inputs: Mapping,
    ) -> ValueError:
        errors = [
            f"Need({n}) failed due to: {type(nex).__name__}({nex})"
            for n, nex in enumerate(exceptions, 1)
        ]
        ner = len(exceptions) + 1

        if missing:
            errors.append(f"{ner}. Missing compulsory needs{list(missing)}!")
            ner += 1
        if varargs_bad:
            errors.append(
                f"{ner}. Expected needs{list(varargs_bad)} to be non-str iterables!"
            )
        inputs = dict(named_inputs) if is_debug() else list(named_inputs)
        errors.append(f"+++inputs: {inputs}")
        errors.append(f"+++{self}")

        msg = textwrap.indent("\n".join(errors), " " * 4)
        raise MultiValueError(f"Failed preparing needs: \n{msg}", *exceptions)

    def _zip_results_with_provides(self, results, fn_expected: iset) -> dict:
        """Zip results with expected "real" (without sideffects) `provides`."""
        rescheduled = is_solid_true(is_reschedule_operations(), self.rescheduled)
        if not fn_expected:  # All provides were sideffects?
            if results and results != NO_RESULT:
                ## Do not scream,
                #  it is common to call a function for its sideffects,
                # which happens to return an irrelevant value.
                log.warning(
                    "Ignoring result(%s) because no `provides` given!\n  %s",
                    results,
                    self,
                )
            results = {}

        elif self.returns_dict:

            if hasattr(results, "_asdict"):  # named tuple
                results = results._asdict()
            elif isinstance(results, cabc.Mapping):
                pass
            elif hasattr(results, "__dict__"):  # regular object
                results = vars(results)
            else:
                raise ValueError(
                    "Expected results as mapping, named_tuple, object, "
                    f"got {type(results).__name__!r}: {results}\n  {self}"
                )

            res_names = results.keys()

            ## Allow unknown outs when dict,
            #  bc we can safely ignore them (and it's handy for reuse).
            #
            if res_names - fn_expected:
                unknown = list(res_names - fn_expected)
                log.info(
                    "Results%s contained +%s unknown provides%s\n  {self}",
                    list(res_names),
                    len(unknown),
                    list(unknown),
                )

            missmatched = fn_expected - res_names
            if missmatched:
                if rescheduled:
                    log.warning(
                        "... Op %r did not provide%s",
                        self.name,
                        list(fn_expected - res_names),
                    )
                else:
                    raise ValueError(
                        f"Got x{len(results)} results({list(results)}) mismatched "
                        f"-{len(missmatched)} provides({list(fn_expected)})!\n  {self}"
                    )

        else:  # Handle result sequence: no-result, single-item, many
            nexpected = len(fn_expected)

            if results == NO_RESULT:
                results = ()
                ngot = 0

            elif nexpected == 1:
                results = [results]
                ngot = 1

            else:  # nexpected > 1; nexpected == 0 was the very 1st check.
                if isinstance(results, (str, bytes)) or not isinstance(
                    results, cabc.Iterable
                ):
                    raise ValueError(
                        f"Expected x{nexpected} ITERABLE results, "
                        f"got {type(results).__name__!r}: {results}\n  {self}"
                    )
                ngot = len(results)

            if ngot < nexpected and not rescheduled:
                raise ValueError(
                    f"Got {ngot - nexpected} fewer results, while expected x{nexpected} "
                    f"provides({list(fn_expected)})!\n  {self}"
                )

            if ngot > nexpected:
                ## Less problematic if not expecting anything but got something
                #  (e.g reusing some function for sideffects).
                extra_results_loglevel = (
                    logging.INFO if nexpected == 0 else logging.WARNING
                )
                logging.log(
                    extra_results_loglevel,
                    "Got +%s more results, while expected "
                    "x%s provides%s\n  results: %s\n  %s",
                    ngot - nexpected,
                    nexpected,
                    list(fn_expected),
                    results,
                    self,
                )

            results = dict(zip(fn_expected, results))  # , fillvalue=UNSET))

        assert isinstance(
            results, cabc.Mapping
        ), f"Abnormal results type {type(results).__name__!r}: {results}!"

        if self.aliases:
            alias_values = [
                (dst, results[src]) for src, dst in self.aliases if src in results
            ]
            results.update(alias_values)

        return results

    def compute(self, named_inputs, outputs=None) -> dict:
        try:
            positional, vararg_vals = [], []
            kwargs = {}
            errors, missing, varargs_bad = [], [], []
            for n in self.needs:
                try:
                    if n not in named_inputs:
                        if not isinstance(n, (optional, vararg, varargs, sideffect)):
                            # It means `inputs` < compulsory `needs`.
                            # Compilation should have ensured all compulsories existed,
                            # but ..?
                            ##
                            missing.append(n)
                        continue

                    ## TODO: augment modifiers with "retrievers" from `inputs`.
                    inp_value = named_inputs[n]

                    if isinstance(n, kw):  # includes `optionals`
                        kwargs[n if n.fn_arg is None else n.fn_arg] = inp_value

                    elif isinstance(n, vararg):
                        vararg_vals.append(inp_value)

                    elif isinstance(n, varargs):
                        if isinstance(inp_value, str) or not isinstance(
                            inp_value, cabc.Iterable
                        ):
                            varargs_bad.append(n)
                        else:
                            vararg_vals.extend(i for i in inp_value)

                    elif isinstance(n, sideffect):
                        pass  # ignored as function argument.

                    else:
                        positional.append(named_inputs[n])

                except Exception as nex:
                    log.debug(
                        "Cannot prepare op(%s) need(%s) due to: %s",
                        self.name,
                        n,
                        nex,
                        exc_info=nex,
                    )
                    errors.append((n, nex))

            if errors or missing or varargs_bad:
                raise self._prepare_plot_args_error(
                    errors, missing, varargs_bad, named_inputs
                )

            results_fn = self.fn(*positional, *vararg_vals, **kwargs)

            # TODO: rename op jetsam (real_)provides --> fn_expected
            provides = iset(n for n in self.fn_provides if not isinstance(n, sideffect))
            results_op = self._zip_results_with_provides(results_fn, provides)

            if outputs:
                outputs = set(n for n in outputs if not isinstance(n, sideffect))
                # Ignore sideffect outputs.
                results_op = {
                    key: val for key, val in results_op.items() if key in outputs
                }

            return results_op
        except Exception as ex:
            jetsam(
                ex,
                locals(),
                "outputs",
                "aliases",
                "provides",
                "results_fn",
                "results_op",
                operation="self",
                args=lambda locs: {
                    "positional": locs.get("positional"),
                    "varargs": locs.get("vararg_vals"),
                    "kwargs": locs.get("kwargs"),
                },
            )
            raise

    def __call__(self, *args, **kwargs):
        return self.fn(*args, **kwargs)


class operation:
    """
    A builder for graph-operations wrapping functions.

    :param fn:
        The callable underlying this operation.  This does not need to be
        specified when the operation object is instantiated and can instead
        be set via ``__call__`` later.
    :param str name:
        The name of the operation in the computation graph.
    :param needs:
        The list of (positionally ordered) names of the data needed by the `operation`
        to receive as :term:`inputs`, roughly corresponding to the arguments of
        the underlying `fn`.

        See also :term:`needs` & :term:`modifiers`.
    :param provides:
        Names of output data this operation provides, which must correspond
        to the returned values of the `fn`.
        If more than one given, those must be returned in an iterable,
        unless `returns_dict` is true, in which case a dictionary with (at least)
        as many elements must be returned.

        See also :term:`provides` & :term:`modifiers`.
    :param aliases:
        an optional mapping of `provides` to additional ones
    :param rescheduled:
        If true, underlying *callable* may produce a subset of `provides`,
        and the :term:`plan` must then :term:`reschedule` after the operation
        has executed.  In that case, it makes more sense for the *callable*
        to `returns_dict`.
    :param endured:
        If true, even if *callable* fails, solution will :term:`reschedule`.
        ignored if :term:`endurance` enabled globally.
    :param parallel:
        execute in :term:`parallel`
    :param marshalled:
        If true, operation will be :term:`marshalled <marshalling>` while computed,        along with its `inputs` & `outputs`.
        (usefull when run in `parallel` with a :term:`process pool`).
    :param returns_dict:
        if true, it means the `fn` :term:`returns dictionary` with all `provides`,
        and no further processing is done on them
        (i.e. the returned output-values are not zipped with `provides`)
    :param node_props:
        Added as-is into NetworkX graph, and you may filter operations by
        :meth:`.NetworkOperation.withset()`.
        Also plot-rendering affected if they match `Graphviz` properties.,
        unless they start with underscore(``_``)

    :return:
        when called, it returns a :class:`.FunctionalOperation`

    **Example:**

    This is an example of its use, based on the "builder pattern":

        >>> from graphtik import operation

        >>> opb = operation(name='add_op')
        >>> opb.withset(needs=['a', 'b'])
        operation(name='add_op', needs=['a', 'b'], provides=[], fn=None)
        >>> opb.withset(provides='SUM', fn=sum)
        operation(name='add_op', needs=['a', 'b'], provides=['SUM'], fn='sum')

    You may keep calling ``withset()`` till you invoke a final ``__call__()``
    on the builder;  then you get the actual :class:`.FunctionalOperation` instance:

        >>> # Create `Operation` and overwrite function at the last moment.
        >>> opb(sum)
        FunctionalOperation(name='add_op', needs=['a', 'b'], provides=['SUM'], fn='sum')

    .. Tip::
        Remember to call once more the builder class at the end, to get the actual
        operation instance.

    """

    def __init__(
        self,
        fn: Callable = None,
        *,
        name=None,
        needs: Items = None,
        provides: Items = None,
        aliases: Mapping = None,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        returns_dict=None,
        node_props: Mapping = None,
    ):
        kw = _dict_without(locals(), "self")
        vars(self).update(kw)
        # To check `fn` callable.
        self.withset(**kw)

    def withset(
        self,
        *,
        fn: Callable = None,
        name=None,
        needs: Items = None,
        provides: Items = None,
        aliases: Mapping = None,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        returns_dict=None,
        node_props: Mapping = None,
    ) -> "operation":
        """See :class:`operation` for arguments here."""
        if fn is not None:
            if not callable(fn):
                raise ValueError(
                    f"`fn` arg must be callable, was {type(fn).__name__}!"
                    f"\n  did you mean? operation(name={fn}, ..."
                )
            self.fn = fn
        if name is not None:
            self.name = name
        if needs is not None:
            self.needs = needs
        if provides is not None:
            self.provides = provides
        if aliases is not None:
            self.aliases = aliases
        if rescheduled is not None:
            self.rescheduled = rescheduled
        if endured is not None:
            self.endured = endured
        if parallel is not None:
            self.parallel = parallel
        if marshalled is not None:
            self.marshalled = marshalled
        if returns_dict is not None:
            self.returns_dict = returns_dict
        if node_props is not None:
            self.node_props = node_props

        return self

    def __call__(
        self,
        fn: Callable = None,
        *,
        name=None,
        needs: Items = None,
        provides: Items = None,
        aliases: Mapping = None,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        returns_dict=None,
        node_props: Mapping = None,
    ) -> FunctionalOperation:
        """
        This enables ``operation`` to act as a decorator or as a functional
        operation, for example::

            @operator(name='myadd1', needs=['a', 'b'], provides=['c'])
            def myadd(a, b):
                return a + b

        or::

            def myadd(a, b):
                return a + b
            operator(name='myadd1', needs=['a', 'b'], provides=['c'])(myadd)

        :param fn:
            The function to be used by this ``operation``.

        :return:
            Returns an operation class that can be called as a function or
            composed into a computation graph.
        """
        self.withset(**_dict_without(locals(), "self"))

        return FunctionalOperation(**vars(self))

    def __repr__(self):
        """
        Display more informative names for the Operation class
        """
        needs = aslist(self.needs, "needs")
        provides = aslist(self.provides, "provides")
        aliases = aslist(self.aliases, "aliases")
        aliases = f", aliases={aliases!r}" if aliases else ""
        fn_name = self.fn and func_name(self.fn, None, mod=0, fqdn=0, human=0)
        nprops = f", x{len(self.node_props)}props" if self.node_props else ""
        resched = "?" if self.rescheduled else ""
        endured = "!" if self.endured else ""
        parallel = "|" if self.parallel else ""
        marshalled = "$" if self.marshalled else ""
        return (
            f"operation{endured}{resched}{parallel}{marshalled}(name={self.name!r}, needs={needs!r}, "
            f"provides={provides!r}{aliases}, fn={fn_name!r}{nprops})"
        )
