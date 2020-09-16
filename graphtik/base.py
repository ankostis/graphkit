# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Generic utilities, exceptions and :term:`operation` & :term:`plottable` base classes.

.. doctest::
    :hide:

    .. Workaround sphinx-doc/sphinx#6590

    >>> from graphtik.base import *
    >>> __name__ = "graphtik.base"
"""

import abc
import inspect
import logging
from collections import abc as cabc
from functools import partial, partialmethod, wraps
from typing import (
    Any,
    Callable,
    Collection,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Tuple,
    Union,
)

Items = Union[Collection, str, None]

log = logging.getLogger(__name__)


class AbortedException(Exception):
    """
    Raised from Network when :func:`.abort_run()` is called, and contains the solution ...

    with any values populated so far.
    """


class IncompleteExecutionError(Exception):
    """
    Reported when any :term:`endured`/:term:`reschedule` operations were are canceled.

    The exception contains 3 arguments:

    1. the causal errors and conditions (1st arg),
    2. the list of collected exceptions (2nd arg), and
    3. the solution instance (3rd argument), to interrogate for more.

    Returned by :meth:`.check_if_incomplete()` or raised by :meth:`.scream_if_incomplete()`.
    """

    def __str__(self):
        return self.args[0]  # pylint: disable=unsubscriptable-object


class Token(str):
    """Guarantee equality, not(!) identity, across processes."""

    __slots__ = ("hashid",)

    def __new__(cls, s):
        return super().__new__(cls, f"<{s}>")

    def __init__(self, *args):
        import random

        self.hashid = random.randint(-(2 ** 32), 2 ** 32 - 1)

    def __eq__(self, other):
        return self.hashid == getattr(other, "hashid", None)

    def __hash__(self):
        return self.hashid

    def __getstate__(self):
        return self.hashid

    def __setstate__(self, state):
        self.hashid = state

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __bool__(self):
        """Always `True`, even if empty string."""
        return True

    def __repr__(self):
        """Avoid 'ticks' around repr."""
        return self.__str__()


UNSET = Token("UNSET")

debug_var_tip = "(tip: set GRAPHTIK_DEBUG envvar to view Op details in print-outs)"


def first_solid(*tristates, default=None):
    """Utility combining multiple tri-state booleans."""
    from boltons.iterutils import first

    return first(tristates, default=default, key=lambda i: i is not None)


def aslist(i, argname, allowed_types=list):
    """Utility to accept singular strings as lists, and None --> []."""
    if not i:
        return i if isinstance(i, allowed_types) else []

    if isinstance(i, str):
        i = [i]
    elif not isinstance(i, allowed_types):
        try:
            i = list(i)
        except Exception as ex:
            raise ValueError(f"Cannot list-ize {argname}({i!r}) due to: {ex}") from None

    return i


def astuple(i, argname, allowed_types=tuple):
    if not i:
        return i if isinstance(i, allowed_types) else ()

    if isinstance(i, str):
        i = (i,)
    elif not isinstance(i, allowed_types):
        try:
            i = tuple(i)
        except Exception as ex:
            raise ValueError(
                f"Cannot tuple-ize {argname}({i!r}) due to: {ex}"
            ) from None

    return i


def asset(i, argname, allowed_types=set):
    if not i:
        return i if isinstance(i, allowed_types) else set()

    if isinstance(i, str):
        i = {i}
    elif not isinstance(i, allowed_types):
        try:
            i = set(i)
        except Exception as ex:
            raise ValueError(f"Cannot set-ize {argname}({i!r}) due to: {ex}") from None

    return i


def func_name(
    fn, default=..., mod=None, fqdn=None, human=None, partials=None
) -> Optional[str]:
    """
    FQDN of `fn`, descending into partials to print their args.

    :param default:
        What to return if it fails; by default it raises.
    :param mod:
        when true, prepend module like ``module.name.fn_name``
    :param fqdn:
        when true, use ``__qualname__`` (instead of ``__name__``)
        which differs mostly on methods, where it contains class(es),
        and locals, respectively (:pep:`3155`).
        *Sphinx* uses `fqdn=True` for generating IDs.
    :param human:
        when true, explain built-ins, and assume ``partials=True`` (if that was None)
    :param partials:
        when true (or omitted & `human` true), partials denote their args
        like ``fn({"a": 1}, ...)``

    :return:
        a (possibly dot-separated) string, or `default` (unless this is ``...```).
    :raises:
        Only if default is ``...``, otherwise, errors debug-logged.


    **Examples**

        >>> func_name(func_name)
        'func_name'
        >>> func_name(func_name, mod=1)
        'graphtik.base.func_name'
        >>> func_name(func_name.__format__, fqdn=0)
        '__format__'
        >>> func_name(func_name.__format__, fqdn=1)
        'function.__format__'

    Even functions defined in docstrings are reported:

        >>> def f():
        ...     def inner():
        ...         pass
        ...     return inner

        >>> func_name(f, mod=1, fqdn=1)
        'graphtik.base.f'
        >>> func_name(f(), fqdn=1)
        'f.<locals>.inner'

    On failures, arg `default` controls the outcomes:

    TBD
    """
    if partials is None:
        partials = human

    if isinstance(fn, (partial, partialmethod)):
        # Always bubble-up errors.
        fn_name = func_name(fn.func, default, mod, fqdn, human)
        if partials:
            args = ", ".join(str(i) for i in fn.args)
            kw = ", ".join(f"{k}={v}" for k, v in fn.keywords.items())
            args_str = ", ".join(i for i in (args, kw) + ("...",) if i)
            fn_name = f"{fn_name}({args_str})"

        return fn_name

    try:
        if human and inspect.isbuiltin(fn):
            return str(fn)
        # Not possible for all objects to fake ``__qualname__``.
        fn_name = (
            fn.__qualname__ if fqdn and hasattr(fn, "__qualname__") else fn.__name__
        )
        assert fn_name

        mod_name = getattr(fn, "__module__", None)
        if mod and mod_name:
            fn_name = ".".join((mod_name, fn_name))
        return fn_name
    except Exception as ex:
        if default is ...:
            raise
        log.debug("Ignored error while inspecting %r name: %s", fn, ex)
        return default


def _un_partial_ize(func):
    """
    Alter functions working on 1st arg being a callable, to descend it if it's a partial.
    """

    @wraps(func)
    def wrapper(fn, *args, **kw):
        if isinstance(fn, (partial, partialmethod)):
            fn = fn.func
        return func(fn, *args, **kw)

    return wrapper


@_un_partial_ize
def func_source(fn, default=..., human=None) -> Optional[Tuple[str, int]]:
    """
    Like :func:`inspect.getsource` supporting partials.

    :param default:
        If given, better be a 2-tuple respecting types,
        or ``...``, to raise.
    :param human:
        when true, denote builtins like python does
    """
    try:
        if inspect.isbuiltin(fn):
            if human:
                return inspect.getdoc(fn).splitlines()[0]
            else:
                return str(fn)
        return inspect.getsource(fn)
    except Exception as ex:
        if default is ...:
            raise
        log.debug("Ignored error while inspecting %r sources: %s", fn, ex)
        return default


@_un_partial_ize
def func_sourcelines(fn, default=..., human=None) -> Optional[Tuple[str, int]]:
    """
    Like :func:`inspect.getsourcelines` supporting partials.

    :param default:
        If given, better be a 2-tuple respecting types,
        or ``...``, to raise.
    """
    try:
        if human and inspect.isbuiltin(fn):
            return [str(fn)], -1
        return inspect.getsourcelines(fn)
    except Exception as ex:
        if default is ...:
            raise
        log.debug("Ignored error while inspecting %r sourcelines: %s", fn, ex)
        return default


class PlotArgs(NamedTuple):
    """
    All the args of a :meth:`.Plottable.plot()` call,

    check this method for a more detailed  explanation of its attributes.
    """

    #: who is the caller
    plottable: "Plottable" = None
    #: what to plot (or the "overlay" when calling :meth:`Plottable.plot()`)
    graph: "nx.Graph" = None
    #: The name of the graph in the dot-file (important for cmaps).
    name: str = None
    #: the list of execution plan steps.
    steps: Collection = None
    #: the list of input names .
    inputs: Collection = None
    #: the list of output names .
    outputs: Collection = None
    #: Contains the computed results, which might be different from :attr:`plottable`.
    solution: "graphtik.planning.Solution" = None
    #: Either a mapping of node-names to dot(``.``)-separated cluster-names, or
    #: false/true to enable :term:`plotter`'s default clustering of nodes based
    #: on their dot-separated name parts.
    #:
    #: Note that if it's `None` (default), the plotter will cluster based on node-names,
    #: BUT the Plan may replace the None with a dictionary with the "pruned" cluster
    #: (when its :term:`dag` differs from network's :term:`graph`);
    #: to suppress the pruned-cluster, pass a truthy, NON-dictionary value.
    clusters: Mapping = None
    #: If given, overrides :active plotter`.
    plotter: "graphtik.plot.Plotter" = None
    #: If given, overrides :term:`plot theme` plotter will use.
    #: It can be any mapping, in which case it overrite the :term:`current theme`.
    theme: "graphtik.plot.Theme" = None

    #######
    # Internal item-args for :meth:`.Plotter._make_node()` etall.
    #
    #: Where to add graphviz nodes & stuff.
    dot: "pydot.Dot" = None
    #: The node (data(str) or :class:`Operation`) or edge as gotten from nx-graph.
    nx_item: Any = None
    #: Attributes gotten from nx-graph for the given graph/node/edge.
    #: They are NOT a clone, so any modifications affect the nx `graph`.
    nx_attrs: dict = None
    #: The pydot-node/edge created
    dot_item: Any = None
    #: Collect the actual clustered `dot_nodes` among the given nodes.
    clustered: dict = None

    #######
    # Render args
    #
    #: jupyter configuration overrides
    jupyter_render: Mapping = None
    #: where to write image or show in a matplotlib window
    filename: Union[str, bool, int] = None

    def clone_or_merge_graph(self, base_graph) -> "PlotArgs":
        """
        Overlay :attr:`graph` over `base_graph`, or clone `base_graph`, if no attribute.

        :return:
            the updated plot_args
        """

        if self.graph:
            import networkx as nx

            graph = nx.compose(base_graph, self.graph)
        else:
            graph = base_graph.copy()  # cloned, to freely annotate downstream

        return self._replace(graph=graph)

    def with_defaults(self, *args, **kw) -> "PlotArgs":
        """Replace only fields with `None` values."""
        return self._replace(
            **{k: v for k, v in dict(*args, **kw).items() if getattr(self, k) is None}
        )

    @property
    def kw_render_pydot(self) -> dict:
        return {k: getattr(self, k) for k in self._fields[-2:]}


## Defined here, to avoid subclasses importing `plot` module.
class Plottable(abc.ABC):
    """
    :term:`plottable` capabilities and graph props for all major classes of the project.

    Classes wishing to plot their graphs should inherit this and
    implement property ``plot`` to return a "partial" callable that somehow
    ends up calling  :func:`.plot.render_pydot()` with the `graph` or any other
    args bound appropriately.
    The purpose is to avoid copying this function & documentation here around.
    """

    graph: "networkx.Graph"

    def plot(
        self,
        filename: Union[str, bool, int] = None,
        show=None,
        *,
        plotter: "graphtik.plot.Plotter" = None,
        theme: "graphtik.plot.Theme" = None,
        graph: "networkx.Graph" = None,
        name=None,
        steps=None,
        inputs=None,
        outputs=None,
        solution: "graphtik.planning.Solution" = None,
        clusters: Mapping = None,
        jupyter_render: Union[None, Mapping, str] = None,
    ) -> "pydot.Dot":
        """
        Entry-point for plotting ready made operation graphs.

        :param str filename:
            Write a file or open a `matplotlib` window.

            - If it is a string or file, the diagram is written into the file-path

              Common extensions are ``.png .dot .jpg .jpeg .pdf .svg``
              call :func:`.plot.supported_plot_formats()` for more.

            - If it IS `True`, opens the  diagram in a  matplotlib window
              (requires `matplotlib` package to be installed).

            - If it equals `-1`, it mat-plots but does not open the window.

            - Otherwise, just return the ``pydot.Dot`` instance.

            :seealso: :attr:`.PlotArgs.filename`, :meth:`.Plotter.render_pydot()`
        :param plottable:
            the :term:`plottable` that ordered the plotting.
            Automatically set downstreams to one of::

                op | pipeline | net | plan | solution | <missing>

            :seealso: :attr:`.PlotArgs.plottable`
        :param plotter:
            the :term:`plotter` to handle plotting; if none, the :term:`active plotter`
            is used by default.

            :seealso: :attr:`.PlotArgs.plotter`
        :param theme:
            Any :term:`plot theme` or dictionary overrides; if none,
            the :attr:`.Plotter.default_theme` of the :term:`active plotter` is used.

            :seealso: :attr:`.PlotArgs.theme`
        :param name:
            if not given, dot-lang graph would is named "G"; necessary to be unique
            when referring to generated CMAPs.
            No need to quote it, handled by the plotter, downstream.

            :seealso: :attr:`.PlotArgs.name`
        :param str graph:
            (optional) A :class:`nx.Digraph` with overrides to merge with the graph provided
            by underlying plottables (translated by the :term:`active plotter`).

            It may contain *graph*, *node* & *edge* attributes for any usage,
            but these conventions apply:

            ``'graphviz.xxx'`` *(graph/node/edge attributes)*
                Any "user-overrides" with this prefix are sent verbatim a `Graphviz`_
                attributes.

                .. Note::

                    Remember to escape those values as `Graphviz`_ HTML-Like strings
                    (use :func:`.plot.graphviz_html_string()`).

            ``no_plot`` *(node/edge attribute)*
                element skipped from plotting
                (see *"Examples:"* section, below)

            :seealso: :attr:`.PlotArgs.graph`
        :param inputs:
            an optional name list, any nodes in there are plotted
            as a "house"

            :seealso: :attr:`.PlotArgs.inputs`
        :param outputs:
            an optional name list, any nodes in there are plotted
            as an "inverted-house"

            :seealso: :attr:`.PlotArgs.outputs`
        :param solution:
            an optional dict with values to annotate nodes, drawn "filled"
            (currently content not shown, but node drawn as "filled").
            It extracts more infos from a :class:`.Solution` instance, such as,
            if `solution` has an ``executed`` attribute, operations contained in it
            are  drawn as "filled".

            :seealso: :attr:`.PlotArgs.solution`
        :param clusters:
            Either a mapping, or false/true to enable :term:`plotter`'s default
            clustering of nodes base on their dot-separated name parts.

            Note that if it's `None` (default), the plotter will cluster based on node-names,
            BUT the Plan may replace the None with a dictionary with the "pruned" cluster
            (when its :term:`dag` differs from network's :term:`graph`);
            to suppress the pruned-cluster, pass a truthy, NON-dictionary value.

            Practically, when it is a:

            - dictionary of node-names --> dot(``.``)-separated cluster-names,
              it is respected, even if empty;
            - truthy: cluster based on dot(``.``)-separated node-name parts;
            - falsy: don't cluster at all.

            :seealso: :attr:`.PlotArgs.clusters`
        :param jupyter_render:
            a nested dictionary controlling the rendering of graph-plots in Jupyter cells,
            if `None`, defaults to :data:`jupyter_render`; you may modify it in place
            and apply for all future calls (see :ref:`jupyter_rendering`).

            :seealso: :attr:`.PlotArgs.jupyter_render`
        :param show:
            .. deprecated:: v6.1.1
                Merged with `filename` param (filename takes precedence).

        :return:
            a |pydot.Dot|_ instance
            (for reference to as similar API to |pydot.Dot|_ instance, visit:
            https://pydotplus.readthedocs.io/reference.html#pydotplus.graphviz.Dot)

            The |pydot.Dot|_ instance returned is rendered directly in *Jupyter/IPython*
            notebooks as SVG images (see :ref:`jupyter_rendering`).

        Note that the `graph` argument is absent - Each Plottable provides
        its own graph internally;  use directly :func:`.render_pydot()` to provide
        a different graph.

        .. image:: images/GraphtikLegend.svg
            :alt: Graphtik Legend

        *NODES:*

        oval
            function
        egg
            subgraph operation
        house
            given input
        inversed-house
            asked output
        polygon
            given both as input & asked as output (what?)
        square
            intermediate data, neither given nor asked.
        red frame
            evict-instruction, to free up memory.
        filled
            data node has a value in `solution` OR function has been executed.
        thick frame
            function/data node in execution `steps`.

        *ARROWS*

        solid black arrows
            dependencies (source-data *need*-ed by target-operations,
            sources-operations *provides* target-data)
        dashed black arrows
            optional needs
        blue arrows
            sideffect needs/provides
        wheat arrows
            broken dependency (``provide``) during pruning
        green-dotted arrows
            execution steps labeled in succession

        To generate the **legend**, see :func:`.legend()`.


        **Examples:**

            >>> from graphtik import compose, operation
            >>> from graphtik.modifier import optional
            >>> from operator import add

            >>> pipeline = compose("pipeline",
            ...     operation(name="add", needs=["a", "b1"], provides=["ab1"])(add),
            ...     operation(name="sub", needs=["a", optional("b2")], provides=["ab2"])(lambda a, b=1: a-b),
            ...     operation(name="abb", needs=["ab1", "ab2"], provides=["asked"])(add),
            ... )

            >>> pipeline.plot(True);                       # plot just the graph in a matplotlib window # doctest: +SKIP
            >>> inputs = {'a': 1, 'b1': 2}
            >>> solution = pipeline(**inputs)              # now plots will include the execution-plan

        The solution is also *plottable*:

            >>> solution.plot('plot1.svg');                                                             # doctest: +SKIP

        or you may augment the pipelinewith the requested inputs/outputs & solution:

            >>> pipeline.plot('plot1.svg', inputs=inputs, outputs=['asked', 'b1'], solution=solution);  # doctest: +SKIP

        In any case you may get the `pydot.Dot` object
        (n.b. it is renderable in Jupyter as-is):

            >>> dot = pipeline.plot(solution=solution);
            >>> print(dot)
            digraph pipeline {
            fontname=italic;
            label=<pipeline>;
            node [fillcolor=white, style=filled];
            <a> [fillcolor=wheat, fixedsize=shape, label=<<TABLE CELLBORDER="0" CELLSPACING="0" BORDER="0">
            ...

        .. graphtik::

        .. TODO: move advanced plot examples from API --> tutorial page

        You may use the :attr:`PlotArgs.graph` overlay to skip certain nodes (or edges)
        from the plots:

            >>> import networkx as nx

            >>> g = nx.DiGraph()  # the overlay
            >>> to_hide = pipeline.net.find_op_by_name("sub")
            >>> g.add_node(to_hide, no_plot=True)
            >>> dot = pipeline.plot(graph=g)
            >>> assert "<sub>" not in str(dot), str(dot)

        .. graphtik::

        """
        kw = locals().copy()

        del kw["self"]
        show = kw.pop("show", None)
        if show:
            import warnings

            warnings.warn(
                "Argument `plot` has merged with `filename` and will be deleted soon.",
                DeprecationWarning,
            )
            if not filename:
                kw["filename"] = show

        plot_args = PlotArgs(**kw)

        from .plot import Plotter, Theme, get_active_plotter
        from .execution import Solution

        ## Ensure a valid plotter in the args asap.
        #
        if plotter and not isinstance(plotter, Plotter):
            raise TypeError(f"Invalid `plotter` argument given: {plotter}")
        if not plotter:
            plotter = get_active_plotter()
        plot_args = plot_args._replace(plotter=plotter)
        assert plot_args.plotter, f"Expected `plotter`: {plot_args}"

        ## Overwrite any dictionaries over active theme asap.
        #
        if isinstance(theme, cabc.Mapping):
            theme = plotter.default_theme.withset(**theme)
            plot_args = plot_args._replace(theme=theme)

        plot_args = self.prepare_plot_args(plot_args)
        assert plot_args.graph, f"Expected `graph: {plot_args}"

        plot_args = plot_args.with_defaults(
            # Don't leave `solution` unassigned, if possible.
            solution=plot_args.plottable
            if isinstance(plot_args.plottable, Solution)
            else None
        )

        if plot_args.steps is None and plot_args.solution is not None:
            plot_args = plot_args._replace(steps=plot_args.solution.plan.steps)

        return plot_args.plotter.plot(plot_args)

    @abc.abstractmethod
    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        """
        Called by :meth:`plot()` to create the nx-graph and other plot-args, e.g. solution.

        - Clone the graph or merge it with the one in the `plot_args`
          (see :meth:`PlotArgs.clone_or_merge_graph()`.

        - For the rest args, prefer :meth:`PlotArgs.with_defaults()` over
          :meth:`_replace()`,
          not to override user args.
        """

    @property
    def ops(self) -> List["Operation"]:
        """A new list with all :term:`operation`\\s contained in the :term:`network`."""
        from .planning import yield_ops

        return list(yield_ops(self.graph))

    @property
    def data(self) -> List[str]:
        """A new list with all :term:`operation`\\s contained in the :term:`network`."""
        from .planning import yield_datanodes

        return list(yield_datanodes(self.graph))

    def find_ops(self, predicate) -> List["Operation"]:
        """
        Scan operation nodes and fetch those satisfying `predicate`.

        :param predicate:
            the :term:`node predicate` is a 2-argument callable(op, node-data)
            that should return true for nodes to include.
        """
        return [
            n
            for n, d in self.graph.nodes.data(True)
            if isinstance(n, Operation) and predicate(n, d)
        ]

    def find_op_by_name(self, name) -> Optional["Operation"]:
        """Fetch the 1st operation named with the given `name`."""
        from .planning import yield_ops

        for n in yield_ops(self.graph):
            if n.name == name:
                return n


class RenArgs(NamedTuple):
    """
    Arguments received by callbacks in :meth:`.rename()` and :term:`operation nesting`.
    """

    #: what is currently being renamed,
    #: one of the string::
    #:
    #:     op
    #:     need.jsonpart
    #:     need
    #:     provide.jsonpart
    #:     provide
    #:     alias.jsonpart
    #:     alias
    #:
    #: Any :term:`jsonp` parts are renamed prior to the full path (as ordered above).
    typ: str
    #: the operation currently being processed
    op: "Operation"
    # the name of the item to be renamed/nested
    name: str
    #: The parent :class:`.Pipeline` of the operation currently being processed,.
    #: Has value only when doing :term:`operation nesting` from :func:`.compose()`.
    parent: "Pipeline" = None


class Operation(Plottable, abc.ABC):
    """An abstract class representing an action with :meth:`.compute()`."""

    name: str
    needs: Items
    provides: Items

    def __eq__(self, other):
        """Operation identity is based on `name`."""
        return bool(self.name == getattr(other, "name", other))

    def __hash__(self):
        """Operation identity is based on `name`."""
        return hash(self.name)

    @abc.abstractmethod
    def compute(
        self,
        named_inputs,
        outputs=None,
        recompute_from=None,
        *kw,
    ):
        """
        Compute (optional) asked `outputs` for the given `named_inputs`.

        It is called by :class:`.Network`.
        End-users should simply call the operation with `named_inputs` as kwargs.

        :param named_inputs:
            the input values with which to feed the computation.
        :param outputs:
            what results to compute,
            see :meth:`.Pipeline.compute()`.
        :param recompute_from:
            recompute all downstream from those dependencies,
            see :meth:`.Pipeline.compute()`.

        :returns list:
            Should return a list values representing
            the results of running the feed-forward computation on
            ``inputs``.
        """

    # def plot(self, *args, **kw):
    #     """Dead impl so as to be easier to make a dummy Op."""
    #     raise NotImplementedError("Operation subclasses")

    def _rename_graph_names(
        self, kw, renamer: Union[Callable[[RenArgs], str], Mapping[str, str]]
    ) -> None:
        """
        Pass operation & dependency names through `renamer`.

        :param kw:
            all data are extracted 1st from this kw, falling back on the operation's
            attributes, and it is modified in-place

        For the other 2 params, see :meth:`.FnOp.withset()`.

        :raise ValueError:
            - if a `renamer` was neither dict nor callable
            - if a `renamer` dict contained a non-string value,
        """
        from .modifier import dep_renamed

        def with_errors_logged(fn, ren_args: RenArgs) -> str:
            """Wrap `fn` to log its errors without touching ex, for debug aid."""
            ok = False
            try:
                ret = fn(ren_args)
                ok = True
                return ret
            finally:
                if not ok:
                    log.warning("Failed to rename %s", ren_args)

        def rename_driver(ren_args: RenArgs) -> str:
            """Handle dicts, callables and non-string names as true/false."""

            new_name = old_name = ren_args.name
            if isinstance(renamer, cabc.Mapping):
                dst = renamer.get(old_name)
                if callable(dst) or (dst and isinstance(dst, str)):
                    new_name = dep_renamed(old_name, dst)
            elif callable(renamer):
                dst = renamer(ren_args)
                if dst and isinstance(dst, str):
                    new_name = dst
                # A falsy means don't touch the node.
            else:
                raise AssertionError(
                    f"Invalid `renamer` {renamer!r} should have been caught earlier."
                )

            if (not new_name and old_name) or not isinstance(new_name, str):
                raise ValueError(
                    f"Must rename {old_name!r} into a non-empty string, got {new_name!r}!"
                )

            return new_name

        def rename_subdocs(ren_args):
            parts = getattr(ren_args.name, "_jsonp", None)
            if parts:  # assuming deps here have been jsonpized earlier.
                path = "/".join(
                    rename_driver(
                        ren_args._replace(typ=ren_args.typ + ".jsonpart", name=p)
                    )
                    for p in parts
                )
                ren_args = ren_args._replace(name=dep_renamed(ren_args.name, path))
            return rename_driver(ren_args)

        ren_args = RenArgs(None, self, None)

        kw["name"] = with_errors_logged(
            rename_driver, ren_args._replace(typ="op", name=kw.get("name", self.name))
        )
        ren_args = ren_args._replace(typ="need")
        kw["needs"] = [
            with_errors_logged(rename_subdocs, ren_args._replace(name=n))
            for n in kw.get("needs", self.needs)
        ]
        ren_args = ren_args._replace(typ="provide")
        # Store renamed `provides` as map, used for `aliases` below.
        renamed_provides = {
            n: with_errors_logged(rename_subdocs, ren_args._replace(name=n))
            for n in kw.get("provides", self.provides)
        }
        kw["provides"] = list(renamed_provides.values())
        if hasattr(self, "aliases"):
            ren_args = ren_args._replace(typ="alias")
            kw["aliases"] = [
                (
                    renamed_provides[k],
                    with_errors_logged(rename_subdocs, ren_args._replace(name=v)),
                )
                for k, v in kw.get("aliases", self.aliases)  # pylint: disable=no-member
            ]

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
