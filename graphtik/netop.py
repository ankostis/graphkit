# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""About :term:`network operation`\\s (those based on graphs)"""

import logging
import re
from collections import abc
from typing import Any, Callable, Mapping

import networkx as nx
from boltons.setutils import IndexedSet as iset

from .base import Items, Plotter, aslist, astuple, jetsam
from .modifiers import optional, sideffect
from .network import ExecutionPlan, Network, NodePredicate, Solution, yield_ops
from .op import FunctionalOperation, Operation, reparse_operation_data

log = logging.getLogger(__name__)

_unset = object()


def _make_network(
    operations, reschedule=None, endured=None, merge=None, node_props=None
):
    def proc_op(op, parent=None):
        """clone FuncOperation with certain props changed"""
        assert isinstance(op, FunctionalOperation), op

        ## Convey any node-props specified in the netop here
        #  to all sub-operations.
        #
        if (
            node_props
            or (not merge and parent)
            or reschedule is not None
            or endured is not None
        ):
            kw = {"reschedule": reschedule, "endured": endured}
            if node_props:
                op_node_props = op.node_props.copy()
                op_node_props.update(node_props)
                kw["node_props"] = op_node_props
            ## If `merge` asked, leave original `name` to deduplicate operations,
            #  otherwise rename the op by prefixing them with their parent netop.
            #
            if not merge and parent:
                kw["parents"] = (parent,) + (op.parents or ())
            op = op.withset(**kw)

        return op

    merge_set = iset()  # Preseve given node order.
    for op in operations:
        if isinstance(op, NetworkOperation):
            merge_set.update(
                proc_op(s, op.name) for s in op.net.graph if isinstance(s, Operation)
            )
        else:
            merge_set.add(proc_op(op))

    assert all(bool(n) for n in merge_set)
    net = Network(*merge_set)

    return net


class NetworkOperation(Operation, Plotter):
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
    #: either `parallel` or None (default);
    #: if ``"parallel"``, launches multi-threading.
    #: When `None`, sequential by default.
    method = None
    #: The execution_plan of the last call to compute(), stored as debugging aid.
    last_plan = None
    #: The outputs names (possibly `None`) used to compile the :attr:`plan`.
    outputs = None

    def __init__(
        self,
        operations,
        name,
        *,
        outputs=None,
        predicate: NodePredicate = None,
        reschedule=None,
        endured=None,
        merge=None,
        method=None,
        node_props=None,
    ):
        """
        For arguments, ee :meth:`narrowed()` & class attributes.

        :raises ValueError:
            if dupe operation, with msg:

                *Operations may only be added once, ...*
        """
        ## Set data asap, for debugging, although `net.narrowed()` will reset them.
        self.name = name
        # Remember Outputs for future `compute()`?
        self.outputs = outputs
        self.predicate = predicate
        self.set_execution_method(method)

        # Prune network
        self.net = _make_network(operations, reschedule, endured, merge, node_props)
        self.name, self.needs, self.provides = reparse_operation_data(
            self.name, self.net.needs, self.net.provides
        )

    def __repr__(self):
        """
        Display more informative names for the Operation class
        """
        clsname = type(self).__name__
        needs = aslist(self.needs, "needs")
        provides = aslist(self.provides, "provides")
        nops = sum(1 for i in yield_ops(self.net.graph))
        return (
            f"{clsname}({self.name!r}, needs={needs}, provides={provides}, x{nops}ops)"
        )

    def narrowed(
        self,
        outputs: Items = _unset,
        predicate: NodePredicate = _unset,
        *,
        name=None,
        reschedule=None,
        endured=None,
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
        :param reschedule:
            applies :term:`reschedule` to all contained `operations`
        :param endured:
            applies :term:`endurance` to all contained `operations`

        :return:
            A narrowed netop clone, which **MIGHT be empty!***

        :raises ValueError:
            - If `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*

        """
        outputs = self.outputs if outputs is _unset else outputs
        predicate = self.predicate if predicate is _unset else predicate

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
                    ^ bool(reschedule)
                    ^ (2 * bool(endured))
                )
            )[:7]
            m = re.match(r"^(.*)-(\d+)$", name)
            if m:
                name = m.group(1)
            name = f"{name}-{uid}"

        return NetworkOperation(
            [op for op in self.net.graph if isinstance(op, Operation)],
            name,
            outputs=outputs,
            predicate=predicate,
            reschedule=reschedule,
            endured=endured,
            method=self.method,
        )

    def _build_pydot(self, **kws):
        """delegate to network"""
        # FIXME:  demarcate netop inputs/outputs in plots.
        kws.setdefault("title", self.name)
        plotter = self.last_plan or self.net
        return plotter._build_pydot(**kws)

    def compile(
        self, inputs=None, outputs=_unset, predicate: NodePredicate = _unset
    ) -> ExecutionPlan:
        """
        Produce a :term:`plan` for the given args or `outputs`/`predicate` narrrowed earlier.

        :param named_inputs:
            a string or a list of strings that should be fed to the `needs` of all operations.
        :param outputs:
            A string or a list of strings with all data asked to compute.
            If ``None``, all possible intermediate outputs will be kept.
            If not given, those set by a previous call to :meth:`narrowed()` or cstor are used.
        :param predicate:
            Will be stored and applied on the next :meth:`compute()` or :meth:`compile()`.
            If not given, those set by a previous call to :meth:`narrowed()` or cstor are used.

        :return:
            the :term:`execution plan` satisfying the given `inputs`, `outputs` & `predicate`

        :raises ValueError:
            - If `outputs` asked do not exist in network, with msg:

                *Unknown output nodes: ...*

            - If solution does not contain any operations, with msg:

                *Unsolvable graph: ...*

            - If given `inputs` mismatched plan's :attr:`needs`, with msg:

                *Plan needs more inputs...*

            - If `outputs` asked cannot be produced by the :attr:`dag`, with msg:

                *Impossible outputs...*
        """
        outputs = self.outputs if outputs is _unset else outputs
        predicate = self.predicate if predicate is _unset else predicate

        return self.net.compile(inputs, outputs, predicate)

    def compute(
        self,
        named_inputs: Mapping,
        outputs: Items = _unset,
        predicate: NodePredicate = _unset,
    ) -> Solution:
        """
        Compile a plan & :term:`execute` the graph, sequentially or parallel.

        :param named_inputs:
            A maping of names --> values that will be fed to the `needs` of all operations.
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

            - If `outputs` asked cannot be produced by the :attr:`dag`, with msg:

                *Impossible outputs...*

        See also :meth:`.Operation.compute()`.
        """
        try:
            net = self.net  # jetsam
            outputs = self.outputs if outputs is _unset else outputs
            predicate = self.predicate if predicate is _unset else predicate

            # Build the execution plan.
            log.debug("=== Compiling netop(%s)...", self.name)
            self.last_plan = plan = net.compile(named_inputs.keys(), outputs, predicate)

            log.debug("=== Executing netop(%s)...", self.name)
            solution = plan.execute(named_inputs, outputs, method=self.execution_method)

            return solution
        except Exception as ex:
            jetsam(ex, locals(), "plan", "solution", "outputs", network="net")
            raise

    def __call__(self, **input_kwargs) -> Solution:
        """
        Delegates to :meth:`compute()`, respecting any narrowed `outputs`.
        """
        # To respect narrowed `outputs` must send them due to recompilation.
        return self.compute(input_kwargs, outputs=self.outputs)

    def set_execution_method(self, method):
        """
        Determine how the network will be executed.

        :param str method:
            If "parallel", execute graph operations concurrently
            using a threadpool.
        """
        choices = ["parallel", None]
        if method not in choices:
            raise ValueError(
                "Invalid computation method %r!  Must be one of %s" % (method, choices)
            )
        self.execution_method = method


def compose(
    name,
    op1,
    *operations,
    outputs: Items = None,
    reschedule=None,
    endured=None,
    merge=False,
    node_props=None,
    method=None,
) -> NetworkOperation:
    """
    Composes a collection of operations into a single computation graph,
    obeying the ``merge`` property, if set in the constructor.

    :param str name:
        A optional name for the graph being composed by this object.
    :param op1:
        syntactically force at least 1 operation
    :param operations:
        Each argument should be an operation instance created using
        ``operation``.
    :param bool merge:
        If ``True``, this compose object will attempt to merge together
        ``operation`` instances that represent entire computation graphs.
        Specifically, if one of the ``operation`` instances passed to this
        ``compose`` object is itself a graph operation created by an
        earlier use of ``compose`` the sub-operations in that graph are
        compared against other operations passed to this ``compose``
        instance (as well as the sub-operations of other graphs passed to
        this ``compose`` instance).  If any two operations are the same
        (based on name), then that operation is computed only once, instead
        of multiple times (one for each time the operation appears).
    :param reschedule:
        applies :term:`reschedule` to all contained `operations`
    :param endured:
        applies :term:`endurance` to all contained `operations`
    :param node_props:
        added as-is into NetworkX graph, to provide for filtering
        by :meth:`.NetworkOperation.narrowed()`.
    :param method:
        either `parallel` or None (default);
        if ``"parallel"``, launches multi-threading.
        Set when invoking a composed graph or by
        :meth:`.NetworkOperation.set_execution_method()`.

    :return:
        Returns a special type of operation class, which represents an
        entire computation graph as a single operation.

    :raises ValueError:
        If the `net`` cannot produce the asked `outputs` from the given `inputs`.
    """
    operations = (op1,) + operations
    if not all(isinstance(op, Operation) for op in operations):
        raise ValueError(f"Non-Operation instances given: {operations}")

    return NetworkOperation(
        operations,
        name,
        outputs=outputs,
        reschedule=reschedule,
        endured=endured,
        method=method,
        merge=merge,
        node_props=node_props,
    )
