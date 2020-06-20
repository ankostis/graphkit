# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
:term:`compose` operations into :term:`pipeline` and the backing :term:`network`.

.. note::
    This module (along with :mod:`.op` & :mod:`.modifiers`) is what client code needs
    to define pipelines *on import time* without incurring a heavy price
    (<5ms on a 2019 fast PC)
"""

import logging
import re
from collections import abc as cabc
from typing import Callable, List, Mapping, Union

from boltons.setutils import IndexedSet as iset

from .base import UNSET, Items, Operation, PlotArgs, Plottable, RenArgs, aslist, jetsam
from .modifiers import dep_renamed

log = logging.getLogger(__name__)


class NULL_OP(Operation):
    """
    Eliminates same-named operations added later during term:`operation merging`.

    :seealso: :ref:`operation-merging`
    """

    def __init__(self, name):
        self.name = name

    def __hash__(self):
        return hash(self.name)

    def __eq__(self, o):
        return self.name == o.name

    def __repr__(self):
        return f"{type(self).__name__}({self.name})"

    def compute(self, *args, **kw):
        raise AssertionError(f"{self} should have been eliminated!")

    def prepare_plot_args(self, *args, **kw):
        raise AssertionError(f"{self} should have been eliminated!")


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
):
    """
    The :term:`network` factory that does :term:`operation merging` before constructing it.

    :param nest:
        see same-named param in :func:`.compose`
    """
    kw = {
        k: v for k, v in locals().items() if v is not None and k not in ("operations")
    }

    def proc_op(op, parent=None):
        """clone FuncOperation with certain props changed"""
        ## Convey any node-props specified in the pipeline here
        #  to all sub-operations.
        #
        from .op import FunctionalOperation

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

        return op

    merge_set = iset()  # Preseve given node order.
    for op in operations:
        if isinstance(op, Pipeline):
            merge_set.update(proc_op(s, op) for s in op.ops)
        else:
            merge_set.add(proc_op(op))
    merge_set = iset(i for i in merge_set if not isinstance(i, NULL_OP))

    assert all(bool(n) for n in merge_set)

    from .network import Network  # Imported here not to affect locals() at the top.

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
    ):
        """
        For arguments, ee :meth:`withset()` & class attributes.

        :raises ValueError:
            if dupe operation, with msg:

                *Operations may only be added once, ...*
        """
        from .op import reparse_operation_data

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
        predicate: "NodePredicate" = None,
        solution_class: "Type[Solution]" = None,
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
        :param predicate:
            filter-out nodes before compiling
        :param solution_class:
            a custom solution factory to use

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
            if not predicate:
                predicate = self.predicate

            log.debug("=== Compiling %s...", self)
            # # Discover nested inputs given.
            # plan = net.compile(
            #     self._nested_inputs_given(named_inputs), outputs, predicate
            # )
            plan = net.compile(named_inputs.keys(), outputs, predicate)

            # Restore `abort` flag for next run.
            reset_abort()

            solution = plan.execute(
                named_inputs, outputs, name=self.name, solution_class=solution_class
            )

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
) -> Pipeline:
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
    )
