# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
:term:`compose` :term:`network` of operations & dependencies, :term:`compile` the :term:`plan`.
"""
import logging
from collections import abc, defaultdict
from itertools import count
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

import networkx as nx
from boltons.setutils import IndexedSet as iset

from .base import Items, astuple, jetsam
from .config import is_debug, is_skip_evictions
from .modifiers import dep_renamed, is_mapped, is_optional, is_sfx, optional
from .composition import FunctionalOperation, Operation, PlotArgs, RenArgs, Plottable

NodePredicate = Callable[[Any, Mapping], bool]

#: If this logger is *eventually* DEBUG-enabled,
#: the string-representation of network-objects (network, plan, solution)
#: is augmented with children's details.
log = logging.getLogger(__name__)


class _EvictInstruction(str):
    """
    A step in the ExecutionPlan to evict a computed value from the `solution`.

    It's a step in :attr:`ExecutionPlan.steps` for the data-node `str` that
    frees its data-value from `solution` after it is no longer needed,
    to reduce memory footprint while computing the graph.
    """

    __slots__ = ()  # avoid __dict__ on instances

    def __repr__(self):
        return f"EvictInstruction('{self}')"


def _yield_datanodes(nodes):
    """May scan dag nodes."""
    return (n for n in nodes if isinstance(n, str))


def yield_ops(nodes):
    """May scan (preferably)  ``plan.steps`` or dag nodes."""
    return (n for n in nodes if isinstance(n, Operation))


def yield_node_names(nodes):
    """Yield either ``op.name`` or ``str(node)``."""
    return (str(getattr(n, "name", n)) for n in nodes)


def _optionalized(graph, data):
    """Retain optionality of a `data` node based on all `needs` edges."""
    all_optionals = all(e[2] for e in graph.out_edges(data, "optional", False))
    return (
        optional(data)
        if all_optionals
        else data  # sideffect
        if is_sfx(data)
        else str(data)  # un-optionalize
    )


def collect_requirements(graph) -> Tuple[iset, iset]:
    """Collect & split datanodes in (possibly overlapping) `needs`/`provides`."""
    operations = list(yield_ops(graph))
    provides = iset(
        p for op in operations for p in getattr(op, "op_provides", op.provides)
    )
    needs = iset(_optionalized(graph, n) for op in operations for n in op.needs)
    provides = iset(provides)
    return needs, provides


def unsatisfied_operations(dag, inputs: Collection) -> List:
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
        a list of unsatisfied operations to prune

    """
    # To collect data that will be produced.
    ok_data = set(inputs)
    # To collect the map of operations --> satisfied-needs.
    op_satisfaction = defaultdict(set)
    # To collect the operations to drop.
    unsatisfied = []
    # Topo-sort dag respecting operation-insertion order to break ties.
    sorted_nodes = nx.topological_sort(dag)
    for node in sorted_nodes:
        if isinstance(node, Operation):
            if not dag.adj[node]:
                # Prune operations that ended up providing no output.
                unsatisfied.append(node)
            else:
                # It's ok not to dig into edge-data("optional") here,
                # we care about all needs, including broken ones.
                real_needs = set(n for n in node.needs if not is_optional(n))
                if real_needs.issubset(op_satisfaction[node]):
                    # We have a satisfied operation; mark its output-data
                    # as ok.
                    ok_data.update(dag.adj[node])
                else:
                    # Prune operations with partial inputs.
                    unsatisfied.append(node)
        elif isinstance(node, str):  # `str` are givens
            if node in ok_data:
                # mark satisfied-needs on all future operations
                for future_op in dag.adj[node]:
                    op_satisfaction[future_op].add(node)
        else:
            raise AssertionError(f"Unrecognized network graph node {node}")

    return unsatisfied


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
            raise ValueError(f"Operations may only be added once, dupes: {list(dupes)}")

        if graph is None:
            # directed graph of operation and data nodes defining the net.
            graph = nx.DiGraph()
        else:
            if not isinstance(graph, nx.Graph):
                raise TypeError(f"Must be a NetworkX graph, was: {graph}")

        #: The :mod:`networkx` (Di)Graph containing all operations and dependencies,
        #: prior to :term:`compilation`.
        self.graph = graph

        for op in operations:
            self._append_operation(graph, op)
        self.needs, self.provides = collect_requirements(self.graph)

        #: Speed up :meth:`compile()` call and avoid a multithreading issue(?)
        #: that is occurring when accessing the dag in networkx.
        self._cached_plans = {}

    def __repr__(self):
        ops = list(yield_ops(self.graph.nodes))
        steps = (
            [f"\n  +--{s}" for s in self.graph.nodes]
            if is_debug()
            else ", ".join(n.name for n in ops)
        )
        return f"Network(x{len(self.graph.nodes)} nodes, x{len(ops)} ops: {''.join(steps)})"

    def find_ops(self, predicate) -> List[Operation]:
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

    def find_op_by_name(self, name) -> Union[Operation, None]:
        """Fetch the 1st operation named with the given `name`."""
        for n in yield_ops(self.graph.nodes):
            if n.name == name:
                return n

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
        ## Needs
        #
        needs = []
        needs_edges = []
        for n in getattr(operation, "op_needs", operation.needs):
            nkw, ekw = {}, {}
            if is_optional(n):
                ekw["optional"] = True
            if is_sfx(n):
                ekw["sideffect"] = nkw["sideffect"] = True
            if is_mapped(n):
                ekw["fn_kwarg"] = n.fn_kwarg
            needs.append((n, nkw))
            needs_edges.append((n, operation, ekw))
        graph.add_nodes_from(needs)
        graph.add_node(operation, **operation.node_props)
        graph.add_edges_from(needs_edges)

        ## Prepare inversed-aliases index, used
        #  to label edges reaching to aliased `provides`.
        #
        aliases = getattr(operation, "aliases", None)
        alias_sources = {v: src for src, v in aliases} if aliases else ()

        ## Provides
        #
        for n in getattr(operation, "op_provides", operation.provides):
            kw = {}
            if is_sfx(n):
                kw["sideffect"] = True
                graph.add_node(n, sideffect=True)

            if n in alias_sources:
                src_provide = alias_sources[n]
                kw["alias_of"] = src_provide

            graph.add_edge(operation, n, **kw)

    def _topo_sort_nodes(self, dag) -> List:
        """Topo-sort dag respecting operation-insertion order to break ties."""
        node_keys = dict(zip(dag.nodes, count()))
        return nx.lexicographical_topological_sort(dag, key=node_keys.get)

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
        log.info("... predicate filtered out %s.", [op.name for op in to_del])
        graph.remove_nodes_from(to_del)

    def _prune_graph(
        self, inputs: Items, outputs: Items, predicate: NodePredicate = None
    ) -> Tuple[nx.DiGraph, Collection, Collection, Collection]:
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
            a 3-tuple with the *pruned_dag* & the needs/provides resolved based
            on the given inputs/outputs
            (which might be a subset of all needs/outputs of the returned graph).

            Use the returned `needs/provides` to build a new plan.

        :raises ValueError:
            - if `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*
        """
        # TODO: break cycles here.
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
                broken_dag.remove_edges_from(list(broken_dag.in_edges(n)))

        # Drop stray input values and operations (if any).
        if outputs is not None:
            # If caller requested specific outputs, we can prune any
            # unrelated nodes further up the dag.
            ending_in_outputs = set()
            for output_name in outputs:
                ending_in_outputs.add(output_name)
                ending_in_outputs.update(nx.ancestors(dag, output_name))
            broken_dag = broken_dag.subgraph(ending_in_outputs)
            if log.isEnabledFor(logging.INFO) and len(broken_dag) != len(dag):
                log.info(
                    "... dropping irrelevant ops%s.",
                    [
                        op.name
                        for op in dag
                        if isinstance(op, Operation) and op not in ending_in_outputs
                    ],
                )

        # Prune unsatisfied operations (those with partial inputs or no outputs).
        unsatisfied = unsatisfied_operations(broken_dag, satisfied_inputs)
        if log.isEnabledFor(logging.INFO) and unsatisfied:
            log.info("... dropping unsatisfied ops%s.", [op.name for op in unsatisfied])
        # Clone it, to modify it.
        pruned_dag = dag.subgraph(broken_dag.nodes - unsatisfied).copy()
        # Clean unlinked data-nodes.
        pruned_dag.remove_nodes_from(list(nx.isolates(pruned_dag)))

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

        return pruned_dag, tuple(inputs), tuple(outputs)

    def _build_execution_steps(
        self, pruned_dag, inputs: Collection, outputs: Optional[Collection]
    ) -> List:
        """
        Create the list of operation-nodes & *instructions* evaluating all

        operations & instructions needed a) to free memory and b) avoid
        overwriting given intermediate inputs.

        :param pruned_dag:
            The original dag, pruned; not broken.
        :param outputs:
            outp-names to decide whether to add (and which) evict-instructions

        Instances of :class:`_EvictInstructions` are inserted in `steps` between
        operation nodes to reduce the memory footprint of solutions while
        the computation is running.
        An evict-instruction is inserted whenever a *need* is not used
        by any other *operation* further down the DAG.
        """

        steps = []

        def add_step_once(step):
            # For functions with repeated needs, like ['a', 'a'].
            if steps and step == steps[-1] and type(step) == type(steps[-1]):
                log.warning("Skipped dupe step %s in position %i.", step, len(steps))
            else:
                steps.append(step)

        ## Create an execution order such that each layer's needs are provided,
        #  respecting operation-insertion order to break ties;  which means that
        #  the first inserted operations win the `needs`, but
        #  the last ones win the `provides` (and the final solution).
        ordered_nodes = iset(self._topo_sort_nodes(pruned_dag))

        # Add Operations evaluation steps, and instructions to evict data.
        for i, node in enumerate(ordered_nodes):

            if isinstance(node, Operation):
                steps.append(node)

                # NO EVICTIONS when no specific outputs asked.
                if not outputs or is_skip_evictions():
                    continue

                # Add EVICT (1) for operation's needs.
                #
                # Broken links are irrelevant bc they are predecessors of data (provides),
                # but here we scan for predecessors of the operation (needs).
                #
                for need in pruned_dag.pred[node]:
                    # Do not evict asked outputs or sideffects.
                    if need in outputs:
                        continue

                    # A needed-data of this operation may be evicted if
                    # no future Operations needs it.
                    #
                    for future_node in ordered_nodes[i + 1 :]:
                        if (
                            isinstance(future_node, Operation)
                            and need in pruned_dag.pred[future_node]
                        ):
                            break
                    else:
                        add_step_once(_EvictInstruction(need))

                # Add EVICT (2) for unused operation's provides.
                #
                # A provided-data is evicted if no future operation needs it
                # (and is not an asked output).
                # It MUST use the broken dag, not to evict data
                # that will be pinned(?), but to populate overwrites with them.
                #
                # .. image:: doc/source/images/unpruned_useless_provides.svg
                #
                for provide in node.provides:
                    # Do not evict asked outputs or sideffects.
                    if provide not in outputs and provide not in pruned_dag.nodes:
                        add_step_once(_EvictInstruction(provide))

            else:
                assert isinstance(
                    node, str
                ), f"Unrecognized network graph node {node}: {type(node).__name__!r}"

        return steps

    def compile(
        self, inputs: Items = None, outputs: Items = None, predicate=None
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
        :param predicate:
            the :term:`node predicate` is a 2-argument callable(op, node-data)
            that should return true for nodes to include; if None, all nodes included.

        :return:
            the cached or fresh new :term:`execution plan`

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
        from .execution import ExecutionPlan

        ## Make a stable cache-key.
        #
        if inputs is not None:
            inputs = tuple(
                sorted(astuple(inputs, "inputs", allowed_types=abc.Collection))
            )
        if outputs is not None:
            outputs = tuple(
                sorted(astuple(outputs, "outputs", allowed_types=abc.Collection))
            )
        if not predicate:
            predicate = None

        cache_key = (inputs, outputs, predicate)

        ## Build (or retrieve from cache) execution plan
        #  for the given inputs & outputs.
        #
        if cache_key in self._cached_plans:
            log.debug("... cache-hit key: %s", cache_key)
            plan = self._cached_plans[cache_key]
        else:
            pruned_dag, needs, provides = self._prune_graph(inputs, outputs, predicate)
            steps = self._build_execution_steps(pruned_dag, needs, outputs or ())
            plan = ExecutionPlan(
                self,
                needs,
                provides,
                pruned_dag,
                tuple(steps),
                asked_outs=outputs is not None,
            )

            self._cached_plans[cache_key] = plan
            log.debug("... cache-updated key: %s", cache_key)

        return plan


def build_network(
    operations,
    rescheduled=None,
    endured=None,
    parallel=None,
    marshalled=None,
    node_props=None,
    renamer=None,
):
    """
    The :term:`network` factory that does :term:`operation merging` before constructing it.

    :param nest:
        see same-named param in :func:`.compose`
    """
    kw = {
        k: v for k, v in locals().items() if v is not None and k not in ("operations")
    }

    from boltons.setutils import IndexedSet as iset
    from .composition import NULL_OP, Pipeline

    def proc_op(op, parent=None):
        """clone FuncOperation with certain props changed"""
        assert isinstance(op, FunctionalOperation), op

        ## Convey any node-props specified in the pipeline here
        #  to all sub-operations.
        #
        if kw:
            if node_props:
                kw["node_props"] = {**op.node_props, **node_props}

            if callable(renamer):

                def parent_wrapper(ren_args: RenArgs) -> str:
                    # Provide RenArgs.parent.
                    return renamer(ren_args._replace(parent=parent))

                kw["renamer"] = parent_wrapper
            op = op = op.withset(**kw)

        return op

    merge_set = iset()  # Preseve given node order.
    for op in operations:
        if isinstance(op, Pipeline):
            merge_set.update(proc_op(s, op) for s in op.ops)
        else:
            merge_set.add(proc_op(op))
    merge_set = iset(i for i in merge_set if not isinstance(i, NULL_OP))

    assert all(bool(n) for n in merge_set)
    net = Network(*merge_set)

    return net
