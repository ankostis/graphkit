# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
""":term:`execute` the :term:`plan` to derrive the :term:`solution`."""
import logging
import random
import sys
import time
from collections import ChainMap, abc, defaultdict, namedtuple
from contextvars import ContextVar, copy_context
from functools import partial
from itertools import chain
from typing import Any, Callable, Collection, List, Mapping, Optional, Tuple, Union

import networkx as nx
from boltons.setutils import IndexedSet as iset

from .base import (
    UNSET,
    AbortedException,
    IncompleteExecutionError,
    Items,
    Operation,
    PlotArgs,
    Plottable,
    aslist,
    astuple,
    first_solid,
)
from .config import (
    get_execution_pool,
    is_abort,
    is_debug,
    is_endure_operations,
    is_layered_solution,
    is_marshal_tasks,
    is_parallel_tasks,
    is_reschedule_operations,
    is_skip_evictions,
)
from .jetsam import save_jetsam
from .modifier import (
    acc_contains,
    acc_delitem,
    acc_getitem,
    acc_setitem,
    dep_singularized,
    dep_stripped,
    get_accessor,
    get_jsonp,
    is_sfx,
)
from .planning import (
    OpMap,
    unsatisfied_operations,
    yield_chaindocs,
    yield_node_names,
    yield_ops,
)

#: If this logger is *eventually* DEBUG-enabled,
#: the string-representation of network-objects (network, plan, solution)
#: is augmented with children's details.
log = logging.getLogger(__name__)


def _isDebugLogging():
    return log.isEnabledFor(logging.DEBUG)


def _update_outputs_grouped_by_accessor(op_layer, outputs: Mapping):
    """
    Mass update values on the :term:`solution layer` for the given `op`

    ... respecting any :attr:`._accessor.update` term:`accessor`\\s.

    :param op_layer:
        where to update values
    :param outputs:
        what to update
    """
    accessors = [get_accessor(o) for o in outputs]
    if any(accessors):
        ## Group out-values by their `update` accessor (or None).
        update_groups = defaultdict(list)
        for ac, kv in zip(accessors, outputs.items()):
            update_groups[ac and ac.update].append(kv)

        ## Values without :attr:`._accessor.update` are to be set one-by-one,
        #  further below.
        outputs = update_groups.pop(None, None)

        for upd, pairs in update_groups.items():
            upd(op_layer, pairs)

    if outputs:
        op_layer.update(outputs)


class Solution(ChainMap, Plottable):
    """
    The :term:`solution` chain-map and execution state (e.g. :term:`overwrite` or :term:`canceled operation`)

    It inherits :class:`collections.ChainMap`, to keep a separate dictionary
    for each operation executed, +1 for the user inputs.
    """

    #: Command :term:`solution layer`, by
    #:  default, false if not any jsonp in dependencies.
    #:
    #: See :attr:`executed` below
    is_layered: bool
    #: A dictionary with keys the operations executed,
    #: and values their :term:`layer`, or status:
    #:
    #: - no key:                not executed yet
    #: - value == dict:         execution ok, produced those outputs
    #: - value == Exception:    execution failed
    #:
    #: Keys are ordered as operations executed (last, most recently executed).
    #:
    #: When :attr:`is_layered`, its value-dicts are inserted, in reverse order,
    #: into my :attr:`maps` (from chain-map).
    executed: OpMap = {}
    #: A map of {rescheduled operation -> dynamically pruned ops, downstream}.
    broken: Mapping[Operation, Operation] = {}
    #: A {op, prune-explanation} dictionary with :term:`canceled operation`\\s
    #: due to upstream failures.
    canceled: OpMap = {}
    elapsed_ms = {}
    #: A unique identifier to distinguish separate flows in execution logs.
    solid: str
    # FIXME: SPURIOUS dag reversals on multi-threaded runs (see below next assertion)!
    #: Cloned from `plan` will be modified, by removing the downstream edges of:
    #:
    #: - any partial outputs not provided, or
    #: - all `provides` of failed operations.
    dag: nx.DiGraph
    #: the plan that produced this solution
    plan = "ExecutionPlan"
    # optimization for the expensive :attr:`.overwrites` dictionary
    _overwrites_cache = None

    def __init__(
        self,
        plan,
        input_values: dict,
        callbacks: Tuple[Callable[["OpCb"], None], Callable[["OpCb"], None]] = None,
        is_layered=None,
    ):
        super().__init__(input_values)
        self.callbacks = callbacks
        is_layered = first_solid(is_layered_solution(), is_layered)

        ##: By default, disable layers if network contains :term:`jsonp` dependencies.
        #
        if is_layered is None:
            is_layered = not any(get_jsonp(d) for d in plan.net.data)
        self.is_layered = is_layered

        #: Preserve name-list to account overwrites when non-:term:`layer`\ed`.
        self._initial_inputs = input_values if self.is_layered else dict(input_values)

        self.plan = plan
        self.executed: Mapping[Operation, dict] = {}
        self.canceled = {}
        self.broken = {}
        self.elapsed_ms = {}
        self.solid = "%X" % random.randint(0, 2 ** 16)

        ## Cache context-var flags.
        #
        self.is_endurance = is_endure_operations()
        self.is_reschedule = is_reschedule_operations()
        self.is_parallel = is_parallel_tasks()
        self.is_marshal = is_marshal_tasks()

        # FIXME: SPURIOUS dag reversals on multi-threaded runs (see below next assertion)!
        self.dag = plan.dag.copy()
        # assert next(iter(dag.edges))[0] == next(iter(plan.dag.edges))[0]:

    def copy(self):
        """Deep-copy user's `input_data` and pass the rest into a new Solution. """
        named_inputs = dict(self.maps[-1])
        clone = type(self)(
            self.plan,
            named_inputs,
            self.callbacks,
            # Specially handled below bc user's `layered_solution` arg is lost.
            is_layered=self.is_layered,
        )

        ## replicate "layers"" machinery.
        #
        props = (
            "is_layered is_endurance is_reschedule is_parallel is_marshal dag"
            " _initial_inputs executed canceled broken elapsed_ms"
        ).split()

        for p in props:
            val = getattr(self, p)
            if isinstance(val, dict):
                val = dict(val)
            setattr(clone, p, val)

        ## Replicate layer setup in constructor, here
        #
        executed_ok = reversed(self.layers) if self.is_layered else ()
        clone.maps = [*executed_ok, named_inputs]

        return clone

    __copy__ = copy

    def __repr__(self):
        if is_debug():
            return self.debugstr()
        else:
            items = ", ".join(f"{k!r}: {v!r}" for k, v in self.items())
            return f"{{{items}}}"

    def debugstr(self):
        return (
            f"{type(self).__name__}({dict(self)}, {self.plan}, {self.executed}, "
            f"is_layered={self.is_layered})"
        )

    @property
    def layers(self) -> List[OpMap]:
        """Outputs by operation, in execution order (last, most recently executed). """
        return [v for v in self.executed.values() if not isinstance(v, Exception)]

    def _reschedule(self, dag, reason, op):
        """
        Re-prune dag, and then update and return any newly-canceled ops.

        :param dag:
            The dag to discover :term:`unsatisfied operation`\\s from.
        :param reason:
            for logging
        :param op:
            for logging
        """
        canceled, _sorted_nodes = unsatisfied_operations(
            dag,
            [
                i
                for i in self
                # don't send canceled SFXs as Inputs.
                if not is_sfx(i) or self.get(i, True)
            ],
        )
        # Minus executed, bc partial-out op might not have any provides left.
        newly_canceled = canceled.keys() - self.canceled.keys() - self.executed.keys()
        self.canceled.update((k, canceled[k]) for k in newly_canceled)

        if log.isEnabledFor(logging.INFO):
            log.info(
                "... (%s) +%s  newly CANCELED ops%s due to %s op(%s).",
                self.solid,
                len(newly_canceled),
                [n.name for n in newly_canceled],
                reason,
                op.name,
            )

    def __contains__(self, key):
        acc = acc_contains(key)
        return any(acc(m, key) for m in self.maps)

    def __getitem__(self, key):
        acc = acc_getitem(key)
        for mapping in self.maps:
            try:
                return acc(mapping, key)
            except KeyError:
                pass
        return self.__missing__(key)

    def __setitem__(self, key, val):
        self._overwrites_cache = None
        super().__setitem__(key, val)

    def __delitem__(self, key):
        self._overwrites_cache = None

        acc = acc_contains(key)
        matches = [m for m in self.maps if acc(m, key)]
        if not matches:
            raise KeyError(key)

        acc = acc_delitem(key)
        for m in matches:
            acc(m, key)

        ## Delete it from extra places when non-layered.
        #
        if not self.is_layered:
            for m in self.layers:
                m.pop(key, None)

            self._initial_inputs.pop(key, None)

    def _populate_op_layer_with_outputs(self, op, outputs) -> dict:
        """
        Populate a new layer for `op` with `outputs` , or use `named_inputs` (if non-layered).
        """
        self._overwrites_cache = None

        if self.is_layered:
            op_layer = {}
            self.maps.insert(0, op_layer)
            self.executed[op] = op_layer
        else:
            op_layer = self.maps[-1]
            # Update just they keys, the values are in `input_names`.
            self.executed[op] = outputs

        if outputs:
            _update_outputs_grouped_by_accessor(op_layer, outputs)

    def operation_executed(self, op, outputs):
        """
        Invoked once per operation, with its results.

        It will update :attr:`executed` with the operation status and
        if `outputs` were partials, it will update :attr:`canceled`
        with the unsatisfied ops downstream of `op`.

        :param op:
            the operation that completed ok
        :param outputs:
            The named values the op` actually produced,
            which may be a subset of its `provides`.  Sideffects are not considered.

        """

        def collect_canceled_sideffects(dep, val) -> Collection:
            """yield any sfx `dep` with falsy value, singularizing sideffected."""
            if val or not is_sfx(dep):
                return ()
            return dep_singularized(dep)

        self._populate_op_layer_with_outputs(op, outputs)
        if first_solid(self.is_reschedule, getattr(op, "rescheduled", None)):
            ## Find which provides have been broken?
            #
            # OPTIMIZE: could use _fn_provides
            missing_outs = iset(op.provides) - set(outputs)
            sfx = (out for out in missing_outs if is_sfx(out))
            canceled_sideffects = [
                sf
                for k, v in outputs.items()
                for sf in collect_canceled_sideffects(k, v)
            ]
            outs_to_break = (missing_outs - sfx) | canceled_sideffects
            log.info(
                "... (%s) missing partial outputs %s from rescheduled %s.",
                self.solid,
                list(outs_to_break),
                op,
            )

            if outs_to_break:
                dag = self.dag
                dag.remove_edges_from((op, out) for out in outs_to_break)
                self._reschedule(dag, "rescheduled", op)
                # list used by `check_if_incomplete()`
                self.broken[op] = outs_to_break

    def operation_failed(self, op, ex):
        """
        Invoked once per operation, with its results.

        It will update :attr:`executed` with the operation status and
        the :attr:`canceled` with the unsatisfied ops downstream of `op`.
        """
        dag = self.dag
        self.executed[op] = ex
        dag.remove_edges_from(tuple(dag.out_edges(op)))
        self._reschedule(dag, "failure of", op)

    def is_failed(self, op):
        """returns Non(not executed), False(ok), Exception(failed)"""
        exe = self.executed.get(op)
        return exe and isinstance(exe, Exception) and exe

    @property
    def overwrites(self) -> Mapping[Any, List]:
        """
        The data in the solution that exist more than once (refreshed on every call).

        A "virtual" property to a dictionary with keys the names of values that
        exist more than once, and values, all those values in a list, ordered
        in reverse compute order (1st is the last one computed, last (any) given-inputs).
        """
        if self._overwrites_cache is not None:
            return self._overwrites_cache

        if self.is_layered:
            maps = self.maps
        else:
            maps = [*reversed(self.layers), self._initial_inputs]
        dd = defaultdict(list)
        for d in maps:
            for k, v in d.items():
                dd[k].append(v)

        return {
            # On a non-layered real dupe-calc (not calcing an input)
            # overwrites would be +1 due to last `named_inputs` dict.
            k: v
            for k, v in dd.items()
            if len(v) > 1
        }

    def check_if_incomplete(self) -> Optional[IncompleteExecutionError]:
        """Return a :class:`IncompleteExecutionError` if `pipeline` operations failed/canceled. """
        failures = {
            op: ex for op, ex in self.executed.items() if isinstance(ex, Exception)
        }
        incomplete = iset(chain(self.canceled, failures.keys()))
        if incomplete:
            incomplete = list(yield_node_names(incomplete))
            partial_msgs = {
                f"\n  +--{op.name}: {list(pouts)}" for op, pouts in self.broken.items()
            }
            err_msgs = [
                f"\n  +--{op.name}: {type(ex).__name__}('{ex}')"
                for op, ex in failures.items()
            ]
            msg = (
                f"Not completed x{len(incomplete)} operations {list(incomplete)}"
                f" due to x{len(failures)} failures and x{len(partial_msgs)} partial-ops:"
                f"{''.join(err_msgs)}{''.join(partial_msgs)}"
            )
            return IncompleteExecutionError(msg, self)

    def scream_if_incomplete(self):
        """Raise a :class:`IncompleteExecutionError` if `pipeline` operations failed/canceled. """
        ex = self.check_if_incomplete()
        if ex:
            raise ex

    @property
    def graph(self):
        return self.dag

    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        """delegate to plan, with solution"""
        name = f"solution-x{len(self.plan.net.graph.nodes)}-nodes"
        plot_args = plot_args.with_defaults(name=name, solution=self)
        plot_args = self.plan.prepare_plot_args(plot_args)
        plot_args = plot_args._replace(plottable=self)

        return plot_args


#: Argument passed in a :term:`callbacks` callable before executing each operation,
#: and contains fields to identify the operation call:
#:
#: :param op: the operation about to be computed;
#: :param sol: the slution (might be just a plain dict if it :term:`marshalling`);
#: :param solid: the operation identity, needed if `sol` is a plain dict,
OpCb = namedtuple("OpCb", "op, sol, solid")


class _OpTask:
    """
    Mimic :class:`concurrent.futures.Future` for :term:`sequential` execution.

    This intermediate class is needed to solve pickling issue with process executor.
    """

    __slots__ = ("op", "sol", "solid", "callbacks", "result")
    logname = __name__

    def __init__(self, op, sol, solid, callbacks=None, result=UNSET):
        self.op = op
        self.sol = sol
        self.solid = solid

        ## Make callbacks a 2-tuple with possible None callables.
        #
        if callbacks is None:
            callbacks = (None, None)
        elif callable(callbacks):
            callbacks = (callbacks, None)
        else:
            callbacks = tuple(callbacks)
            if len(callbacks) < 2:
                callbacks = (*callbacks, None)
        self.callbacks = callbacks
        self.result = result

    def marshalled(self):
        import dill

        return dill.dumps(self)

    def __call__(self):
        if self.result == UNSET:
            self.result = None
            log = logging.getLogger(self.logname)
            log.debug("+++ (%s) Executing %s...", self.solid, self)
            token = task_context.set(self)
            callbacks = self.callbacks
            try:
                if callbacks[0]:
                    callbacks[0](OpCb(self.op, self.sol, self.solid))
                self.result = self.op.compute(self.sol)
                if callbacks[1]:
                    callbacks[1](OpCb(self.op, self.sol, self.solid))
            finally:
                task_context.reset(token)

        return self.result

    get = __call__

    def __repr__(self):
        try:
            sol_items = list(self.sol)
        except Exception:
            sol_items = type(self.sol).__name__
        return f"OpTask({self.op}, sol_keys={sol_items!r})"


def _do_task(task):
    """
    Un-dill the *simpler* :class:`_OpTask` & Dill the results, to pass through pool-processes.

    See https://stackoverflow.com/a/24673524/548792
    """
    ## Note, the "else" case is only for debugging aid,
    #  by skipping `_OpTask.marshal()`` call.
    #
    if isinstance(task, bytes):
        import dill

        task = dill.loads(task)
        result = copy_context().run(task)
        result = dill.dumps(result)
    else:
        result = task()

    return result


class ExecutionPlan(
    namedtuple("ExecPlan", "net needs provides dag steps asked_outs comments"),  # noqa
    Plottable,
):
    """
    A pre-compiled list of operation steps that can :term:`execute` for the given inputs/outputs.

    It is the result of the network's :term:`planning` phase.

    Note the execution plan's attributes are on purpose immutable tuples.

    .. attribute:: net

        The parent :class:`Network`
    .. attribute:: needs

        An :class:`.IndexedSet` with the input names needed to exist in order to produce all `provides`.
    .. attribute:: provides

        An :class:`.IndexedSet` with the outputs names produces when all `inputs` are given.
    .. attribute:: dag

        The regular (not broken) *pruned* subgraph of net-graph.
    .. attribute:: steps

        The tuple of operation-nodes & *instructions* needed to evaluate
        the given inputs & asked outputs, free memory and avoid overwriting
        any given intermediate inputs.
    .. attribute:: asked_outs

        When true, :term:`eviction`\\s may kick in (unless disabled by :term:`configurations`),
        otherwise, *evictions* (along with prefect-evictions check) are skipped.
    .. attribute:: comments

        an {op, prune-explanation} dictionary
    """

    @property
    def graph(self):
        return self.dag

    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        plot_args = plot_args.clone_or_merge_graph(self.net.graph)
        graph = plot_args.graph

        clusters = None
        if self.dag.nodes != graph.nodes:
            clusters = {n: "after pruning" for n in self.dag.nodes}

        plot_args = plot_args.with_defaults(
            name=f"plan-x{len(graph.nodes)}-nodes",
            steps=self.steps,
            inputs=self.needs,
            outputs=self.provides,
            clusters=clusters,
        )
        plot_args = plot_args._replace(plottable=self)

        return plot_args

    def __repr__(self):
        needs = aslist(self.needs, "needs")
        provides = aslist(self.provides, "provides")
        steps = self.steps or ()
        steps_str = (
            "".join(f"\n  +--{s}" for s in steps)
            if is_debug()
            else ", ".join(yield_node_names(steps))
        )
        comments = (
            (f"\n  +--prune-comments: {self.comments}" if is_debug() else "")
            if self.comments
            else ""
        )

        return (
            f"ExecutionPlan(needs={needs}, provides={provides}"
            f", x{len(steps)} steps: {steps_str}{comments})"
        )

    def validate(self, inputs: Items = UNSET, outputs: Items = UNSET):
        """
        Scream on invalid inputs, outputs or no operations in graph.

        :param inputs:
            the inputs that this plan was :term:`compile`\\d for, or MORE;
            will scream if LESS...
        :param outputs:
            the outputs that this plan was :term:`compile`\\d for, or LESS;
            will scream if MORE...

        :raises ValueError:
            *Unsolvable graph...*
                if it cannot produce any `outputs` from the given `inputs`.
            *Plan needs more inputs...*
                if given `inputs` mismatched plan's :attr:`needs`.
            *Unreachable outputs...*
                if net cannot produce asked `outputs`.

        """
        if not self.dag:
            raise ValueError(
                f"Unsolvable graph:\n  +--{self.net}"
                f"\n  +--possible inputs: {list(self.net.needs)}"
                f"\n  +--possible outputs: {list(self.net.provides)}"
            )

        if inputs is UNSET:
            inputs = self.needs
        if outputs is UNSET:
            outputs = self.provides

        # Check plan<-->inputs mismatch.
        #
        missing = iset(self.needs) - set(inputs)
        if missing:
            raise ValueError(
                f"Plan needs more inputs: {list(missing)}"
                f"\n  given inputs: {list(inputs)}\n  {self}"
            )

        if outputs:
            unknown = (
                iset(astuple(outputs, "outputs", allowed_types=abc.Sequence))
                - self.provides
            )
            if unknown:
                raise ValueError(
                    f"Unreachable outputs {list(unknown)}\n  for given inputs {list(unknown)}"
                    f"\n for graph: {self}\n  {self}"
                )

    def _check_if_aborted(self, solution):
        if is_abort():
            raise AbortedException(solution)

    def _prepare_tasks(
        self, operations, solution, pool, global_parallel, global_marshal
    ) -> Union["Future", _OpTask, bytes]:
        """
        Combine ops+inputs, apply :term:`marshalling`, and submit to :term:`execution pool` (or not) ...

         based on global/pre-op configs.
        """
        ## Selectively DILL the *simpler* _OpTask & `sol` dict
        #  so as to pass through pool-processes,
        #  (s)ee https://stackoverflow.com/a/24673524/548792)
        #  and handle results in this thread, to evade Solution locks.
        #
        input_values = dict(solution)

        def prep_task(op):
            ok = False
            try:
                # Mark start time here, to include also marshalling overhead.
                solution.elapsed_ms[op] = time.time()

                task = _OpTask(op, input_values, solution.solid, solution.callbacks)
                if first_solid(global_marshal, getattr(op, "marshalled", None)):
                    task = task.marshalled()

                if first_solid(global_parallel, getattr(op, "parallel", None)):
                    if not pool:
                        raise RuntimeError(
                            "With `parallel` you must `set_execution_pool().`"
                        )

                    task = pool.apply_async(_do_task, (task,))
                elif isinstance(task, bytes):
                    # Marshalled (but non-parallel) tasks still need `_do_task()`.
                    task = partial(_do_task, task)
                    task.get = task.__call__

                ok = True
                return task
            finally:
                if not ok:
                    ex = sys.exc_info()[1]
                    save_jetsam(ex, locals(), "task", plan="self")

        return [prep_task(op) for op in operations]

    def _handle_task(self, future, op, solution) -> None:
        """Un-dill parallel task results (if marshalled), and update solution / handle failure."""

        def elapsed_ms(op):
            t0 = solution.elapsed_ms[op]
            solution.elapsed_ms[op] = elapsed = round(1000 * (time.time() - t0), 3)

            return elapsed

        try:
            ## Reset start time for Sequential tasks
            #  (bummer, they will miss marshalling overhead).
            #
            if isinstance(future, _OpTask):
                solution.elapsed_ms[op] = time.time()

            outputs = future.get()
            if isinstance(outputs, bytes):
                import dill

                outputs = dill.loads(outputs)

            solution.operation_executed(op, outputs)

            elapsed = elapsed_ms(op)
            log.info(
                "... (%s) op(%s) completed in %sms.", solution.solid, op.name, elapsed
            )
        except Exception as ex:
            is_endured = first_solid(
                solution.is_endurance, getattr(op, "endured", None)
            )
            elapsed = elapsed_ms(op)
            loglevel = logging.WARNING if is_endured else logging.ERROR
            log.log(
                loglevel,
                "... (%s) %s op(%s) FAILED in %0.3fms, due to: %s(%s)"
                "\n  x%i ops executed so far: %s",
                solution.solid,
                "*Enduring* " if is_endured else "",
                op.name,
                elapsed,
                type(ex).__name__,
                ex,
                len(solution.executed),
                list(solution.executed),
                exc_info=is_debug(),
            )

            if is_endured:
                solution.operation_failed(op, ex)
            else:
                # Although `plan` have added to jetsam in `compute()``,
                # add it again, in case compile()/execute() is called separately.
                save_jetsam(ex, locals(), "solution", task="future", plan="self")
                raise

    def _execute_thread_pool_barrier_method(self, solution: Solution):
        """
        (deprecated)  This method runs the graph using a parallel pool of thread executors.
        You may achieve lower total latency if your graph is sufficiently
        sub divided into operations using this method.

        :param solution:
            must contain the input values only, gets modified
        """
        pool = get_execution_pool()  # cache pool
        parallel = solution.is_parallel
        marshal = solution.is_marshal

        # with each loop iteration, we determine a set of operations that can be
        # scheduled, then schedule them onto a thread pool, then collect their
        # results onto a memory solution for use upon the next iteration.
        while True:
            ## Note: do not check abort in between task handling (at the bottom),
            #  or it would ignore solution updates from already executed tasks.
            self._check_if_aborted(solution)

            # the upnext list contains a list of operations for scheduling
            # in the current round of scheduling
            upnext = []
            # TODO: optimization: start batches from previous last op).
            for node in self.steps:
                ## Determines if a Operation is ready to be scheduled for execution
                #  based on what has already been executed.
                if (
                    isinstance(node, Operation)
                    and node not in solution.executed
                    and set(yield_ops(nx.ancestors(self.dag, node))).issubset(
                        solution.executed
                    )
                ):
                    if node not in solution.canceled:
                        upnext.append(node)
                elif isinstance(node, str):
                    # Only evict if all successors for the data node
                    # have been executed.
                    if (
                        # An optional need may not have a value in the solution.
                        node in solution
                        and (
                            # The 2nd eviction branch for unused provides.
                            node not in self.dag.nodes
                            # Scan node's successors in `broken_dag`, not to block
                            # an op waiting for calced data already given as input.
                            or set(self.dag.successors(node)).issubset(
                                solution.executed
                            )
                        )
                    ):
                        if log.isEnabledFor(logging.INFO):
                            log.info(
                                "... (%s) evicting '%s' from solution%s.",
                                solution.solid,
                                node,
                                list(solution),
                            )
                        del solution[node]

            # stop if no nodes left to schedule, exit out of the loop
            if not upnext:
                ## Re-check evictions and assume all ops executed.
                #  Sequenced executor has no such problem bc exhausts steps.
                #
                #  TODO: evictions for parallel are wasting loops.
                #
                for node in self.steps:
                    if isinstance(node, str) and node in solution:
                        del solution[node]
                break

            if _isDebugLogging():
                log.debug(
                    "+++ (%s) Parallel batch%s on solution%s.",
                    solution.solid,
                    list(op.name for op in upnext),
                    list(solution),
                )
            tasks = self._prepare_tasks(upnext, solution, pool, parallel, marshal)

            ## Handle results.
            #
            for op, task in zip(upnext, tasks):
                self._handle_task(task, op, solution)

    def _execute_sequential_method(self, solution: Solution):
        """
        This method runs the graph one operation at a time in a single thread

        :param solution:
            must contain the input values only, gets modified
        """
        for step in self.steps:
            self._check_if_aborted(solution)

            if isinstance(step, Operation):
                if step in solution.canceled:
                    continue

                task = _OpTask(step, solution, solution.solid, solution.callbacks)
                self._handle_task(task, step, solution)

            elif isinstance(step, str):
                # Cache value may be missing if it is optional.
                if step in solution:
                    log.info(
                        "... (%s) evicting '%s' from solution%s.",
                        solution.solid,
                        step,
                        list(solution),
                    )
                    del solution[step]

            else:
                raise AssertionError(f"Unrecognized instruction.{step}")

    def execute(
        self,
        named_inputs,
        outputs=None,
        *,
        name="",
        callbacks: Callable[[OpCb], None] = None,
        solution_class=None,
        layered_solution=None,
    ) -> Solution:
        """
        :param named_inputs:
            A mapping of names --> values that must contain at least
            the compulsory inputs that were specified when the plan was built
            (but cannot enforce that!).
            Cloned, not modified.
        :param outputs:
            If not None, they are just checked if possible, based on :attr:`provides`,
            and scream if not.
        :param name:
            name of the pipeline used for logging
        :param callbacks:
            If given, a 2-tuple with (optional) x2 :term:`callbacks` to call before & after
            each operation, with :class:`.OpCb` as argument containing the op & solution.
            Less or no elements accepted.
        :param solution_class:
            a custom solution factory to use
        :param layered_solution:
            whether to store operation results into separate :term:`solution layer`

            Unless overridden by a True/False in :func:`.set_layered_solution`
            of :term:`configurations`, it accepts the following values:

            - When True(False), always keep(don't keep) results in a separate layer for each operation,
              regardless of any *jsonp* dependencies.
            - If ``None``, layers are used only if there are NO :term:`jsonp` dependencies
              in the network.

        :return:
            The :term:`solution` which contains the results of each operation executed
            +1 for inputs in separate dictionaries.

        :raises ValueError:
            *Unsolvable graph...*
                if it cannot produce any `outputs` from the given `inputs`.
            *Plan needs more inputs...*
                if given `inputs` mismatched plan's :attr:`needs`.
            *Unreachable outputs...*
                if net cannot produce asked `outputs`.
        """
        ok = False
        try:
            self.validate(named_inputs, outputs)
            dag = self.dag  # locals opt

            ## Choose a method of execution
            #
            in_parallel = is_parallel_tasks() or any(
                getattr(op, "parallel", None) for op in yield_ops(self.steps)
            )
            executor = (
                self._execute_thread_pool_barrier_method
                if in_parallel
                else self._execute_sequential_method
            )

            # If certain outputs asked, put relevant-only inputs in solution,
            # otherwise, keep'em all.
            #
            evict = self.asked_outs and not is_skip_evictions()
            # Note: clone and keep original `inputs` in the 1st chained-map.

            if solution_class is None:
                solution_class = Solution

            solution = solution_class(
                self,
                {k: v for k, v in named_inputs.items() if k in dag.nodes}
                if evict
                else named_inputs,
                callbacks,
                is_layered=layered_solution,
            )

            log.info(
                "=== (%s) Executing pipeline(%s)%s%s, on inputs%s, according to %s...",
                solution.solid,
                name,
                ", in parallel" if in_parallel else "",
                ", evicting" if evict else "",
                list(solution),
                self,
            )

            ok2 = False
            try:
                executor(solution)
                ok2 = True
            finally:
                ## Log cumulative operations elapsed time.
                #
                if log.isEnabledFor(logging.INFO):
                    elapsed = sum(solution.elapsed_ms.values())
                    log.info(
                        "=== (%s) %s pipeline(%s) in %0.3fms.",
                        solution.solid,
                        "Completed" if ok2 else "FAILED",
                        name,
                        elapsed,
                    )

            # Validate eviction was perfect
            #
            if evict:
                expected_provides = set()
                expected_provides.update(
                    yield_chaindocs(dag, self.provides, expected_provides)
                )
                expected_provides = set(dep_stripped(n) for n in expected_provides)
                # It is a proper subset when not all outputs calculated.
                assert set(solution).issubset(expected_provides), (
                    f"Evictions left more data{list(iset(solution) - set(self.provides))} than {self}!"
                    '\n  (hint: did you bypass "impossible-outputs" validation?)'
                    "\n  (tip: enable DEBUG-logging and/or set GRAPHTIK_DEBUG envvar to investigate)"
                )

            ok = True
            return solution
        finally:
            if not ok:
                ex = sys.exc_info()[1]
                save_jetsam(ex, locals(), "solution")
