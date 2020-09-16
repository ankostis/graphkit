# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
:term:`compose` :term:`pipeline`\\s by :term:`combining <combine pipelines>` operations into :term:`network`.

.. note::
    This module (along with :mod:`.op` & :mod:`.modifier`) is what client code needs
    to define pipelines *on import time* without incurring a heavy price
    (<5ms on a 2019 fast PC)
"""

import inspect
import logging
import re
import sys
from collections import abc as cabc
from typing import Callable, List, Mapping, Union

from boltons.setutils import IndexedSet as iset

from .base import (
    UNSET,
    Items,
    Operation,
    PlotArgs,
    Plottable,
    RenArgs,
    aslist,
    asset,
)
from .jetsam import save_jetsam
from .modifier import dep_renamed

log = logging.getLogger(__name__)


def _id_bool(b):
    return hash(bool(b)) + 1


def _id_tristate_bool(b):
    return 3 if b is None else (hash(bool(b)) + 1)


def build_network(
    operations,
    rescheduled=None,
    endured=None,
    parallel=None,
    marshalled=None,
    node_props=None,
    renamer=None,
    excludes=None,
):
    """
    The :term:`network` factory that does :term:`operation merging` before constructing it.

    :param nest:
        see same-named param in :func:`.compose`
    """
    kw = {
        k: v
        for k, v in locals().items()
        if v is not None and k not in ("operations", "excludes")
    }

    def proc_op(op, parent=None):
        """clone FuncOperation with certain props changed"""
        ## Convey any node-props specified in the pipeline here
        #  to all sub-operations.
        #
        if kw:
            op_kw = kw.copy()

            if node_props:
                op_kw["node_props"] = {**op.node_props, **node_props}

            if callable(renamer):

                def parent_wrapper(ren_args: RenArgs) -> str:
                    # Provide RenArgs.parent.
                    return renamer(ren_args._replace(parent=parent))

                op_kw["renamer"] = parent_wrapper
            op = op.withset(**op_kw)

        ## Last minute checks, couldn't check earlier due to builder pattern.
        #
        if hasattr(op, "fn"):
            op.validate_fn_name()
        if not op.provides:
            TypeError(f"`provides` must not be empty!")

        return op

    merge_set = iset()  # Preseve given node order.
    for op in operations:
        if isinstance(op, Pipeline):
            merge_set.update(proc_op(s, op) for s in op.ops)
        else:
            merge_set.add(proc_op(op))

    if excludes is not None:
        excludes = {op for op in merge_set if op in asset(excludes, "excludes")}
        if excludes:
            merge_set = [op for op in merge_set if op not in excludes]
            log.info("Compose excluded %i operations %s.", len(excludes), excludes)

    assert all(bool(n) for n in merge_set)

    from .planning import Network  # Imported here not to affect locals() at the top.

    return Network(*merge_set)


class Pipeline(Operation):
    """
    An operation that can :term:`compute` a network-graph of operations.

    .. Tip::
        - Use :func:`compose()` factory to prepare the `net` and build
          instances of this class.
        - See :term:`diacritic`\\s to understand printouts of this class.
    """

    #: The name for the new pipeline, used when nesting them.
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
        excludes=None,
    ):
        """
        For arguments, ee :meth:`withset()` & class attributes.

        :raises ValueError:
            if dupe operation, with msg:

                *Operations may only be added once, ...*
        """
        from .fnop import reparse_operation_data

        ## Set data asap, for debugging, although `net.withset()` will reset them.
        self.name = name
        #: Fake function attributes.
        self.__name__ = self.__qualname__ = name

        #: Remember `outputs` for future `compute()`?
        self.outputs = outputs
        #: Remember `predicate` for future `compute()`?
        self.predicate = predicate

        # Prune network
        self.net = build_network(
            operations,
            rescheduled,
            endured,
            parallel,
            marshalled,
            node_props,
            renamer,
            excludes,
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
    ) -> "Pipeline":
        """
        Return a copy with a network pruned for the given `needs` & `provides`.

        :param outputs:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, the value of this instance is conveyed to the clone.
        :param predicate:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, the value of this instance is conveyed to the clone.
        :param name:
            the name for the new pipeline:

            - if `None`, the same name is kept;
            - if True, a distinct name is devised::

                <old-name>-<uid>

            - if ellipses(``...``), the name of the function where this function
              call happened is used,
            - otherwise, the given `name` is applied.
        :param rescheduled:
            applies :term:`reschedule`\\d to all contained `operations`
        :param endured:
            applies :term:`endurance` to all contained `operations`
        :param parallel:
            (deprecated) mark all contained `operations` to be executed in :term:`parallel`
        :param marshalled:
            mark all contained `operations` to be :term:`marshalled <marshalling>`
            (usefull when run in (deprecated) `parallel` with a :term:`process pool`).
        :param renamer:
            see respective parameter in :meth:`.FnOp.withset()`.

        :return:
            A narrowed pipeline clone, which **MIGHT be empty!***

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

        return Pipeline(
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

    @property
    def graph(self):
        return self.net.graph

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
        self,
        inputs=None,
        outputs=UNSET,
        recompute_from=None,
        *,
        predicate: "NodePredicate" = UNSET,
    ) -> "ExecutionPlan":
        """
        Produce a :term:`plan` for the given args or `outputs`/`predicate` narrowed earlier.

        :param named_inputs:
            a string or a list of strings that should be fed to the `needs` of all operations.
        :param outputs:
            A string or a list of strings with all data asked to compute.
            If ``None``, all possible intermediate outputs will be kept.
            If not given, those set by a previous call to :meth:`withset()` or cstor are used.
        :param recompute_from:
            Described in :meth:`.Pipeline.compute()`.
        :param predicate:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, those set by a previous call to :meth:`withset()` or cstor are used.

        :return:
            the :term:`execution plan` satisfying the given `inputs`, `outputs` & `predicate`

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
        if outputs == UNSET:
            outputs = self.outputs
        if predicate == UNSET:
            predicate = self.predicate

        return self.net.compile(inputs, outputs, recompute_from, predicate=predicate)

    def compute(
        self,
        named_inputs: Mapping = None,
        outputs: Items = UNSET,
        recompute_from: Items = None,
        *,
        predicate: "NodePredicate" = UNSET,
        pre_callback=None,
        solution_class: "Type[Solution]" = None,
        layered_solution=None,
    ) -> "Solution":
        """
        Compile & :term:`execute` the plan, log :term:`jetsam` & plot :term:`plottable` on errors.

        .. Attention::
            If intermediate :term:`planning` is successful, the "global
            :term:`abort run` flag is reset before the :term:`execution` starts.

        :param named_inputs:
            A mapping of names --> values that will be fed to the `needs` of all operations.
            Cloned, not modified.
        :param outputs:
            A string or a list of dependencies with all data asked to compute.
            If ``None``, all possible intermediate outputs will be kept.
            If not given, those set by a previous call to :meth:`withset()` or cstor are used.
        :param recompute_from:
            :term:`recompute` operations downstream from these (string or list) dependencies.
            In effect, before :term:`compiling <compile>`, it marks all values
            *strictly downstream (excluding themselves)* from the dependencies
            listed here, as missing from `named_inputs`.

            * Traversing downstream stops when arriving at any dep in `outputs`.
            * Any dependencies here unreachable downstreams from values in `named_inputs`
              are ignored, but logged.
            * Any dependencies here unreachable upstreams from `outputs` (if given)
              are ignored, but logged.
            * Results may differ even if graph is unchanged, in the presence
              of :term:`overwrite`\\s.
        :param predicate:
            filter-out nodes before compiling
            If not given, those set by a previous call to :meth:`withset()` or cstor are used.
        :param pre_callback:
            If given, the :term:`pre_callback` is called before each operation,
            with that as argument and solution.
        :param solution_class:
            a custom solution factory to use
        :param layered_solution:
            whether to store operation results into separate :term:`solution layer`\\s

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

        ok = False
        try:
            if named_inputs is None:
                named_inputs = {}

            net = self.net  # jetsam
            if outputs == UNSET:
                outputs = self.outputs
            if predicate == UNSET:
                predicate = self.predicate

            log.info("=== Compiling pipeline(%s) ...", self.name)
            plan = net.compile(
                named_inputs.keys(),
                outputs,
                recompute_from,
                predicate=predicate,
            )

            # Restore `abort` flag for next run.
            reset_abort()

            solution = plan.execute(
                named_inputs,
                outputs,
                name=self.name,
                pre_callback=pre_callback,
                solution_class=solution_class,
                layered_solution=layered_solution,
            )

            ok = True
            return solution
        finally:
            if not ok:
                ex = sys.exc_info()[1]
                jetsam = save_jetsam(
                    ex,
                    locals(),
                    "plan",
                    "solution",
                    "outputs",
                    pipeline="self",
                    network="net",
                )

                try:
                    jetsam.log_n_plot()
                except Exception as ex2:
                    log.warning(
                        "Suppressed error while logging/plotting jetsam of %s: %s(%s)"
                        "\n  +--annotations:%s",
                        self,
                        jetsam,
                        type(ex).__name__,
                        ex,
                        exc_info=True,
                    )

    def __call__(self, **input_kwargs) -> "Solution":
        """
        Delegates to :meth:`compute()`, respecting any narrowed `outputs`.
        """
        # To respect narrowed `outputs` must send them due to recompilation.
        return self.compute(input_kwargs, outputs=self.outputs)


def nest_any_node(ren_args: RenArgs) -> str:
    """Nest both operation & data under `parent`'s name (if given) but NOT jsonparts.

    :return:
        the nested name of the operation or data
    """

    def prefixed(name):
        return f"{ren_args.parent.name}.{name}" if ren_args.parent else name

    if not ren_args.typ.endswith(".jsonpart"):
        return dep_renamed(ren_args.name, prefixed)


def compose(
    name: Union[str, type(...), None],
    op1: Operation,
    *operations: Operation,
    excludes=None,
    outputs: Items = None,
    rescheduled=None,
    endured=None,
    parallel=None,
    marshalled=None,
    nest: Union[Callable[[RenArgs], str], Mapping[str, str], Union[bool, str]] = None,
    node_props=None,
) -> Pipeline:
    """
    Merge or :term:`nest <operation nesting>` operations & pipelines into a new pipeline,

    based on the ``nest`` parameter (read below)

    Operations given earlier (further to the left) override those following
    (further to the right), similar to `set` behavior (and contrary to `dict`).

    :param name:
        An optional name for the graph being composed by this object.
        If ellipses(``...``), derrived from function name where the pipeline
        is defined.
    :param op1:
        syntactically force at least 1 operation
    :param operations:
        each argument should be an operation or pipeline instance
    :param excludes:
        A single string or list of operation-names to exclude from the final network
        (particularly useful when composing existing pipelines).
    :param nest:
        a dictionary or callable corresponding to the `renamer` paremater
        of :meth:`.Pipeline.withset()`, but the calable receives a `ren_args`
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
              and use :func:`.dep_renamed()` for :attr:`.RenArgs.typ` not ending
              in ``.jsonpart``.

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
        (deprecated) mark all contained `operations` to be executed in :term:`parallel`
    :param marshalled:
        mark all contained `operations` to be :term:`marshalled <marshalling>`
        (usefull when run in (deprecated) `parallel` with a :term:`process pool`).
    :param node_props:
        Added as-is into NetworkX graph, to provide for filtering
        by :meth:`.Pipeline.withset()`.
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
    if name is ...:
        pf = inspect.currentframe().f_back
        if pf:
            name = pf.f_code.co_name
        del pf

    operations = (op1,) + operations
    if not all(isinstance(op, Operation) for op in operations):
        bad_ops = [op for op in operations if not isinstance(op, Operation)]
        raise TypeError(f"Received x{len(bad_ops)} non-Operation instances: {bad_ops}")

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

    return Pipeline(
        operations,
        name,
        outputs=outputs,
        rescheduled=rescheduled,
        endured=endured,
        parallel=parallel,
        marshalled=marshalled,
        node_props=node_props,
        renamer=renamer,
        excludes=excludes,
    )
