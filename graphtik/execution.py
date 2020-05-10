# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
""":term:`execute` the :term:`plan` to derrive the :term:`solution`."""
import logging
import random
import time
from collections import ChainMap, abc, defaultdict, namedtuple
from functools import partial
from itertools import chain
from typing import Any, Collection, List, Mapping, Optional, Tuple, Union

import networkx as nx
from boltons.setutils import IndexedSet as iset

from .base import (
    UNSET,
    AbortedException,
    IncompleteExecutionError,
    Items,
    aslist,
    astuple,
    first_solid,
    jetsam,
)
from .config import (
    get_execution_pool,
    is_abort,
    is_debug,
    is_endure_operations,
    is_marshal_tasks,
    is_parallel_tasks,
    is_reschedule_operations,
    is_skip_evictions,
)
from .modifiers import dep_singularized, dep_stripped, is_sfx
from .network import (
    _EvictInstruction,
    unsatisfied_operations,
    yield_node_names,
    yield_ops,
)
from .op import Operation, PlotArgs, Plottable

#: If this logger is *eventually* DEBUG-enabled,
#: the string-representation of network-objects (network, plan, solution)
#: is augmented with children's details.
log = logging.getLogger(__name__)


def _isDebugLogging():
    return log.isEnabledFor(logging.DEBUG)


class Solution(ChainMap, Plottable):
    """
    The :term:`solution` chain-map and execution state (e.g. :term:`overwrite` or :term:`canceled operation`)
    """

    def __init__(self, plan, input_values: dict):
        #: An ordered mapping of plan-operations to their results
        #: (initially empty dicts).
        #: The result dictionaries pre-populate this (self) chainmap,
        #: with the 1st map (wins all reads) the last operation,
        #: the last one the `input_values` dict.
        self._layers = {op: {} for op in yield_ops(reversed(plan.steps))}
        super().__init__(*self._layers.values(), input_values)

        #: the plan that produced this solution
        self.plan = plan
        #: A dictionary with keys the operations executed, and values their status:
        #:
        #: - no key:            not executed yet
        #: - value None:        execution ok
        #: - value Exception:   execution failed
        #: - value Collection:  canceled provides
        self.executed = {}
        #: A sorted set of :term:`canceled operation`\\s due to upstream failures.
        self.canceled = iset()  # not iterated, order not important, but ...
        #: a flag controlled by `plan` (by invoking :meth:`finalized` is invoked)
        #: which becomes `True` when this instance has finished accepting results.
        self.finalized = False
        self.elapsed_ms = {}
        #: A unique identifier to distinguish separate flows in execution logs.
        self.solid = "%X" % random.randint(0, 2 ** 16)

        ## Cache context-var flags.
        #
        self.is_endurance = is_endure_operations()
        self.is_reschedule = is_reschedule_operations()
        self.is_parallel = is_parallel_tasks()
        self.is_marshal = is_marshal_tasks()

        #: Cloned from `plan` will be modified, by removing the downstream edges of:
        #:
        #: - any partial outputs not provided, or
        #: - all `provides` of failed operations.
        #:
        #: FIXME: SPURIOUS dag reversals on multi-threaded runs (see below next assertion)!
        self.dag = plan.dag.copy()
        # assert next(iter(dag.edges))[0] == next(iter(plan.dag.edges))[0]:

    def __copy__(self):
        clone = type(self)(self.plan, {})
        props = (
            "maps executed canceled finalized elapsed_ms solid _layers"
            " is_endurance is_reschedule is_parallel is_marshal dag"
        ).split()
        for p in props:
            setattr(clone, p, getattr(self, p))

        return clone

    def __repr__(self):
        if is_debug():
            return self.debugstr()
        else:
            items = ", ".join(f"{k!r}: {v!r}" for k, v in self.items())
            return f"{{{items}}}"

    def debugstr(self):
        return f"{type(self).__name__}({dict(self)}, {self.plan}, {self.executed}, {self._layers})"

    def _reschedule(self, dag, nbunch_to_break, op):
        """Update dag/canceled/executed ops and return newly-canceled ops. """
        dag.remove_edges_from(tuple(dag.out_edges(nbunch_to_break)))
        canceled = unsatisfied_operations(dag, self)
        # Minus executed, bc partial-out op might not have any provides left.
        newly_canceled = iset(canceled) - self.canceled - self.executed
        self.canceled.update(newly_canceled)

        if newly_canceled and log.isEnabledFor(logging.INFO):
            reason = (
                "failure of"
                if isinstance(nbunch_to_break, Operation) > 1
                else "rescheduled"
            )
            log.info(
                "... (%s) CANCELING +%s ops%s due to %s of op(%s).",
                self.solid,
                len(newly_canceled),
                [n.name for n in newly_canceled],
                reason,
                op.name,
            )

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

        assert not self.finalized, f"Cannot reuse solution: {self}"
        self._layers[op].update(outputs)
        self.executed[op] = None

        if first_solid(self.is_reschedule, op.rescheduled):
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
            to_break = (missing_outs - sfx) | canceled_sideffects
            log.info(
                "... (%s) missing partial outputs %s from rescheduled %s.",
                self.solid,
                list(to_break),
                op,
            )

            if to_break:
                self._reschedule(self.dag, to_break, op)
                # list used by `check_if_incomplete()`
                self.executed[op] = to_break

    def operation_failed(self, op, ex):
        """
        Invoked once per operation, with its results.

        It will update :attr:`executed` with the operation status and
        the :attr:`canceled` with the unsatisfied ops downstream of `op`.
        """
        assert not self.finalized, f"Cannot reuse solution: {self}"
        self.executed[op] = ex
        self._reschedule(self.dag, op, op)

    def finalize(self):
        """invoked only once, after all ops have been executed"""
        # Invert solution so that last value wins
        if not self.finalized:
            self.finalized = True

    def __delitem__(self, key):
        for d in self.maps:
            d.pop(key, None)

    def is_failed(self, op):
        return isinstance(self.executed.get(op), Exception)

    @property
    def overwrites(self) -> Mapping[Any, List]:
        """
        The data in the solution that exist more than once.

        A "virtual" property to a dictionary with keys the names of values that
        exist more than once, and values, all those values in a list, ordered:

        - before :meth:`finalized()`, as computed;
        - after :meth:`finalized()`, in reverse.
        """
        maps = self.maps
        dd = defaultdict(list)
        for d in maps:
            for k, v in d.items():
                dd[k].append(v)

        return {k: v for k, v in dd.items() if len(v) > 1}

    def check_if_incomplete(self) -> Optional[IncompleteExecutionError]:
        """Return a :class:`IncompleteExecutionError` if `pipeline` operations failed/canceled. """
        failures = {
            op: ex for op, ex in self.executed.items() if isinstance(ex, Exception)
        }
        incomplete = iset(chain(self.canceled, failures.keys()))
        if incomplete:
            incomplete = [op.name for op in incomplete]
            partial_msgs = {
                f"\n  +--{op.name}: {list(pouts)}"
                for op, pouts in self.executed.items()
                if pouts and isinstance(pouts, abc.Collection)
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

    def prepare_plot_args(self, plot_args: PlotArgs) -> PlotArgs:
        """delegate to plan, with solution"""
        name = f"solution-x{len(self.plan.net.graph.nodes)}-nodes"
        plot_args = plot_args.with_defaults(name=name, solution=self)
        plot_args = self.plan.prepare_plot_args(plot_args)
        plot_args = plot_args._replace(plottable=self)

        return plot_args


class _OpTask:
    """
    Mimic :class:`concurrent.futures.Future` for :term:`sequential` execution.

    This intermediate class is needed to solve pickling issue with process executor.
    """

    __slots__ = ("op", "sol", "result", "solid")
    logname = __name__

    def __init__(self, op, sol, solid):
        self.op = op
        self.sol = sol
        self.result = UNSET
        self.solid = solid

    def marshalled(self):
        import dill

        return dill.dumps(self)

    def __call__(self):
        if self.result == UNSET:
            self.result = None
            log = logging.getLogger(self.logname)
            log.debug("+++ (%s) Executing %s...", self.solid, self)
            self.result = self.op.compute(self.sol)

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
        result = task()
        result = dill.dumps(result)
    else:
        result = task()

    return result


class ExecutionPlan(
    namedtuple("ExecPlan", "net needs provides dag steps asked_outs"), Plottable
):
    """
    A pre-compiled list of operation steps that can :term:`execute` for the given inputs/outputs.

    It is the result of the network's :term:`compilation` phase.

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

        When true, :term:`evictions` may kick in (unless disabled by :term:`configurations`),
        otherwise, *evictions* (along with prefect-evictions check) are skipped.
    """

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
        steps = (
            "".join(f"\n  +--{s}" for s in self.steps)
            if is_debug()
            else ", ".join(yield_node_names(self.steps))
        )
        return f"ExecutionPlan(needs={needs}, provides={provides}, x{len(self.steps)} steps: {steps})"

    def validate(self, inputs: Items, outputs: Items):
        """
        Scream on invalid inputs, outputs or no operations in graph.

        :raises ValueError:
            - If cannot produce any `outputs` from the given `inputs`, with msg:

                *Unsolvable graph: ...*

            - If given `inputs` mismatched plan's :attr:`needs`, with msg:

                *Plan needs more inputs...*

            - If net cannot produce asked `outputs`, with msg:

                *Unreachable outputs...*

        """
        if not self.dag:
            raise ValueError(f"Unsolvable graph:\n  {self}")

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
            try:
                # Mark start time here, to include also marshalling overhead.
                solution.elapsed_ms[op] = time.time()

                task = _OpTask(op, input_values, solution.solid)
                if first_solid(global_marshal, op.marshalled):
                    task = task.marshalled()

                if first_solid(global_parallel, op.parallel):
                    if not pool:
                        raise RuntimeError(
                            "With `parallel` you must `set_execution_pool().`"
                        )

                    task = pool.apply_async(_do_task, (task,))
                elif isinstance(task, bytes):
                    # Marshalled (but non-parallel) tasks still need `_do_task()`.
                    task = partial(_do_task, task)
                    task.get = task.__call__

                return task
            except Exception as ex:
                jetsam(ex, locals(), "task", plan="self")
                raise

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
            log.debug(
                "... (%s) op(%s) completed in %sms.", solution.solid, op.name, elapsed
            )
        except Exception as ex:
            is_endured = first_solid(solution.is_endurance, op.endured)
            elapsed = elapsed_ms(op)
            loglevel = logging.WARNING if is_endured else logging.DEBUG
            log.log(
                loglevel,
                "... (%s) %s op(%s) FAILED in %0.3fms, due to: %s(%s)",
                solution.solid,
                "*Enduring* " if is_endured else "",
                op.name,
                elapsed,
                type(ex).__name__,
                ex,
            )

            if is_endured:
                solution.operation_failed(op, ex)
            else:
                # Although `plan` have added to jetsam in `compute()``,
                # add it again, in case compile()/execute() is called separately.
                jetsam(ex, locals(), "solution", task="future", plan="self")
                raise

    def _execute_thread_pool_barrier_method(self, solution: Solution):
        """
        This method runs the graph using a parallel pool of thread executors.
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
                elif isinstance(node, _EvictInstruction):
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
                        if _isDebugLogging():
                            log.debug(
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
                    if isinstance(node, _EvictInstruction) and node in solution:
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

                task = _OpTask(step, solution, solution.solid)
                self._handle_task(task, step, solution)

            elif isinstance(step, _EvictInstruction):
                # Cache value may be missing if it is optional.
                if step in solution:
                    log.debug(
                        "... (%s) evicting '%s' from solution%s.",
                        solution.solid,
                        step,
                        list(solution),
                    )
                    del solution[step]

            else:
                raise AssertionError(f"Unrecognized instruction.{step}")

    def execute(self, named_inputs, outputs=None, *, name="") -> Solution:
        """
        :param named_inputs:
            A mapping of names --> values that must contain at least
            the compulsory inputs that were specified when the plan was built
            (but cannot enforce that!).
            Cloned, not modified.
        :param outputs:
            If not None, they are just checked if possible, based on :attr:`provides`,
            and scream if not.

        :return:
            The :term:`solution` which contains the results of each operation executed
            +1 for inputs in separate dictionaries.

        :raises ValueError:
            - If plan does not contain any operations, with msg:

                *Unsolvable graph: ...*

            - If given `inputs` mismatched plan's :attr:`needs`, with msg:

                *Plan needs more inputs...*

            - If net cannot produce asked `outputs`, with msg:

                *Unreachable outputs...*
        """
        try:
            self.validate(named_inputs, outputs)

            ## Choose a method of execution
            #
            in_parallel = is_parallel_tasks() or any(
                op.parallel for op in yield_ops(self.steps)
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
            solution = Solution(
                self,
                {k: v for k, v in named_inputs.items() if k in self.dag.nodes}
                if evict
                else named_inputs,
            )

            log.debug(
                "=== (%s) Executing pipeline(%s)%s%s, on inputs%s, according to %s...",
                solution.solid,
                name,
                ", in parallel" if in_parallel else "",
                ", evicting" if evict else "",
                list(solution),
                self,
            )

            try:
                executor(solution)
            finally:
                solution.finalize()

                ## Log cumulative operations elapsed time.
                #
                if _isDebugLogging():
                    elapsed = sum(solution.elapsed_ms.values())
                    log.debug(
                        "=== (%s) Completed pipeline(%s) in %0.3fms.",
                        solution.solid,
                        name,
                        elapsed,
                    )

            # Validate eviction was perfect
            #
            if evict:
                expected_provides = set(dep_stripped(n) for n in self.provides)
                # It is a proper subset when not all outputs calculated.
                assert set(solution).issubset(expected_provides), (
                    f"Evictions left more data{list(iset(solution) - set(self.provides))} than {self}!"
                    ' \n Did you bypass "impossible-outputs" validation?'
                )

            return solution
        except Exception as ex:
            jetsam(ex, locals(), "solution")
            raise
