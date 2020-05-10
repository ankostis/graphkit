# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
.. default-role: term

`compose` `operation` & `dependency` into `pipeline`\\s match/zip inputs/outputs during `execution`.

.. default-role:
.. note::
    This module (along with :mod:`.modifiers`) is what client code needs
    to define pipelines *on import time* without incurring a heavy price
    (<5ms on a 2019 fast PC)
"""

import abc
import itertools as itt
import logging
import re
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
    NamedTuple,
    Tuple,
    Union,
)

from boltons.setutils import IndexedSet as iset

from .base import (
    UNSET,
    Items,
    MultiValueError,
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
    is_mapped,
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
    solution: "graphtik.network.Solution" = None
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
    Classes wishing to plot their graphs should inherit this and ...

    implement property ``plot`` to return a "partial" callable that somehow
    ends up calling  :func:`.plot.render_pydot()` with the `graph` or any other
    args bound appropriately.
    The purpose is to avoid copying this function & documentation here around.
    """

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
        solution: "graphtik.network.Solution" = None,
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

                op | netop | net | plan | solution | <missing>

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
            >>> from graphtik.modifiers import optional
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
            <a> [fillcolor=wheat, margin="0.04,0.02", shape=invhouse, style=filled, tooltip="(int) 1"];
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
        pass


class RenArgs(NamedTuple):
    """
    Arguments received by callbacks in :meth:`.rename()` and :term:`operation nesting`.
    """

    #: what is currently being renamed,
    #: one of the string: ``(op | needs | provides | aliases)``
    typ: str
    #: the operation currently being processed
    op: "Operation"
    # the name of the item to be renamed/nested
    name: str
    #: The parent :class:`.NetworkOperation` of the operation currently being processed,.
    #: Has value only when doing :term:`operation nesting` from :func:`.compose()`.
    parent: "NetworkOperation" = None


class Operation(Plottable, abc.ABC):
    """An abstract class representing an action with :meth:`.compute()`."""

    name: str
    needs: Items
    provides: Items

    @property
    def __name__(self) -> str:
        return self.name

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

    # def plot(self, *args, **kw):
    #     """Dead impl so as to be easier to make a dummy Op."""
    #     raise NotImplementedError("Operation subclasses")

    def _rename_graph_names(
        self, kw, renamer: Union[Callable[[RenArgs], str], Mapping[str, str]],
    ) -> None:
        """
        Pass operation & dependency names through `renamer`.

        :param kw:
            all data are extracted 1st from this kw, falling back on the operation's
            attributes, and it is modified in-place

        For the other 2 params, see :meth:`.FunctionalOperation.withset()`.

        :raise ValueError:
            - if a `renamer` was neither dict nor callable
            - if a `renamer` dict contained a non-string value,
        """

        def rename_driver(ren_args: RenArgs) -> str:
            """Handle dicts, callables and non-string names as true/false."""

            ok = False
            try:
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

                if not new_name or not isinstance(new_name, str):
                    raise ValueError(
                        f"Must rename {old_name!r} into a non-empty string, got {new_name!r}!"
                    )

                ok = True
                return new_name
            finally:
                if not ok:
                    # Debug aid without touching ex.
                    log.warning("Failed to rename %s", ren_args)

        ren_args = RenArgs(None, self, None)

        kw["name"] = rename_driver(
            ren_args._replace(typ="op", name=kw.get("name", self.name))
        )
        ren_args = ren_args._replace(typ="needs")
        kw["needs"] = [
            rename_driver(ren_args._replace(name=n))
            for n in kw.get("needs", self.needs)
        ]
        ren_args = ren_args._replace(typ="provides")
        # Store renamed `provides` as map, used for `aliases` below.
        renamed_provides = {
            n: rename_driver(ren_args._replace(name=n))
            for n in kw.get("provides", self.provides)
        }
        kw["provides"] = list(renamed_provides.values())
        if hasattr(self, "aliases"):
            ren_args = ren_args._replace(typ="aliases")
            kw["aliases"] = [
                (renamed_provides[k], rename_driver(ren_args._replace(name=v)),)
                for k, v in kw.get("aliases", self.aliases)  # pylint: disable=no-member
            ]


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


class FunctionalOperation(Operation, Plottable):
    """
    An :term:`operation` performing a callable (ie a function, a method, a lambda).

    .. Tip::
        - Use :func:`.operation()` factory to build instances of this class instead.
        - Call :meth:`withset()` on existing instances to re-configure new clones.
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
        #: (dupes preserved, with sideffects & SINGULARIZED :term:`sideffected`\s).
        #: It is stored for builder functionality to work.
        self.needs = needs
        #: Value names ready to lay the graph for :term:`pruning`
        #: (NO dupes, WITH aliases & sideffects, and SINGULAR :term:`sideffected`\s).
        self.op_needs = op_needs
        #: Value names the underlying function requires
        #: (dupes preserved, without sideffects, with stripped :term:`sideffected` dependencies).
        self._fn_needs = _fn_needs

        #: The :term:`provides` almost as given by the user
        #: (which may contain MULTI-sideffecteds and dupes),
        #: roughly morphed into `_fn_provides` + sideffects
        #: (dupes preserved, without aliases, with sideffects & SINGULARIZED :term:`sideffected`\s).
        #: It is stored for builder functionality to work.
        self.provides = provides
        #: Value names ready to lay the graph for :term:`pruning`
        #: (NO dupes, WITH aliases & sideffects, and SINGULAR sideffecteds).
        self.op_provides = op_provides
        #: Value names the underlying function produces
        #: (dupes preserved, without aliases & sideffects, with stripped :term:`sideffected` dependencies).
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
        #: :meth:`.NetworkOperation.withset()`.
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
        Display more informative names for the Operation class
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

            >>> from graphtik import sfx

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

        msg = textwrap.indent("\n".join(errors), " " * 4)
        raise MultiValueError(f"Failed preparing needs: \n{msg}", *exceptions)

    def _match_inputs_with_fn_needs(self, named_inputs) -> Tuple[list, list, dict]:
        positional, vararg_vals = [], []
        kwargs = {}
        errors, missing, varargs_bad = [], [], []
        for n in self._fn_needs:
            assert not is_sfx(n), locals()
            try:
                if n not in named_inputs:
                    if not is_optional(n) or is_sfx(n):
                        # It means `inputs` < compulsory `needs`.
                        # Compilation should have ensured all compulsories existed,
                        # but ..?
                        ##
                        missing.append(n)
                    continue

                ## TODO: augment modifiers with "retrievers" from `inputs`.
                inp_value = named_inputs[n]

                if is_mapped(n):
                    kwargs[n.fn_kwarg] = inp_value

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
        :meth:`.NetworkOperation.withset()`.
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


class NULL_OP(FunctionalOperation):
    """
    Eliminates same-named operations added later during term:`operation merging`.

    :seealso: :ref:`operation-merging`
    """

    def __init__(self, name):
        super().__init__(name=name)

    def compute(self, *args, **kw):
        raise AssertionError("Should have been eliminated!")


def _id_bool(b):
    return hash(bool(b)) + 1


def _id_tristate_bool(b):
    return 3 if b is None else (hash(bool(b)) + 1)


class NetworkOperation(Operation, Plottable):
    """
    An operation that can :term:`compute` a network-graph of operations.

    .. Tip::
        Use :func:`compose()` factory to prepare the `net` and build
        instances of this class.
    """

    #: The name for the new netop, used when nesting them.
    name = None
    #: Will prune `net` against these possible outputs when :meth:`compute()` called.
    outputs = None
    #: The :term:`node predicate` is a 2-argument callable(op, node-data)
    #: that should return true for nodes to include; if None, all nodes included.
    predicate = None
    #: The outputs names (possibly `None`) used to compile the :attr:`plan`.
    outputs = None

    def __init__(
        self,
        operations,
        name,
        *,
        outputs=None,
        predicate: "NodePredicate" = None,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        node_props=None,
        renamer=None,
    ):
        """
        For arguments, ee :meth:`withset()` & class attributes.

        :raises ValueError:
            if dupe operation, with msg:

                *Operations may only be added once, ...*
        """
        from .network import build_network

        ## Set data asap, for debugging, although `net.withset()` will reset them.
        self.name = name
        # Remember Outputs for future `compute()`?
        self.outputs = outputs
        self.predicate = predicate

        # Prune network
        self.net = build_network(
            operations, rescheduled, endured, parallel, marshalled, node_props, renamer,
        )
        self.name, self.needs, self.provides, _aliases = reparse_operation_data(
            self.name, self.net.needs, self.net.provides
        )

    def __repr__(self):
        """
        Display more informative names for the Operation class
        """
        from .config import is_debug

        clsname = type(self).__name__
        items = [repr(self.name)]
        if self.needs:
            items.append(f"needs={aslist(self.needs, 'needs')}")
        if self.provides:
            items.append(f"provides={aslist(self.provides, 'provides')}")
        ops = self.ops
        if ops:
            steps = (
                "".join(f"\n  +--{s}" for s in ops)
                if is_debug()
                else ", ".join(str(s.name) for s in ops)
            )
            items.append(f"x{len(ops)} ops: {steps}")
        return f"{clsname}({', '.join(items)})"

    @property
    def ops(self) -> List[Operation]:
        """A new list with all :term:`operation`\\s contained in the :term:`network`."""
        from .network import yield_ops

        return list(yield_ops(self.net.graph))

    def withset(
        self,
        outputs: Items = UNSET,
        predicate: "NodePredicate" = UNSET,
        *,
        name=None,
        rescheduled=None,
        endured=None,
        parallel=None,
        marshalled=None,
        node_props=None,
        renamer=None,
    ) -> "NetworkOperation":
        """
        Return a copy with a network pruned for the given `needs` & `provides`.

        :param outputs:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, the value of this instance is conveyed to the clone.
        :param predicate:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, the value of this instance is conveyed to the clone.
        :param name:
            the name for the new netop:

            - if `None`, the same name is kept;
            - if True, a distinct name is  devised::

                <old-name>-<uid>

            - otherwise, the given `name` is applied.
        :param rescheduled:
            applies :term:`reschedule`\\d to all contained `operations`
        :param endured:
            applies :term:`endurance` to all contained `operations`
        :param parallel:
            mark all contained `operations` to be executed in :term:`parallel`
        :param marshalled:
            mark all contained `operations` to be :term:`marshalled <marshalling>`
            (usefull when run in `parallel` with a :term:`process pool`).
        :param renamer:
            see respective parameter in :meth:`.FunctionalOperation.withset()`.

        :return:
            A narrowed netop clone, which **MIGHT be empty!***

        :raises ValueError:
            - If `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*

        """
        outputs = self.outputs if outputs == UNSET else outputs
        predicate = self.predicate if predicate == UNSET else predicate

        if name is None:
            name = self.name
        elif name is True:
            name = self.name

            ## Devise a stable UID based args.
            #
            uid = str(
                abs(
                    hash(str(outputs))
                    ^ hash(predicate)
                    ^ (1 * _id_bool(rescheduled))
                    ^ (2 * _id_bool(endured))
                    ^ (4 * _id_tristate_bool(parallel))
                    ^ (8 * _id_tristate_bool(marshalled))
                )
            )[:7]
            m = re.match(r"^(.*)-(\d+)$", name)
            if m:
                name = m.group(1)
            name = f"{name}-{uid}"

        return NetworkOperation(
            self.ops,
            name,
            outputs=outputs,
            predicate=predicate,
            rescheduled=rescheduled,
            endured=endured,
            parallel=parallel,
            marshalled=marshalled,
            node_props=node_props,
            renamer=renamer,
        )

    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        """Delegate to network. """
        from .plot import graphviz_html_string

        plottable = self.net
        plot_args = plot_args.with_defaults(name=self.name)
        plot_args = plottable.prepare_plot_args(plot_args)
        assert plot_args.graph, plot_args

        plot_args.graph.graph.setdefault(
            "graphviz.label", graphviz_html_string(self.name)
        )
        plot_args = plot_args._replace(plottable=self)

        return plot_args

    def compile(
        self, inputs=None, outputs=UNSET, predicate: "NodePredicate" = UNSET
    ) -> "ExecutionPlan":
        """
        Produce a :term:`plan` for the given args or `outputs`/`predicate` narrowed earlier.

        :param named_inputs:
            a string or a list of strings that should be fed to the `needs` of all operations.
        :param outputs:
            A string or a list of strings with all data asked to compute.
            If ``None``, all possible intermediate outputs will be kept.
            If not given, those set by a previous call to :meth:`withset()` or cstor are used.
        :param predicate:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, those set by a previous call to :meth:`withset()` or cstor are used.

        :return:
            the :term:`execution plan` satisfying the given `inputs`, `outputs` & `predicate`

        :raises ValueError:
            - If `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*

            - If solution does not contain any operations, with msg:

                *Unsolvable graph: ...*

            - If given `inputs` mismatched plan's :attr:`needs`, with msg:

                *Plan needs more inputs...*

            - If net cannot produce asked `outputs`, with msg:

                *Unreachable outputs...*
        """
        outputs = self.outputs if outputs == UNSET else outputs
        predicate = self.predicate if predicate == UNSET else predicate

        return self.net.compile(inputs, outputs, predicate)

    def compute(
        self,
        named_inputs: Mapping = UNSET,
        outputs: Items = UNSET,
        predicate: "NodePredicate" = UNSET,
    ) -> "Solution":
        """
        Compile a plan & :term:`execute` the graph, sequentially or parallel.

        .. Attention::
            If intermediate :term:`compilation` is successful, the "global
            :term:`abort run` flag is reset before the :term:`execution` starts.

        :param named_inputs:
            A mapping of names --> values that will be fed to the `needs` of all operations.
            Cloned, not modified.
        :param outputs:
            A string or a list of strings with all data asked to compute.
            If ``None``, all intermediate data will be kept.

        :return:
            The :term:`solution` which contains the results of each operation executed
            +1 for inputs in separate dictionaries.

        :raises ValueError:
            - If `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*

            - If plan does not contain any operations, with msg:

                *Unsolvable graph: ...*

            - If given `inputs` mismatched plan's :attr:`needs`, with msg:

                *Plan needs more inputs...*

            - If net cannot produce asked `outputs`, with msg:

                *Unreachable outputs...*

        See also :meth:`.Operation.compute()`.
        """
        from .config import reset_abort

        try:
            if named_inputs is UNSET:
                named_inputs = {}

            net = self.net  # jetsam
            outputs = self.outputs if outputs == UNSET else outputs
            predicate = self.predicate if predicate == UNSET else predicate

            # Build the execution plan.
            log.debug("=== Compiling netop(%s)...", self.name)
            plan = net.compile(named_inputs.keys(), outputs, predicate)

            # Restore `abort` flag for next run.
            reset_abort()

            solution = plan.execute(named_inputs, outputs, name=self.name)

            return solution
        except Exception as ex:
            jetsam(ex, locals(), "plan", "solution", "outputs", network="net")
            raise

    def __call__(self, **input_kwargs) -> "Solution":
        """
        Delegates to :meth:`compute()`, respecting any narrowed `outputs`.
        """
        # To respect narrowed `outputs` must send them due to recompilation.
        return self.compute(input_kwargs, outputs=self.outputs)


def nest_any_node(ren_args: RenArgs) -> str:
    """Nest both operation & data under `parent`'s name (if given).

    :return:
        the nested name of the operation or data
    """

    def prefixed(name):
        return f"{ren_args.parent.name}.{name}" if ren_args.parent else name

    return dep_renamed(ren_args.name, prefixed)


def compose(
    name,
    op1,
    *operations,
    outputs: Items = None,
    rescheduled=None,
    endured=None,
    parallel=None,
    marshalled=None,
    nest: Union[Callable[[RenArgs], str], Mapping[str, str], Union[bool, str]] = None,
    node_props=None,
) -> NetworkOperation:
    """
    Merge or :term:`nest <operation nesting>` operations & pipelines into a new pipeline,

    based on the ``nest`` parameter (read below)

    Operations given earlier (further to the left) override those following
    (further to the right), similar to `set` behavior (and contrary to `dict`).

    :param str name:
        A optional name for the graph being composed by this object.
    :param op1:
        syntactically force at least 1 operation
    :param operations:
        each argument should be an operation or pipeline instance
    :param nest:
        a dictionary or callable corresponding to the `renamer` paremater
        of :meth:`.NetOperation.withset()`, but the calable receives a `ren_args`
        with :attr:`RenArgs.parent` set when merging a pipeline, and applies
        the default nesting behavior (:func:`.nest_any_node()`) on truthies.

        Specifically:

        - if it is a dictionary, it renames any operations & data named as keys
          into the respective values, like that:

          - if a value is callable or str, it is fed into :func:`.dep_renamed`
            (hint: it can be single-arg callable like: ``(str) -> str``)
          - it applies default all-nodes nesting if other truthy;

          Note that you cannot access the "parent" name with dictionaries,
          you can only apply default all-node nesting by returning a non-string truthy.

        - if it is a :func:`.callable`, it is given a :class:`.RenArgs` instance
          to decide the node's name.

          The callable may return a *str* for the new-name, or any other true/false
          to apply default all-nodes nesting.

          For example, to nest just operation's names (but not their dependencies),
          call::

              compose(
                  ...,
                  nest=lambda ren_args: ren_args.typ == "op"
              )

          .. Attention::
              The callable SHOULD wish to preserve any :term:`modifier` on dependencies,
              and use :func:`.dep_renamed()`.

        - If false (default), applies :term:`operation merging`, not *nesting*.

        - if true, applies default :term:`operation nesting` to all types of nodes.

        In all other cases, the names are preserved.

        .. seealso::
            - :ref:`operation-nesting` for examples
            - Default nesting applied by :func:`.nest_any_node()`

    :param rescheduled:
        applies :term:`reschedule`\\d to all contained `operations`
    :param endured:
        applies :term:`endurance` to all contained `operations`
    :param parallel:
        mark all contained `operations` to be executed in :term:`parallel`
    :param marshalled:
        mark all contained `operations` to be :term:`marshalled <marshalling>`
        (usefull when run in `parallel` with a :term:`process pool`).
    :param node_props:
        Added as-is into NetworkX graph, to provide for filtering
        by :meth:`.NetworkOperation.withset()`.
        Also plot-rendering affected if they match `Graphviz` properties,
        unless they start with underscore(``_``)

    :return:
        Returns a special type of operation class, which represents an
        entire computation graph as a single operation.

    :raises ValueError:
        - If the `net`` cannot produce the asked `outputs` from the given `inputs`.
        - If `nest` callable/dictionary produced an non-string or empty name
          (see (NetworkPipeline))
    """
    operations = (op1,) + operations
    if not all(isinstance(op, Operation) for op in operations):
        raise TypeError(f"Non-Operation instances given: {operations}")

    ## Apply default nesting if user asked just a truthy.
    #
    if nest:
        if not callable(nest) and not isinstance(nest, cabc.Mapping):
            renamer = nest_any_node
        else:

            def nest_wrapper(ren_args: RenArgs) -> str:
                """Handle user's `nest` callable or dict."""

                new_name = old_name = ren_args.name
                if isinstance(nest, cabc.Mapping):
                    dst = nest.get(old_name)
                    if dst:
                        if callable(dst) or isinstance(dst, str):
                            new_name = dep_renamed(old_name, dst)
                        else:
                            # Apply default nesting for non-str truthy values.
                            new_name = nest_any_node(ren_args)
                    # A falsy means don't touch the node.

                elif callable(nest):
                    dst = nest(ren_args)
                    if dst:
                        if isinstance(dst, str):
                            new_name = dst
                        else:
                            # Truthy but not str values mean apply default nesting.
                            new_name = nest_any_node(ren_args)
                    # A falsy means don't touch the node.
                else:
                    raise AssertionError(f"Case unhandled earlier {nest!r}: {locals()}")

                return new_name

            renamer = nest_wrapper
    else:
        renamer = None

    return NetworkOperation(
        operations,
        name,
        outputs=outputs,
        rescheduled=rescheduled,
        endured=endured,
        parallel=parallel,
        marshalled=marshalled,
        node_props=node_props,
        renamer=renamer,
    )
