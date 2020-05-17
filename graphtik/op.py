# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
Define :term:`operation` & :term:`dependency` and match/zip inputs/outputs during :term:`execution`.

.. note::
    This module (along with :mod:`.modifiers` & :mod:`.pipeline`) is what client code needs
    to define pipelines *on import time* without incurring a heavy price
    (<5ms on a 2019 fast PC)
"""

import itertools as itt
import logging
import textwrap
from collections import abc as cabc
from functools import wraps
from typing import (
    Any,
    Callable,
    Collection,
    Hashable,
    List,
    Mapping,
    Tuple,
)

from boltons.setutils import IndexedSet as iset

from .base import (
    UNSET,
    Items,
    MultiValueError,
    Operation,
    Plottable,
    PlotArgs,
    Token,
    aslist,
    astuple,
    first_solid,
    func_name,
    jetsam,
)
from .modifiers import (
    dep_renamed,
    dep_singularized,
    dep_stripped,
    get_accessor,
    get_keyword,
    is_optional,
    is_pure_sfx,
    is_sfx,
    is_sfxed,
    is_vararg,
    is_varargs,
    optional,
)

log = logging.getLogger(__name__)

#: A special return value for the function of a :term:`reschedule` operation
#: signifying that it did not produce any result at all (including :term:`sideffects`),
#: otherwise, it would have been a single result, ``None``.
#: Usefull for rescheduled who want to cancel their single result
#: witout being delcared as :term:`returns dictionary`.
NO_RESULT = Token("NO_RESULT")
#: Like :data:`NO_RESULT` but does not cancel any :term;`sideffects`
#: declared as provides.
NO_RESULT_BUT_SFX = Token("NO_RESULT_BUT_SFX")


def as_renames(i, argname):
    """
    Parses a list of (source-->destination) from dict, list-of-2-items, single 2-tuple.

    :return:
        a (possibly empty)list-of-pairs

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
        raise TypeError(
            f"Argument {argname} must be a list of 2-element items, was: {i!r}"
        ) from None
    elif not is_list_of_2(i):
        try:
            i = list(dict(i).items())
        except Exception as ex:
            raise ValueError(f"Cannot dict-ize {argname}({i!r}) due to: {ex}") from None

    return i


def reparse_operation_data(
    name, needs, provides, aliases=()
) -> Tuple[
    Hashable, Collection[str], Collection[str], Collection[Tuple[str, str]],
]:
    """
    Validate & reparse operation data as lists.

    :return:
        name, needs, provides, aliases

    As a separate function to be reused by client building operations,
    to detect errors early.
    """
    # if name is not None and not name or not isinstance(name, cabc.Hashable):
    #     raise TypeError(f"Operation `name` must be a truthy hashable, got: {name}")
    if not isinstance(name, cabc.Hashable):
        raise TypeError(f"Operation `name` must be hashable, got: {name}")

    # Allow single string-value for needs parameter
    needs = astuple(needs, "needs", allowed_types=cabc.Collection)
    if not all(isinstance(i, str) for i in needs):
        raise TypeError(f"All `needs` must be str, got: {needs!r}")

    # Allow single value for provides parameter
    provides = astuple(provides, "provides", allowed_types=cabc.Collection)
    if not all(isinstance(i, str) for i in provides):
        raise TypeError(f"All `provides` must be str, got: {provides!r}")

    aliases = as_renames(aliases, "aliases")
    if aliases:
        if not all(
            src and isinstance(src, str) and dst and isinstance(dst, str)
            for src, dst in aliases
        ):
            raise TypeError(f"All `aliases` must be non-empty str, got: {aliases!r}")
        if any(1 for src, dst in aliases if dst in provides):
            bad = ", ".join(
                f"{src} -> {dst}" for src, dst in aliases if dst in provides
            )
            raise ValueError(
                f"The `aliases` [{bad}] clash with existing provides in {list(provides)}!"
            )

        alias_src = iset(src for src, _dst in aliases)
        if not alias_src <= set(provides):
            bad_alias_sources = alias_src - provides
            bad_aliases = ", ".join(
                f"{src!r}-->{dst!r}" for src, dst in aliases if src in bad_alias_sources
            )
            raise ValueError(
                f"The `aliases` [{bad_aliases}] rename non-existent provides in {list(provides)}!"
            )
        sfx_aliases = [
            f"{src} -> {dst}" for src, dst in aliases if is_sfx(src) or is_sfx(dst)
        ]
        if sfx_aliases:
            raise ValueError(
                f"The `aliases` must not contain `sideffects` {sfx_aliases}"
                "\n  Simply add any extra `sideffects` in the `provides`."
            )

    return name, needs, provides, aliases


def _spread_sideffects(
    deps: Collection[str],
) -> Tuple[Collection[str], Collection[str]]:
    """
    Build fn/op dependencies from user ones by stripping or singularizing any :term:`sideffects`.

    :return:
        the given `deps` duplicated as ``(fn_deps,  op_deps)``, where any instances of
        :term:`sideffects` are processed like this:

        `fn_deps`
            - any :func:`.sfxed` are replaced by the :func:`stripped <.dep_stripped>`
              dependency consumed/produced by underlying functions, in the order
              they are first met (the rest duplicate `sideffected` are discarded).
            - any :func:`.sfx` are simply dropped;

        `op_deps`
            any :func:`.sfxed` are replaced by a sequence of ":func:`singularized
            <.dep_singularized>`" instances, one for each item in their
            :attr:`._Modifier.sfx_list` attribute, in the order they are first met
            (any duplicates are discarded, order is irrelevant, since they don't reach
            the function);
    """

    #: The dedupe  any `sideffected`.
    seen_sideffecteds = set()

    def strip_sideffecteds(dep):
        """Strip and dedupe any sfxed, drop any sfx. """
        if is_sfxed(dep):
            dep = dep_stripped(dep)
            if not dep in seen_sideffecteds:
                seen_sideffecteds.add(dep)
                return (dep,)
        elif not is_sfx(dep):
            return (dep,)
        return ()

    assert deps is not None

    if deps:
        deps = tuple(nn for n in deps for nn in dep_singularized(n))
        fn_deps = tuple(nn for n in deps for nn in strip_sideffecteds(n))
        return deps, fn_deps
    else:
        return deps, deps


class FunctionalOperation(Operation):
    """
    An :term:`operation` performing a callable (ie a function, a method, a lambda).

    .. Tip::
        - Use :func:`.operation()` factory to build instances of this class instead.
        - Call :meth:`withset()` on existing instances to re-configure new clones.
        - See :term:`diacritic`\\s to understand printouts of this class.

    .. dep-attributes-start

    Differences between various dependency operation attributes:

    +--------------------------+-------+-------+------+-------+
    |   dependency attribute   |DUPES  |SFX    |SFXED |ALIASES|
    +==========+===============+=======+=======+======+=======+
    |          |**needs**      ||check|||check||SINGLE|       |
    +          +---------------+-------+-------+------+       |
    | *needs*  |**op_needs**   ||cross|||check||SINGLE|       |
    +          +---------------+-------+-------+------+       |
    |          |**fn_needs**   ||check|||cross||STRIP |       |
    +----------+---------------+-------+-------+------+-------+
    |          |**provides**   ||check|||check||SINGLE||cross||
    +          +---------------+-------+-------+------+-------+
    |*provides*|**op_provides**||cross|||check||SINGLE||check||
    +          +---------------+-------+-------+------+-------+
    |          |**fn_provides**||check|||cross||STRIP ||cross||
    +----------+---------------+-------+-------+------+-------+

    ..
    .. dep-attributes-end
    """

    def __init__(
        self,
        fn: Callable = None,
        name=None,
        needs: Items = None,
        provides: Items = None,
        aliases: Mapping = None,
        *,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        returns_dict=None,
        node_props: Mapping = None,
    ):
        """
        Build a new operation out of some function and its requirements.

        See :func:`.operation` for the full documentation of parameters,
        study the code for attributes (or read them from  rendered sphinx site).
        """
        super().__init__()
        node_props = node_props = node_props if node_props else {}

        if fn and not callable(fn):
            raise TypeError(f"Operation was not provided with a callable: {fn}")
        if node_props is not None and not isinstance(node_props, cabc.Mapping):
            raise TypeError(
                f"Operation `node_props` must be a dict, was {type(node_props).__name__!r}: {node_props}"
            )

        if name is None and fn:
            name = func_name(fn, None, mod=0, fqdn=0, human=0, partials=1)
        ## Overwrite reparsed op-data.
        name, needs, provides, aliases = reparse_operation_data(
            name, needs, provides, aliases
        )

        needs, _fn_needs = _spread_sideffects(needs)
        provides, _fn_provides = _spread_sideffects(provides)
        op_needs = iset(needs)
        alias_dst = aliases and tuple(dst for _src, dst in aliases)
        op_provides = iset(itt.chain(provides, alias_dst))

        #: The :term:`operation`'s underlying function.
        self.fn = fn
        #: a name for the operation (e.g. `'conv1'`, `'sum'`, etc..);
        #: any "parents split by dots(``.``)".
        #: :seealso: :ref:`operation-nesting`
        self.name = name

        #: The :term:`needs` almost as given by the user
        #: (which may contain MULTI-sideffecteds and dupes),
        #: roughly morphed into `_fn_provides` + sideffects
        #: (DUPES, SFX, SINGULARIZED :term:`sideffected`\s).
        #: It is stored for builder functionality to work.
        self.needs = needs
        #: Value names ready to lay the graph for :term:`pruning`
        #: (NO-DUPES, SFX, SINGULAR :term:`sideffected`\s).
        self.op_needs = op_needs
        #: Value names the underlying function requires
        #: (DUPES preserved, NO-SFX, STRIPPED :term:`sideffected`).
        self._fn_needs = _fn_needs

        #: The :term:`provides` almost as given by the user
        #: (which may contain MULTI-sideffecteds and dupes),
        #: roughly morphed into `_fn_provides` + sideffects
        #: (DUPES, NO-ALIASES, SFX, SINGULAR :term:`sideffected`\s).
        #: It is stored for builder functionality to work.
        self.provides = provides
        #: Value names ready to lay the graph for :term:`pruning`
        #: (NO DUPES, ALIASES, SFX, SINGULAR sideffecteds).
        self.op_provides = op_provides
        #: Value names the underlying function produces
        #: (DUPES, NO-ALIASES, NO_SFX, STRIPPED :term:`sideffected`).
        self._fn_provides = _fn_provides
        #: an optional mapping of `fn_provides` to additional ones, together
        #: comprising this operations :term:`op_provides`.
        #:
        #: You cannot alias an :term:`alias`.
        self.aliases = aliases
        #: If true, underlying *callable* may produce a subset of `provides`,
        #: and the :term:`plan` must then :term:`reschedule` after the operation
        #: has executed.  In that case, it makes more sense for the *callable*
        #: to `returns_dict`.
        self.rescheduled = rescheduled
        #: If true, even if *callable* fails, solution will :term:`reschedule`;
        #: ignored if :term:`endurance` enabled globally.
        self.endured = endured
        #: execute in :term:`parallel`
        self.parallel = parallel
        #: If true, operation will be :term:`marshalled <marshalling>` while computed,
        #: along with its `inputs` & `outputs`.
        #: (usefull when run in `parallel` with a :term:`process pool`).
        self.marshalled = marshalled
        #: If true, it means the underlying function :term:`returns dictionary` ,
        #: and no further processing is done on its results,
        #: i.e. the returned output-values are not zipped with `provides`.
        #:
        #: It does not have to return any :term:`alias` `outputs`.
        #:
        #: Can be changed amidst execution by the operation's function,
        #: but it is easier for that function to simply call :meth:`.set_results_by_name()`.
        self.returns_dict = returns_dict
        #: Added as-is into NetworkX graph, and you may filter operations by
        #: :meth:`.Pipeline.withset()`.
        #: Also plot-rendering affected if they match `Graphviz` properties,
        #: unless they start with underscore(``_``).
        self.node_props = node_props

    def __eq__(self, other):
        """Operation identity is based on `name`."""
        return bool(self.name == getattr(other, "name", UNSET))

    def __hash__(self):
        """Operation identity is based on `name`."""
        return hash(self.name)

    def __repr__(self):
        """
        Display operation & dependency names annotated with :term:`diacritic`\\s.
        """
        from .config import (
            is_debug,
            is_endure_operations,
            is_marshal_tasks,
            is_parallel_tasks,
            is_reschedule_operations,
            reset_abort,
        )

        dep_names = (
            "needs op_needs _fn_needs provides op_provides _fn_provides aliases"
            if is_debug()
            else "needs provides aliases"
        ).split()
        deps = [(i, getattr(self, i)) for i in dep_names]
        fn_name = self.fn and func_name(self.fn, None, mod=0, fqdn=0, human=0)
        returns_dict_marker = self.returns_dict and "{}" or ""
        items = [
            f"name={self.name!r}",
            *(f"{n}={aslist(d, n)}" for n, d in deps if d),
            f"fn{returns_dict_marker}={fn_name!r}",
        ]
        if self.node_props:
            items.append(f"x{len(self.node_props)}props")

        resched = (
            "?" if first_solid(self.rescheduled, is_reschedule_operations()) else ""
        )
        endured = "!" if first_solid(self.endured, is_endure_operations()) else ""
        parallel = "|" if first_solid(self.parallel, is_parallel_tasks()) else ""
        marshalled = "&" if first_solid(self.marshalled, is_marshal_tasks()) else ""

        return f"FunctionalOperation{endured}{resched}{parallel}{marshalled}({', '.join(items)})"

    @property
    def deps(self) -> Mapping[str, Collection]:
        """
        All :term:`dependency` names, including `op_` & internal `_fn_`.

        if not DEBUG, all deps are converted into lists, ready to be printed.
        """
        from .config import is_debug

        return {
            k: v if is_debug() else list(v)
            for k, v in zip(
                "needs op_needs fn_needs provides op_provides fn_provides".split(),
                (
                    self.needs,
                    self.op_needs,
                    self._fn_needs,
                    self.provides,
                    self.op_provides,
                    self._fn_provides,
                ),
            )
        }

    def withset(
        self,
        fn: Callable = ...,
        name=...,
        needs: Items = ...,
        provides: Items = ...,
        aliases: Mapping = ...,
        *,
        rescheduled=...,
        endured=...,
        parallel=...,
        marshalled=...,
        returns_dict=...,
        node_props: Mapping = ...,
        renamer=None,
    ) -> "FunctionalOperation":
        """
        Make a *clone* with the some values replaced, or operation and dependencies renamed.

        if `renamer` given, it is applied on top (and afterwards) any other changed
        values, for operation-name, needs, provides & any aliases.

        :param renamer:
            - if a dictionary, it renames any operations & data named as keys
              into the respective values by feeding them into :func:.dep_renamed()`,
              so values may be single-input callables themselves.
            - if it is a :func:`.callable`, it is given a :class:`.RenArgs` instance
              to decide the node's name.

            The callable may return a *str* for the new-name, or any other false
            value to leave node named as is.

            .. Attention::
                The callable SHOULD wish to preserve any :term:`modifier` on dependencies,
                and use :func:`.dep_renamed()` if a callable is given.

        :return:
            a clone operation with changed/renamed values asked

        :raise:
            - (ValueError, TypeError): all cstor validation errors
            - ValueError: if a `renamer` dict contains a non-string and non-callable
              value


        **Examples**

            >>> from graphtik import operation, sfx

            >>> op = operation(str, "foo", needs="a",
            ...     provides=["b", sfx("c")],
            ...     aliases={"b": "B-aliased"})
            >>> op.withset(renamer={"foo": "BAR",
            ...                     'a': "A",
            ...                     'b': "B",
            ...                     sfx('c'): "cc",
            ...                     "B-aliased": "new.B-aliased"})
            FunctionalOperation(name='BAR',
                                needs=['A'],
                                provides=['B', sfx('cc')],
                                aliases=[('B', 'new.B-aliased')],
                                fn='str')

        - Notice that ``'c'`` rename change the "sideffect name, without the destination name
          being an ``sfx()`` modifier (but source name must match the sfx-specifier).
        - Notice that the source of aliases from ``b-->B`` is handled implicitely
          from the respective rename on the `provides`.

        But usually a callable is more practical, like the one below renaming
        only data names:

            >>> op.withset(renamer=lambda ren_args:
            ...            dep_renamed(ren_args.name, lambda n: f"parent.{n}")
            ...            if ren_args.typ != 'op' else
            ...            False)
            FunctionalOperation(name='foo',
                                needs=['parent.a'],
                                provides=['parent.b', sfx('parent.c')],
                                aliases=[('parent.b', 'parent.B-aliased')],
                                fn='str')


        Notice the double use of lambdas with :func:`dep_renamed()` -- an equivalent
        rename callback would be::

            dep_renamed(ren_args.name, f"parent.{dependency(ren_args.name)}")
        """
        kw = {
            k: v
            for k, v in locals().items()
            if v is not ... and k not in "self renamer".split()
        }
        ## Exclude calculated dep-fields.
        #
        me = {
            k: v
            for k, v in vars(self).items()
            if not k.startswith("_") and not k.startswith("op_")
        }
        kw = {**me, **kw}

        if renamer:
            self._rename_graph_names(kw, renamer)

        return FunctionalOperation(**kw)

    def _prepare_match_inputs_error(
        self,
        exceptions: List[Tuple[Any, Exception]],
        missing: List,
        varargs_bad: List,
        named_inputs: Mapping,
    ) -> ValueError:
        from .config import is_debug

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
        errors.append("(tip: enabled debug log for stack traces)")

        msg = textwrap.indent("\n".join(errors), " " * 4)
        raise MultiValueError(f"Failed preparing needs: \n{msg}", *exceptions)

    def _match_inputs_with_fn_needs(self, named_inputs) -> Tuple[list, list, dict]:
        positional, vararg_vals = [], []
        kwargs = {}
        errors, missing, varargs_bad = [], [], []
        for n in self._fn_needs:
            assert not is_sfx(n), locals()
            try:
                acc = get_accessor(n)
                if acc:
                    inp_value = acc.get(named_inputs, n)
                elif n not in named_inputs:
                    if not is_optional(n) or is_sfx(n):
                        # It means `inputs` < compulsory `needs`.
                        # Compilation should have ensured all compulsories existed,
                        # but ..?
                        ##
                        missing.append(n)
                    continue
                else:
                    inp_value = named_inputs[n]

                if get_keyword(n):
                    kwargs[n.keyword] = inp_value

                elif is_vararg(n):
                    vararg_vals.append(inp_value)

                elif is_varargs(n):
                    if isinstance(inp_value, str) or not isinstance(
                        inp_value, cabc.Iterable
                    ):
                        varargs_bad.append(n)
                    else:
                        vararg_vals.extend(i for i in inp_value)

                else:
                    positional.append(inp_value)

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
            raise self._prepare_match_inputs_error(
                errors, missing, varargs_bad, named_inputs
            )

        return positional, vararg_vals, kwargs

    def _zip_results_with_provides(self, results) -> dict:
        """Zip results with expected "real" (without sideffects) `provides`."""
        from .config import is_reschedule_operations

        fn_expected: iset = self._fn_provides
        rescheduled = first_solid(is_reschedule_operations(), self.rescheduled)
        if self.returns_dict:

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

            if rescheduled:
                fn_required = fn_expected
                # Canceled sfx are welcomed.
                fn_expected = iset(self.provides)
            else:
                fn_required = fn_expected

            res_names = results.keys()

            ## Allow unknown outs when dict,
            #  bc we can safely ignore them (and it's handy for reuse).
            #
            unknown = res_names - fn_expected
            if unknown:
                unknown = list(unknown)
                log.info(
                    "Results%s contained +%s unknown provides%s\n  %s",
                    list(res_names),
                    len(unknown),
                    list(unknown),
                    self,
                )

            missmatched = fn_required - res_names
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

        elif results in (NO_RESULT, NO_RESULT_BUT_SFX) and rescheduled:
            results = (
                {}
                if results == NO_RESULT_BUT_SFX
                # Cancel also any SFX.
                else {p: False for p in set(self.provides) if is_sfx(p)}
            )

        elif not fn_expected:  # All provides were sideffects?
            if results and results not in (NO_RESULT, NO_RESULT_BUT_SFX):
                ## Do not scream,
                #  it is common to call a function for its sideffects,
                # which happens to return an irrelevant value.
                log.warning(
                    "Ignoring result(%s) because no `provides` given!\n  %s",
                    results,
                    self,
                )
            results = {}

        else:  # Handle result sequence: no-result, single-item, many
            nexpected = len(fn_expected)

            if results in (NO_RESULT, NO_RESULT_BUT_SFX):
                results = ()
                ngot = 0

            elif nexpected == 1:
                results = [results]
                ngot = 1

            else:
                # nexpected == 0 was method's 1st check.
                assert nexpected > 1, nexpected
                if isinstance(results, (str, bytes)) or not isinstance(
                    results, cabc.Iterable
                ):
                    raise TypeError(
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

    def compute(self, named_inputs=None, outputs: Items = None) -> dict:
        try:
            if self.fn is None or not self.name:
                raise ValueError(
                    f"Operation must have a callable `fn` and a non-empty `name`:\n  {self}"
                )
            assert self.name is not None, self
            if named_inputs is None:
                named_inputs = {}

            positional, varargs, kwargs = self._match_inputs_with_fn_needs(named_inputs)
            results_fn = self.fn(*positional, *varargs, **kwargs)
            results_op = self._zip_results_with_provides(results_fn)

            outputs = astuple(outputs, "outputs", allowed_types=cabc.Collection)

            ## Keep only outputs asked.
            #  Note that plan's executors do not ask outputs
            #  (see `_OpTask.__call__`).
            #
            if outputs:
                outputs = set(n for n in outputs)
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
                "results_fn",
                "results_op",
                operation="self",
                args=lambda locs: {
                    "positional": locs.get("positional"),
                    "varargs": locs.get("varargs"),
                    "kwargs": locs.get("kwargs"),
                },
            )
            raise

    def __call__(self, *args, **kwargs):
        """Like dict args, delegates to :meth:`.compute()`."""
        return self.compute(dict(*args, **kwargs))

    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        """Delegate to a provisional network with a single op . """
        from .pipeline import compose
        from .plot import graphviz_html_string

        is_user_label = bool(plot_args.graph and plot_args.graph.get("graphviz.label"))
        plottable = compose(self.name, self)
        plot_args = plot_args.with_defaults(name=self.name)
        plot_args = plottable.prepare_plot_args(plot_args)
        assert plot_args.graph, plot_args

        ## Operations don't need another name visible.
        #
        if is_user_label:
            del plot_args.graph.graph["graphviz.label"]
        plot_args = plot_args._replace(plottable=self)

        return plot_args


def operation(
    fn: Callable = None,
    name=None,
    needs: Items = None,
    provides: Items = None,
    aliases: Mapping = None,
    *,
    rescheduled=None,
    endured=None,
    parallel=None,
    marshalled=None,
    returns_dict=None,
    node_props: Mapping = None,
):
    r"""
    An :term:`operation` factory that can function as a decorator.

    :param fn:
        The callable underlying this operation.
        If given, it builds the operation right away (along with any other arguments).

        If not given, it returns a "fancy decorator" that still supports all arguments
        here AND the ``withset()`` method.

        .. hint::
            This is a twisted way for `"fancy decorators"
            <https://realpython.com/primer-on-python-decorators/#both-please-but-never-mind-the-bread>`_.

        After all that, you can always call :meth:`FunctionalOperation.withset()`
        on existing operation, to obtain a re-configured clone.
    :param str name:
        The name of the operation in the computation graph.
        If not given, deduce from any `fn` given.

    :param needs:
        the list of (positionally ordered) names of the data needed by the `operation`
        to receive as :term:`inputs`, roughly corresponding to the arguments of
        the underlying `fn` (plus any :term:`sideffects`).

        It can be a single string, in which case a 1-element iterable is assumed.

        .. seealso::
            - :term:`needs`
            - :term:`modifier`
            - :attr:`.FunctionalOperation.needs`
            - :attr:`.FunctionalOperation.op_needs`
            - :attr:`.FunctionalOperation._fn_needs`


    :param provides:
        the list of (positionally ordered) output data this operation provides,
        which must, roughly, correspond to the returned values of the `fn`
        (plus any :term:`sideffects` & :term:`alias`\es).

        It can be a single string, in which case a 1-element iterable is assumed.

        If they are more than one, the underlying function must return an iterable
        with same number of elements, unless param `returns_dict` :term:`is true
        <returns dictionary>`, in which case must return a dictionary that containing
        (at least) those named elements.

        .. seealso::
            - :term:`provides`
            - :term:`modifier`
            - :attr:`.FunctionalOperation.provides`
            - :attr:`.FunctionalOperation.op_provides`
            - :attr:`.FunctionalOperation._fn_provides`

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
        :meth:`.Pipeline.withset()`.
        Also plot-rendering affected if they match `Graphviz` properties.,
        unless they start with underscore(``_``)

    :return:
        when called with `fn`, it returns a :class:`.FunctionalOperation`,
        otherwise it returns a decorator function that accepts `fn` as the 1st argument.

        .. Note::
            Actually the returned decorator is the :meth:`.FunctionalOperation.withset()`
            method and accepts all arguments, monkeypatched to support calling a virtual
            ``withset()`` method on it, not to interrupt the builder-pattern,
            but only that - besides that trick, it is just a bound method.

    **Example:**

    This is an example of its use, based on the "builder pattern":

        >>> from graphtik import operation, varargs

        >>> op = operation()
        >>> op
        <function FunctionalOperation.withset at ...

    That's a "fancy decorator".

        >>> op = op.withset(needs=['a', 'b'])
        >>> op
        FunctionalOperation(name=None, needs=['a', 'b'], fn=None)

    If you call an operation with `fn` un-initialized, it will scream:

        >>> op.compute({"a":1, "b": 2})
        Traceback (most recent call last):
        ValueError: Operation must have a callable `fn` and a non-empty `name`:
          FunctionalOperation(name=None, needs=['a', 'b'], fn=None)

    You may keep calling ``withset()`` until a valid operation instance is returned,
    and compute it:

        >>> op = op.withset(needs=['a', 'b'],
        ...                 provides='SUM', fn=lambda a, b: a + b)
        >>> op
        FunctionalOperation(name='<lambda>', needs=['a', 'b'], provides=['SUM'], fn='<lambda>')
        >>> op.compute({"a":1, "b": 2})
        {'SUM': 3}

        >>> op.withset(fn=lambda a, b: a * b).compute({'a': 2, 'b': 5})
        {'SUM': 10}
    """
    kw = {k: v for k, v in locals().items() if v is not None and k != "self"}
    op = FunctionalOperation(**kw)

    if "fn" in kw:
        # Either used as a "naked" decorator (without any arguments)
        # or not used as decorator at all (manually called and passed in `fn`).
        # Don't bother returning a decorator.
        return op

    @wraps(op.withset)
    def decorator(*args, **kw):
        return op.withset(*args, **kw)

    # Dress decorator to support the builder-pattern.
    # even when called as regular function (without `@`) without `fn`.
    decorator.withset = op.withset

    return decorator
