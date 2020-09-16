# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
:term:`compose` :term:`network` of operations & dependencies, :term:`compile` the :term:`plan`.
"""
import logging
import sys
from collections import abc, defaultdict
from functools import partial
from itertools import count
from typing import (
    Any,
    Callable,
    Collection,
    Iterable,
    List,
    Mapping,
    NamedTuple,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import networkx as nx
from boltons.iterutils import pairwise
from boltons.setutils import IndexedSet as iset

from .base import Items, Operation, PlotArgs, Plottable, astuple
from .config import is_debug, is_skip_evictions
from .jetsam import save_jetsam
from .modifier import (
    dep_renamed,
    dep_stripped,
    get_jsonp,
    get_keyword,
    is_implicit,
    is_optional,
    is_pure_sfx,
    is_sfx,
    modifier_withset,
    modify,
    optional,
)

NodePredicate = Callable[[Any, Mapping], bool]
OpMap = Mapping[Operation, Any]

#: If this logger is *eventually* DEBUG-enabled,
#: the string-representation of network-objects (network, plan, solution)
#: is augmented with children's details.
log = logging.getLogger(__name__)


def yield_datanodes(nodes) -> List[str]:
    """May scan dag nodes."""
    return (n for n in nodes if isinstance(n, str))


def yield_ops(nodes) -> List[Operation]:
    """May scan (preferably)  ``plan.steps`` or dag nodes."""
    return (n for n in nodes if isinstance(n, Operation))


def yield_node_names(nodes):
    """Yield either ``op.name`` or ``str(node)``."""
    return (getattr(n, "name", n) for n in nodes)


def _optionalized(graph, data):
    """Retain optionality of a `data` node based on all `needs` edges."""
    all_optionals = all(e[2] for e in graph.out_edges(data, "optional", False))
    return (
        optional(data)
        if all_optionals
        else data  # sideffect
        if is_sfx(data)
        else modifier_withset(
            data,
            # un-optionalize
            optional=None,
            # not relevant for a pipeline
            keyword=False,
        )
    )


def collect_requirements(graph) -> Tuple[iset, iset]:
    """Collect & split datanodes in (possibly overlapping) `needs`/`provides`."""
    operations = list(yield_ops(graph))
    provides = iset(p for op in operations for p in op.provides)
    needs = iset(_optionalized(graph, n) for op in operations for n in op.needs)
    provides = iset(provides)
    return needs, provides


def root_doc(dag, doc: str) -> str:
    """
    Return the most superdoc, or the same `doc` is not in a chin, or
    raise if node unknown.
    """
    for src, dst, subdoc in dag.in_edges(doc, data="subdoc"):
        if subdoc:
            doc = src
    return doc


EdgeTraversal = Tuple[str, int]


def _yield_also_chained_docs(
    dig_dag: List[EdgeTraversal], dag, doc: str, stop_set=()
) -> Iterable[str]:
    """
    Dig the `doc` and its sub/super docs, not recursing in those already in `stop_set`.

    :param dig_dag:
        a sequence of 2-tuples like ``("in_edges", 0)``, with the name of
        a networkx method and which edge-node to pick, 0:= src, 1:= dst
    :param stop_set:
        Stop traversing (and don't return)  `doc` if already contained in
        this set.

    :return:
        the given `doc`, and any other docs discovered with `dig_dag`
        linked with a "subdoc" attribute on their edge,
        except those sub-trees with a root node already in `stop_set`.
        If `doc` is not in `dag`, returns empty.
    """
    if doc not in dag:
        return

    if doc not in stop_set:
        yield doc
        for meth, idx in dig_dag:
            for *edge, subdoc in getattr(dag, meth)(doc, data="subdoc"):
                child = edge[idx]
                if subdoc:
                    yield from _yield_also_chained_docs(
                        ((meth, idx),), dag, child, stop_set
                    )


def _yield_chained_docs(
    dig_dag: Union[EdgeTraversal, List[EdgeTraversal]],
    dag,
    docs: Iterable[str],
    stop_set=(),
) -> Iterable[str]:
    """
    Like :func:`_yield_also_chained_docs()` but digging for many docs at once.

    :return:
        the given `docs`, and any other nodes discovered with `dig_dag`
        linked with a "subdoc" attribute on their edge,
        except those sub-trees with a root node already in `stop_set`.
    """
    return (
        dd for d in docs for dd in _yield_also_chained_docs(dig_dag, dag, d, stop_set)
    )


#: Calls :func:`_yield_also_chained_docs()` for subdocs.
yield_also_subdocs = partial(_yield_also_chained_docs, (("out_edges", 1),))
#: Calls :func:`_yield_also_chained_docs()` for superdocs.
yield_also_superdocs = partial(_yield_also_chained_docs, (("in_edges", 0),))
#: Calls :func:`_yield_also_chained_docs()` for both subdocs & superdocs.
yield_also_chaindocs = partial(
    _yield_also_chained_docs, (("out_edges", 1), ("in_edges", 0))
)

#: Calls :func:`_yield_chained_docs()` for subdocs.
yield_subdocs = partial(_yield_chained_docs, (("out_edges", 1),))
#: Calls :func:`_yield_chained_docs()` for superdocs.
yield_superdocs = partial(_yield_chained_docs, (("in_edges", 0),))
#: Calls :func:`_yield_chained_docs()` for both subdocs & superdocs.
yield_chaindocs = partial(_yield_chained_docs, (("out_edges", 1), ("in_edges", 0)))


def clone_graph_with_stripped_sfxed(graph):
    """Clone `graph` including ALSO stripped :term:`sideffected` deps, with original attrs. """

    def with_stripped_node(node):
        stripped = dep_stripped(node)

        return (node,) if stripped is node else (node, stripped)

    def with_stripped_edge(n1, n2):
        nn1, nn2 = dep_stripped(n1), dep_stripped(n2)

        return (
            ((n1, n2), (nn1, n2))
            if n1 is not nn1
            else ((n1, n2), (n1, nn2))
            if n2 is not nn2
            else ((n1, n2),)
        )

    clone = graph.__class__()
    clone.add_nodes_from(
        (nn, nd) for n, nd in graph.nodes.items() for nn in with_stripped_node(n)
    )
    clone.add_edges_from(
        (*ee, ed) for e, ed in graph.edges.items() for ee in with_stripped_edge(*e)
    )

    return clone


def _topo_sort_nodes(dag) -> iset:
    """
    Topo-sort dag by execution order & operation-insertion order to break ties.

    This means (probably!?) that the first inserted win the `needs`, but
    the last one win the `provides` (and the final solution).

    Inform user in case of cycles.
    """
    node_keys = dict(zip(dag.nodes, count()))
    try:
        return iset(nx.lexicographical_topological_sort(dag, key=node_keys.get))
    except nx.NetworkXUnfeasible as ex:
        import sys
        from textwrap import dedent

        tb = sys.exc_info()[2]
        msg = dedent(
            f"""
            {ex}

            TIP:
                Launch a post-mortem debugger, move 3 frames UP, and
                plot the `graphtik.planning.Network' class in `self`
                to discover the cycle.

                If GRAPHTIK_DEBUG enabled, this plot will be stored tmp-folder
                automatically :-)
            """
        )
        raise nx.NetworkXUnfeasible(msg).with_traceback(tb)


def inputs_for_recompute(
    graph,
    inputs: Sequence[str],
    recompute_from: Sequence[str],
    recompute_till: Sequence[str] = None,
) -> Tuple[iset, iset]:
    """
    Clears the inputs between `recompute_from >--<= recompute_till` to clear.

    :param graph:
        MODIFIED, at most 2 helper nodes inserted
    :param inputs:
        a sequence
    :param recompute_from:
        None or a sequence, including any out-of-graph deps (logged))
    :param recompute_till:
        (optional) a sequence, only in-graph deps.

    :return:
        a 2-tuple with the reduced `inputs` by the dependencies that must
        be removed from the graph to recompute (along with those dependencies).

    It works by temporarily adding x2 nodes to find and remove the intersection of::

        strict-descendants(recompute_from) & ancestors(recompute_till)

    FIXME: merge recompute() with travesing unsatisfied (see ``test_recompute_NEEDS_FIX``)
    bc it clears inputs of unsatisfied ops (cannot be replaced later)
    """
    START, STOP = "_TMP.RECOMPUTE_FROM", "_TMP.RECOMPUTE_TILL"

    deps = set(yield_datanodes(graph.nodes))
    recompute_from = iset(recompute_from)  # traversed in logs
    inputs = iset(inputs)  # returned
    bad = recompute_from - deps
    if bad:
        log.info("... ignoring unknown `recompute_from` dependencies: %s", list(bad))
        recompute_from = recompute_from & deps  # avoid sideffect in `recompute_from`
    assert recompute_from, f"Given unknown-only `recompute_from` {locals()}"

    graph.add_edges_from((START, i) for i in recompute_from)

    between_deps = iset(nx.descendants(graph, START)) & deps - recompute_from

    if recompute_till:
        graph.add_edges_from((i, STOP) for i in recompute_till)  # edge reversed!

        upstreams = set(nx.ancestors(graph, STOP)) & deps  # upstream
        between_deps &= upstreams

    recomputes = between_deps & inputs
    new_inputs = iset(inputs) - recomputes

    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "... recompute x%i data%s means deleting x%i inputs%s, to arrive from x%i %s -> x%i %s.",
            len(between_deps),
            list(between_deps),
            len(recomputes),
            list(recomputes),
            len(inputs),
            list(inputs),
            len(new_inputs),
            list(new_inputs),
        )

    return new_inputs, recomputes


def unsatisfied_operations(dag, inputs: Iterable) -> Tuple[OpMap, iset]:
    """
    Traverse topologically sorted dag to collect un-satisfied operations.

    Unsatisfied operations are those suffering from ANY of the following:

    - They are missing at least one compulsory need-input.
        Since the dag is ordered, as soon as we're on an operation,
        all its needs have been accounted, so we can get its satisfaction.

    - Their provided outputs are not linked to any data in the dag.
        An operation might not have any output link when :meth:`_prune_graph()`
        has broken them, due to given intermediate inputs.

    :param dag:
        a graph with broken edges those arriving to existing inputs
    :param inputs:
        an iterable of the names of the input values
    :return:
        a 2-tuple with ({pruned-op, unsatisfied-explanation}, topo-sorted-nodes)

    """
    # Collect data that will be produced.
    ok_data = set()
    # Input parents assumed to contain all subdocs.
    ok_data.update(yield_chaindocs(dag, inputs, ok_data))
    # To collect the map of operations --> satisfied-needs.
    op_satisfaction = defaultdict(set)
    # To collect the operations to drop.
    pruned_ops = {}
    ## Topo-sort dag respecting operation-insertion order to break ties.
    sorted_nodes = _topo_sort_nodes(dag)

    if log.isEnabledFor(logging.DEBUG):
        log.debug("...topo-sorted nodes: %s", list(yield_node_names(sorted_nodes)))
    for i, node in enumerate(sorted_nodes):
        if isinstance(node, Operation):
            if not dag.adj[node]:
                pruned_ops[node] = "needless-outputs"
                log.info("... pruned step #%i due to needless-outputs\n  %s", i, node)
            else:
                real_needs = set(
                    n for n, _, opt in dag.in_edges(node, data="optional") if not opt
                )
                satisfied_needs = op_satisfaction[node]
                ## Sanity check that op's needs are never broken
                # assert real_needs == set(n for n in node.needs if not is_optional(n))
                if real_needs.issubset(satisfied_needs):
                    # Op is satisfied; mark its outputs as ok.
                    ok_data.update(yield_chaindocs(dag, dag.adj[node], ok_data))
                else:
                    pruned_ops[
                        node
                    ] = msg = f"unsatisfied-needs{list(real_needs - satisfied_needs)}"
                    log.info("... pruned step #%i due to %s\n  %s", i, msg, node)
        elif isinstance(node, str):  # `str` are givens
            if node in ok_data:
                # mark satisfied-needs on all future operations
                for future_op in dag.adj[node]:
                    op_satisfaction[future_op].add(node)
        else:
            raise AssertionError(f"Unrecognized network graph node {node}")

    return pruned_ops, sorted_nodes


class Network(Plottable):
    """
    A graph of operations that can :term:`compile` an execution plan.

    .. attribute:: needs

        the "base", all data-nodes that are not produced by some operation
    .. attribute:: provides

        the "base", all data-nodes produced by some operation
    """

    def __init__(self, *operations, graph=None):
        """

        :param operations:
            to be added in the graph
        :param graph:
            if None, create a new.

        :raises ValueError:
            if dupe operation, with msg:

                *Operations may only be added once, ...*
        """
        ## Check for duplicate, operations can only append  once.
        #
        uniques = set(operations)
        if len(operations) != len(uniques):
            dupes = list(operations)
            for i in uniques:
                dupes.remove(i)
            raise ValueError(
                f"Operations may only be added once, dupes: {list(dupes)}"
                f"\n  out of: {list(operations)}"
            )

        if graph is None:
            # directed graph of operation and data nodes defining the net.
            graph = nx.DiGraph()
        else:
            if not isinstance(graph, nx.Graph):
                raise TypeError(f"Must be a NetworkX graph, was: {graph}")

        #: The :mod:`networkx` (Di)Graph containing all operations and dependencies,
        #: prior to :term:`planning`.
        self.graph = graph

        for op in operations:
            self._append_operation(graph, op)
        self.needs, self.provides = collect_requirements(self.graph)

        #: Speed up :meth:`compile()` call and avoid a multithreading issue(?)
        #: that is occurring when accessing the dag in networkx.
        self._cached_plans = {}

    def __repr__(self):
        nodes = self.graph.nodes
        ops = list(yield_ops(nodes))
        steps = (
            [f"\n  +--{s}" for s in nodes]
            if is_debug()
            else ", ".join(n.name for n in ops)
        )
        return f"Network(x{len(nodes)} nodes, x{len(ops)} ops: {''.join(steps)})"

    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        plot_args = plot_args.clone_or_merge_graph(self.graph)
        plot_args = plot_args.with_defaults(
            name=f"network-x{len(self.graph.nodes)}-nodes",
            inputs=self.needs,
            outputs=self.provides,
        )
        plot_args = plot_args._replace(plottable=self)

        return plot_args

    def _append_operation(self, graph, operation: Operation):
        """
        Adds the given operation and its data requirements to the network graph.

        - Invoked during constructor only (immutability).
        - Identities are based on the name of the operation, the names of the operation's needs,
          and the names of the data it provides.
        - Adds needs, operation & provides, in that order.

        :param graph:
            the `networkx` graph to append to
        :param operation:
            operation instance to append
        """
        subdoc_attrs = {"subdoc": True}
        # Using a separate set (and not ``graph.edges`` view)
        # to avoid concurrent access error.
        seen_doc_edges = set()

        def unseen_subdoc_edges(doc_edges):
            """:param doc_edges: e.g. ``[(root, root/n1), (root/n1, root/n1/n11)]``"""
            ## Start in reverse, from leaf edge, and stop
            #  ASAP a known edge is met, assuming path to root
            #  has already been inserted into graph.
            #
            for src, dst in reversed(doc_edges):
                if (src, dst) in seen_doc_edges:
                    break

                seen_doc_edges.add((src, dst))
                yield (src, dst, subdoc_attrs)

        def append_subdoc_chain(doc_parts):
            doc_chain = list(doc_parts)
            doc_chain = [
                modify("/".join(doc_chain[: i + 1])) for i in range(len(doc_chain))
            ]
            # FIXME: subdocs ignore double-slashes or final slash!
            doc_chain = [p for p in doc_chain if p]
            graph.add_edges_from(unseen_subdoc_edges(pairwise(doc_chain)))

        ## Needs
        #
        needs = []
        needs_edges = []
        for n in operation.needs:
            json_path = get_jsonp(n)
            if json_path:
                append_subdoc_chain(json_path)

            nkw, ekw = {}, {}  # node, edge props
            if is_optional(n):
                ekw["optional"] = True
            if is_sfx(n):
                ekw["sideffect"] = nkw["sideffect"] = True
            if is_implicit(n):
                ekw["implicit"] = True
            keyword = get_keyword(n)
            if keyword:
                ekw["keyword"] = keyword
            needs.append((n, nkw))
            needs_edges.append((n, operation, ekw))
        graph.add_nodes_from(needs)
        node_props = getattr(operation, "node_props", None) or {}
        graph.add_node(operation, **node_props)
        graph.add_edges_from(needs_edges)

        ## Prepare inversed-aliases index, used
        #  to label edges reaching to aliased `provides`.
        #
        aliases = getattr(operation, "aliases", None)
        alias_destinations = {v: src for src, v in aliases} if aliases else ()

        ## Provides
        #
        for n in operation.provides:
            json_path = get_jsonp(n)
            if json_path:
                append_subdoc_chain(json_path)

            kw = {}
            if is_sfx(n):
                kw["sideffect"] = True
                graph.add_node(n, sideffect=True)
            if is_implicit(n):
                kw["implicit"] = True

            if n in alias_destinations:
                kw["alias_of"] = alias_destinations[n]

            graph.add_edge(operation, n, **kw)

    def _apply_graph_predicate(self, graph, predicate):
        to_del = []
        for node, data in graph.nodes.items():
            try:
                if isinstance(node, Operation) and not predicate(node, data):
                    to_del.append(node)
            except Exception as ex:
                raise ValueError(
                    f"Node-predicate({predicate}) failed due to: {ex}\n  node: {node}, {self}"
                ) from ex
        log.info("... predicate filtered out %s.", list(yield_node_names(to_del)))
        graph.remove_nodes_from(to_del)

    def _prune_graph(
        self, inputs: Items, outputs: Items, predicate: NodePredicate = None
    ) -> Tuple[nx.DiGraph, Tuple, Tuple, OpMap]:
        """
        Determines what graph steps need to run to get to the requested
        outputs from the provided inputs:
        - Eliminate steps that are not on a path arriving to requested outputs;
        - Eliminate unsatisfied operations: partial inputs or no outputs needed;
        - consolidate the list of needs & provides.

        :param inputs:
            The names of all given inputs.
        :param outputs:
            The desired output names.  This can also be ``None``, in which
            case the necessary steps are all graph nodes that are reachable
            from the provided inputs.
        :param predicate:
            the :term:`node predicate` is a 2-argument callable(op, node-data)
            that should return true for nodes to include; if None, all nodes included.

        :return:
            a 4-tuple:

            - the *pruned* :term:`execution dag`,
            - net's needs & outputs based on the given inputs/outputs and the net
              (may overlap, see :func:`collect_requirements()`),
            - an {op, prune-explanation} dictionary


            Use the returned `needs/provides` to build a new plan.

        :raises ValueError:
            - if `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*
        """
        # TODO: break cycles based on weights here.
        dag = self.graph

        ##  When `inputs` is None, we have to keep all possible input nodes
        #   and this is achieved with 2 tricky locals:
        #
        #   inputs
        #       it is kept falsy, to disable the edge-breaking, so that
        #       the ascending_from_outputs that follows can reach all input nodes;
        #       including intermediate ones;
        #   satisfied_inputs
        #       it is filled with all possible input nodes, to trick `unsatisfied_operations()`
        #       to assume their operations are satisfied, and keep them.
        #
        if inputs is None and outputs is None:
            satisfied_inputs, outputs = self.needs, self.provides
        else:
            if inputs is None:  # outputs: NOT None
                satisfied_inputs = self.needs - outputs
            else:  # inputs: NOT None, outputs: None
                # Just ignore `inputs` not in the graph.
                satisfied_inputs = inputs = iset(inputs) & dag.nodes

            ## Scream on unknown `outputs`.
            #
            if outputs:
                unknown_outputs = iset(outputs) - dag.nodes
                if unknown_outputs:
                    raise ValueError(
                        f"Unknown output nodes: {list(unknown_outputs)}\n  {self}"
                        "\n  (tip: set GRAPHTIK_DEBUG envvar to view Op details in print-outs)"
                    )

        assert isinstance(satisfied_inputs, abc.Collection)
        assert inputs is None or isinstance(inputs, abc.Collection)
        assert outputs is None or isinstance(outputs, abc.Collection)

        broken_dag = dag.copy()  # preserve net's graph

        if predicate:
            self._apply_graph_predicate(broken_dag, predicate)

        # Break the incoming edges to all given inputs.
        #
        # Nodes producing any given intermediate inputs are unnecessary
        # (unless they are also used elsewhere).
        # To discover which ones to prune, we break their incoming edges
        # and they will drop out while collecting ancestors from the outputs.
        #
        if inputs:
            for n in inputs:
                # Coalesce to a list, to avoid concurrent modification.
                broken_dag.remove_edges_from(
                    list(
                        (src, dst)
                        for src, dst, subdoc in broken_dag.in_edges(n, data="subdoc")
                        if not subdoc
                    )
                )

        comments: OpMap = {}

        # Drop stray input values and operations (if any).
        if outputs is not None:
            ## If caller requested specific outputs, we can prune any
            #  unrelated nodes further up the dag.
            #
            ending_in_outputs = set()
            for out in yield_chaindocs(dag, outputs, ending_in_outputs):
                # TODO: speedup prune-by-outs with traversing code
                ending_in_outputs.update(nx.ancestors(broken_dag, out))
                ending_in_outputs.add(out)
            # Clone it, to modify it, or BUG@@ much later (e.g in eviction planing).
            broken_dag = broken_dag.subgraph(ending_in_outputs).copy()

            irrelevant_ops = [
                op for op in yield_ops(dag) if op not in ending_in_outputs
            ]
            if irrelevant_ops:
                comments.update((op, "outputs-irrelevant") for op in irrelevant_ops)
                log.info(
                    "... dropping output-irrelevant ops%s.\n    +--outputs: %s",
                    irrelevant_ops,
                    outputs,
                )

        # Prune unsatisfied operations (those with partial inputs or no outputs).
        unsatisfied, sorted_nodes = unsatisfied_operations(broken_dag, satisfied_inputs)
        comments.update(unsatisfied)

        # Clone it, to modify it.
        pruned_dag = dag.subgraph(broken_dag.nodes - unsatisfied).copy()
        ## Clean unlinked data-nodes (except those both given & asked).
        #
        unlinked_data = set(nx.isolates(pruned_dag))
        if outputs is not None:
            # FIXME: must cast to simple set due to mahmoud/boltons#252 (boltons < v20.1)
            unlinked_data -= set(satisfied_inputs & outputs)
        pruned_dag.remove_nodes_from(unlinked_data)

        inputs = iset(
            _optionalized(pruned_dag, n) for n in satisfied_inputs if n in pruned_dag
        )
        if outputs is None:
            outputs = iset(
                n
                for n in self.provides
                if n not in inputs and n in pruned_dag and not is_sfx(n)
            )
        else:
            # filter-out from new `provides` if pruned.
            outputs = iset(n for n in outputs if n in pruned_dag)

        assert inputs is not None or isinstance(inputs, abc.Collection)
        assert outputs is not None or isinstance(outputs, abc.Collection)

        return pruned_dag, sorted_nodes, tuple(inputs), tuple(outputs), comments

    def _build_execution_steps(
        self, pruned_dag, sorted_nodes, inputs: Collection, outputs: Collection
    ) -> List:
        """
        Create the list of operations and :term:`eviction` steps, to execute given IOs.

        :param pruned_dag:
            The original dag, pruned; not broken.
        :param sorted_nodes:
            an :class:`~boltons:boltons.setutils.IndexedSet` with all graph nodes
            topo-sorted (including pruned ones)
        :param inputs:
            Not used(!), useless inputs will be evicted when the solution is created.
        :param outputs:
            outp-names to decide whether to add (and which) evict-instructions

        :return:
            the list of operation or dependencies to evict, in computation order


        **IMPLEMENTATION:**

        The operation steps are based on the topological sort of the DAG,
        therefore pruning must have eliminated any cycles.

        Then the eviction steps are introduced between the operation nodes
        (if enabled, and `outputs` have been asked, or else all outputs are kept),
        to reduce asap solution's memory footprint while the computation is running.

        - An evict-instruction is inserted on 2 occasions:

          1. whenever a *need* of a an executed op is not used by any other *operation*
             further down the DAG.
          2. whenever a *provide* falls beyond the `pruned_dag`.

        - For :term:`doc chain`\\s, it is either evicted the whole chain (from root),
          or nothing at all.

        - For eviction purposes, :class:`sfxed` dependencies are equivalent
          to their stripped :term:`sideffected` ones, so these are also inserted
          in the graph (after sorting, to evade cycles).

        """
        ## Sort by execution order, then by operation-insertion, to break ties,
        #  and inform user in case of cycles (must have been caught earlier) .
        #
        if not outputs or is_skip_evictions():
            # When no specific outputs asked, NO EVICTIONS,
            # so just add the Operations.
            return list(op for op in yield_ops(sorted_nodes) if op in pruned_dag)

        ## Strip SFXED without the fear of cycles
        #  (must augment dag before stripping outputs docchains).
        #
        pruned_dag = clone_graph_with_stripped_sfxed(pruned_dag)
        outputs = set(oo for o in outputs for oo in (o, dep_stripped(o)))
        outputs = set(yield_chaindocs(pruned_dag, outputs))

        ## Add Operation and Eviction steps.
        #
        def add_eviction(dep):
            if steps:
                if steps[-1] == dep:
                    # Functions with redundant SFXEDs like ['a', sfxed('a', ...)]??
                    log.warning(
                        "Skipped adding dupe eviction step %r @ #%i."
                        "\n  (hint: do you have redundant SFXEDs like ['a', sfxed('a', ...)]??)",
                        dep,
                        len(steps),
                    )
                    return
                if log.isEnabledFor(logging.DEBUG) and dep in steps:
                    # Happens by rule-2 if multiple Ops produce
                    # the same pruned out.
                    log.debug("... re-evicting %r @ #%i.", dep, len(steps))
            steps.append(dep)

        steps = []
        for i, op in enumerate(sorted_nodes):
            if not isinstance(op, Operation) or op not in pruned_dag:
                continue

            steps.append(op)

            future_nodes = sorted_nodes[i + 1 :]

            ## EVICT(1) operation's needs not to be used in the future.
            #
            #  Broken links are irrelevant bc they are predecessors of data (provides),
            #  but here we scan for predecessors of the operation (needs).
            #
            for need in pruned_dag.predecessors(op):
                need_chain = set(yield_also_chaindocs(pruned_dag, need))

                ## Don't evict if any `need` in doc-chain has been asked
                #  as output.
                #
                if need_chain & outputs:
                    continue

                ## Don't evict if any `need` in doc-chain will be used
                #  in the future.
                #
                need_users = set(
                    dst
                    for n in need_chain
                    for _, dst, subdoc in pruned_dag.out_edges(n, data="subdoc")
                    if not subdoc
                )
                if not need_users & future_nodes:
                    log.debug(
                        "... adding evict-1 for not-to-be-used NEED-chain%s of topo-sorted #%i %s .",
                        need_chain,
                        i,
                        op,
                    )
                    add_eviction(root_doc(pruned_dag, need))

            ## EVICT(2) for operation's pruned provides,
            #  by searching nodes in net missing from plan.
            #  .. image:: docs/source/images/unpruned_useless_provides.svg
            #
            for provide in self.graph.successors(op):
                if provide not in pruned_dag:  # speedy test, to avoid scanning chain.
                    log.debug(
                        "... adding evict-2 for pruned-PROVIDE(%r) of topo-sorted #%i %s.",
                        provide,
                        i,
                        op,
                    )
                    add_eviction(root_doc(pruned_dag, provide))

        return list(steps)

    def _deps_tuplized(self, deps, arg_name):
        """Stabilize None, ``UNSET`` or string/list-of-strings deps in `graph`."""
        if deps is None:
            return None, None

        data_nodes = set(yield_datanodes(self.graph.nodes))
        deps = tuple(sorted(astuple(deps, arg_name, allowed_types=abc.Collection)))
        return deps, tuple(d for d in deps if d in data_nodes)

    def compile(
        self,
        inputs: Items = None,
        outputs: Items = None,
        recompute_from=None,
        *,
        predicate=None,
    ) -> "ExecutionPlan":
        """
        Create or get from cache an execution-plan for the given inputs/outputs.

        See :meth:`_prune_graph()` and :meth:`_build_execution_steps()`
        for detailed description.

        :param inputs:
            A collection with the names of all the given inputs.
            If `None``, all inputs that lead to given `outputs` are assumed.
            If string, it is converted to a single-element collection.
        :param outputs:
            A collection or the name of the output name(s).
            If `None``, all reachable nodes from the given `inputs` are assumed.
            If string, it is converted to a single-element collection.
        :param recompute_from:
            Described in :meth:`.Pipeline.compute()`.
        :param predicate:
            the :term:`node predicate` is a 2-argument callable(op, node-data)
            that should return true for nodes to include; if None, all nodes included.

        :return:
            the cached or fresh new :term:`execution plan`

        :raises ValueError:
            *Unknown output nodes...*
                if `outputs` asked do not exist in network.
            *Unsolvable graph: ...*
                if it cannot produce any `outputs` from the given `inputs`.
            *Plan needs more inputs...*
                if given `inputs` mismatched plan's :attr:`needs`.
            *Unreachable outputs...*
                if net cannot produce asked `outputs`.
        """
        from .execution import ExecutionPlan

        ok = False
        try:
            ## Make a stable cache-key,
            #  ignoring out-of-graph nodes (2nd results).
            #
            inputs, k1 = self._deps_tuplized(inputs, "inputs")
            outputs, k2 = self._deps_tuplized(outputs, "outputs")
            recompute_from, k3 = self._deps_tuplized(recompute_from, "recompute_from")
            if not predicate:
                predicate = None
            cache_key = (k1, k2, k3, predicate, is_skip_evictions())

            ## Build (or retrieve from cache) execution plan
            #  for the given dep-lists (excluding any unknown node-names).
            #
            if cache_key in self._cached_plans:
                log.debug("... compile cache-hit key: %s", cache_key)
                plan = self._cached_plans[cache_key]
            else:
                if recompute_from:
                    inputs, recomputes = inputs_for_recompute(
                        self.graph.copy(), inputs, recompute_from, k2
                    )

                _prune_results = self._prune_graph(inputs, outputs, predicate)
                pruned_dag, sorted_nodes, needs, provides, op_comments = _prune_results

                steps = self._build_execution_steps(
                    pruned_dag, sorted_nodes, needs, outputs or ()
                )
                plan = ExecutionPlan(
                    self,
                    needs,
                    provides,
                    pruned_dag,
                    tuple(steps),
                    asked_outs=outputs is not None,
                    comments=op_comments,
                )

                self._cached_plans[cache_key] = plan
                log.debug("... compile cache-updated key: %s", cache_key)

            ok = True
            return plan
        finally:
            if not ok:
                ex = sys.exc_info()[1]
                save_jetsam(
                    ex,
                    locals(),
                    "pruned_dag",
                    "sorted_nodes",
                    "needs",
                    "provides",
                    "op_comments",
                    "plan",
                )
