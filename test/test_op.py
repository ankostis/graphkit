# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.

import logging
import re
from collections import OrderedDict, namedtuple
from types import SimpleNamespace

import dill
import pytest

from graphtik import NO_RESULT, compose, operation, optional, sideffect, vararg, varargs
from graphtik.network import yield_ops
from graphtik.op import (
    FunctionalOperation,
    Operation,
    as_renames,
    reparse_operation_data,
)


@pytest.fixture(params=[None, ["some"]])
def opname(request):
    return request.param


@pytest.fixture(params=[None, ["some"]])
def opneeds(request):
    return request.param


@pytest.fixture(params=[None, ["some"]])
def opprovides(request):
    return request.param


def test_repr_smoke(opname, opneeds, opprovides):
    # Simply check __repr__() does not crash on partial attributes.
    kw = locals().copy()
    kw = {name[2:]: arg for name, arg in kw.items()}

    op = operation(**kw)
    str(op)


def test_repr_returns_dict():
    assert (
        str(operation(lambda: None, name="", returns_dict=True)())
        == "FunctionalOperation(name='', needs=[], provides=[], fn{}='<lambda>')"
    )
    assert (
        str(operation(lambda: None, name="myname")())
        == "FunctionalOperation(name='myname', needs=[], provides=[], fn='<lambda>')"
    )


@pytest.mark.parametrize(
    "opargs, exp",
    [
        ((None, None, None), (None, (), ())),
        ## Check name
        (("_", "a", ("A",)), ("_", ("a",), ("A",))),
        (((), ("a",), None), ((), ("a",), ())),
        ((("a",), "a", "b"), (("a",), ("a",), ("b",))),
        ((("a",), None, None), (("a",), (), ())),
        ## Check needs
        (((), (), None), ((), (), ())),
        (((), [], None), ((), [], ())),
        (("", object(), None), ValueError("Cannot tuple-ize needs")),
        (("", [None], None), ValueError("All `needs` must be str")),
        (("", [()], None), ValueError("All `needs` must be str")),
        ## Check provides
        (((), "a", ()), ((), ("a",), ())),
        (((), "a", []), ((), ("a",), [])),
        (("", "a", object()), ValueError("Cannot tuple-ize provides")),
        (("", "a", (None,)), ValueError("All `provides` must be str")),
        (("", "a", [()]), ValueError("All `provides` must be str")),
    ],
)
def test_func_op_validation(opargs, exp):
    if isinstance(exp, Exception):
        with pytest.raises(type(exp), match=str(exp)):
            reparse_operation_data(*opargs)
    else:
        assert reparse_operation_data(*opargs) == exp


@pytest.mark.parametrize(
    "args, kw, exp",
    [
        ((), {"node_props": []}, None),
        (
            (),
            {"node_props": 3.14},
            ValueError("node_props` must be a dict, was 'float':"),
        ),
        (
            (),
            {"node_props": "ab"},
            ValueError("node_props` must be a dict, was 'str':"),
        ),
        ((), {"parents": []}, None),
        ((), {"parents": ["gg"]}, ValueError("parents` must be tuple, was 'list':")),
        ((), {"parents": 3.14}, ValueError("parents` must be tuple, was 'float':")),
        ((), {"parents": "gg"}, ValueError("parents` must be tuple, was 'str':")),
    ],
)
def test_func_op_init(args, kw, exp):
    if isinstance(exp, Exception):
        with pytest.raises(type(exp), match=str(exp)):
            FunctionalOperation(str, "", *args, **kw)
    else:
        FunctionalOperation(str, "", *args, **kw)


@pytest.mark.parametrize(
    "result, dictres",
    [
        ({"aa": 1, "b": 2}, ...),
        (OrderedDict(aa=1, b=2), ...),
        (namedtuple("T", "a, bb")(1, 2), {"a": 1, "bb": 2}),
        (SimpleNamespace(a=1, bb=2), {"a": 1, "bb": 2}),
    ],
)
def test_returns_dict(result, dictres):
    if dictres is ...:
        dictres = result

    op = operation(lambda: result, provides=dictres.keys(), returns_dict=True)()
    assert op.compute({}) == dictres

    op = operation(lambda: result, provides="a", returns_dict=False)()
    assert op.compute({})["a"] == result


@pytest.fixture(params=[None, ["a", "b"]])
def asked_outputs(request):
    return request.param


@pytest.mark.parametrize("result", [(), None, {}, {"a"}, "b", "ab", "abc", ""])
def test_results_sequence_1_provides_ok(result, asked_outputs):
    op = operation(lambda: result, provides=["a"])()
    sol = op.compute({}, outputs=asked_outputs)
    assert sol["a"] == result


def test_results_sequence_lt_1_provides(asked_outputs):
    op = operation(lambda: NO_RESULT, provides=["a"])()
    with pytest.raises(ValueError, match=f"Got -1 fewer results, while expected x1"):
        op.compute({}, outputs=asked_outputs)


@pytest.mark.parametrize(
    "result, nfewer", [((), -2), ({}, -2), (NO_RESULT, -2), ({"a"}, -1)]
)
def test_results_sequence_lt_many_provides(result, nfewer, asked_outputs):
    op = operation(lambda: result, provides=["a", "b"])()
    with pytest.raises(
        ValueError, match=f"Got {nfewer} fewer results, while expected x2"
    ):

        op.compute({}, outputs=asked_outputs)


@pytest.mark.parametrize("result", ["", "a", "ab", "foobar", 3.14, None])
def test_results_validation_bad_iterable(result, asked_outputs):
    op = operation(lambda: result, provides=["a", "b"])()
    with pytest.raises(ValueError, match=f"Expected x2 ITERABLE results, got"):
        op.compute({}, outputs=asked_outputs)


@pytest.mark.parametrize("result", [None, 3.14, [], "foo", ["b", "c", "e"], {"a", "b"}])
def test_dict_results_validation_BAD(result, asked_outputs):
    op = operation(lambda: result, provides=["a", "b"], returns_dict=True)()
    with pytest.raises(ValueError, match="Expected results as mapping,"):
        op.compute({}, outputs=asked_outputs)


@pytest.mark.parametrize(
    "result, nmiss",
    [({}, 2), ({"a": 1}, 1), ({"a": 1, "c": 3}, 1), ({"aa": 1, "bb": 2}, 2)],
)
def test_dict_results_validation_MISMATCH(result, nmiss, asked_outputs):
    op = operation(lambda: result, provides=["a", "b"], returns_dict=True)()
    with pytest.raises(ValueError, match=f"mismatched -{nmiss} provides"):
        op.compute({}, outputs=asked_outputs)


def test_varargs():
    def sumall(a, *args, b=0, **kwargs):
        return a + sum(args) + b + sum(kwargs.values())

    op = operation(
        sumall,
        name="t",
        needs=[
            "a",
            vararg("arg1"),
            vararg("arg2"),
            varargs("args"),
            optional("b"),
            optional("c"),
        ],
        provides="sum",
    )()

    exp = sum(range(8))
    assert op.compute(dict(a=1, arg1=2, arg2=3, args=[4, 5], b=6, c=7))["sum"] == exp
    assert op.compute(dict(a=1, arg1=2, arg2=3, args=[4, 5], c=7))["sum"] == exp - 6
    assert op.compute(dict(a=1, arg1=2, arg2=3, args=[4, 5], b=6))["sum"] == exp - 7
    assert op.compute(dict(a=1, arg2=3, args=[4, 5], b=6, c=7))["sum"] == exp - 2
    assert op.compute(dict(a=1, arg1=2, arg2=3, b=6, c=7))["sum"] == exp - 4 - 5
    with pytest.raises(ValueError, match="Missing compulsory needs.+'a'"):
        assert op.compute(dict(arg1=2, arg2=3, b=6, c=7))


def test_op_node_props_bad():
    op_factory = operation(lambda: None, name="a", node_props="SHOULD BE DICT")
    with pytest.raises(ValueError, match="`node_props` must be"):
        op_factory()


def test_op_node_props():
    op_factory = operation(lambda: None, name="a", node_props=())
    assert op_factory.node_props == ()
    assert op_factory().node_props == {}

    np = {"a": 1}
    op = operation(lambda: None, name="a", node_props=np)()
    assert op.node_props == np


def _collect_op_props(netop):
    return {
        k.name: v
        for k, v in netop.net.graph.nodes.data(True)
        if isinstance(k, Operation)
    }


def test_netop_node_props():
    op1 = operation(lambda: None, name="a", node_props={"a": 11, "b": 0, "bb": 2})()
    op2 = operation(lambda: None, name="b", node_props={"a": 3, "c": 4})()
    netop = compose("n", op1, op2, node_props={"bb": 22, "c": 44})

    exp = {"a": {"a": 11, "b": 0, "bb": 22, "c": 44}, "b": {"a": 3, "bb": 22, "c": 44}}
    node_props = _collect_op_props(netop)
    assert node_props == exp

    # Check node-prop sideffects are not modified
    #
    assert op1.node_props == {"a": 11, "b": 0, "bb": 2}
    assert op2.node_props == {"a": 3, "c": 4}


def test_netop_merge_node_props():
    op1 = operation(lambda: None, name="a", node_props={"a": 1})()
    netop1 = compose("n1", op1)
    op2 = operation(lambda: None, name="a", node_props={"a": 11, "b": 0, "bb": 2})()
    op3 = operation(lambda: None, name="b", node_props={"a": 3, "c": 4})()
    netop2 = compose("n2", op2, op3)

    netop = compose("n", netop1, netop2, node_props={"bb": 22, "c": 44}, merge=False)
    exp = {
        "n1.a": {"a": 1, "bb": 22, "c": 44},
        "n2.a": {"a": 11, "b": 0, "bb": 22, "c": 44},
        "n2.b": {"a": 3, "bb": 22, "c": 44},
    }
    node_props = _collect_op_props(netop)
    assert node_props == exp

    netop = compose("n", netop1, netop2, node_props={"bb": 22, "c": 44}, merge=True)
    exp = {"a": {"a": 1, "bb": 22, "c": 44}, "b": {"a": 3, "bb": 22, "c": 44}}
    node_props = _collect_op_props(netop)
    assert node_props == exp


@pytest.mark.parametrize(
    "inp, exp",
    [
        ({"a": "b"}, {"a": "b"}.items()),
        ((1, 2), [(1, 2)]),
        ([(1, 2)], [(1, 2)]),
        ([], []),
        ((), []),
        (("ab", "ad"), [("a", "b"), ("a", "d")]),
    ],
)
def test_as_renames(inp, exp):
    as_renames((1, 2), "alias")


@pytest.mark.parametrize(
    "opbuilder, ex",
    [
        (
            operation(str, aliases={"a": 1}),
            r"The `aliases` for ['a'] rename ['a'], not found in provides []!",
        ),
        (
            operation(str, name="t", provides="a", aliases={"a": 1, "b": 2}),
            r"The `aliases` for ['a', 'b'] rename ['b'], not found in provides ['a']!",
        ),
        (
            operation(
                str, name="t", provides=sideffect("a"), aliases={sideffect("a"): 1}
            ),
            r"must not contain `sideffects`",
        ),
        (
            operation(str, name="t", provides="a", aliases={"a": sideffect("AA")}),
            r"must not contain `sideffects`",
        ),
    ],
)
def test_provides_aliases_BAD(opbuilder, ex):
    with pytest.raises(ValueError, match=re.escape(ex)):
        opbuilder()


def test_provides_aliases():
    op = operation(str, name="t", needs="s", provides="a", aliases={"a": "aa"})()
    assert op.provides == {"a", "aa"}
    assert op.compute({"s": "k"}) == {"a": "k", "aa": "k"}


@pytest.mark.parametrize("rescheduled", [0, 1])
def test_reschedule_more_outs(rescheduled, caplog):
    op = operation(
        lambda: [1, 2, 3], name="t", provides=["a", "b"], rescheduled=rescheduled
    )()
    op.compute({})
    assert "+1 more results, while expected x2" in caplog.text


def test_reschedule_unknown_dict_outs(caplog):
    op = operation(
        lambda: {"b": "B"}, name="t", provides=["a"], rescheduled=1, returns_dict=1
    )()
    caplog.set_level(logging.INFO)
    op.compute({})
    assert "contained +1 unknown provides['b']" in caplog.text

    caplog.clear()
    op = operation(
        lambda: {"a": 1, "BAD": "B"},
        name="t",
        provides=["a"],
        rescheduled=1,
        returns_dict=1,
    )()
    op.compute({})
    assert "contained +1 unknown provides['BAD']" in caplog.text


def test_rescheduled_op_repr():
    op = operation(str, name="t", provides=["a"], rescheduled=True)
    assert str(op) == "operation?(name='t', needs=[], provides=['a'], fn='str')"
    assert (
        str(op())
        == "FunctionalOperation?(name='t', needs=[], provides=['a'], fn='str')"
    )


def test_endured_op_repr():
    op = operation(str, name="t", provides=["a"], endured=True)
    assert str(op) == "operation!(name='t', needs=[], provides=['a'], fn='str')"
    assert (
        str(op())
        == "FunctionalOperation!(name='t', needs=[], provides=['a'], fn='str')"
    )


def test_endured_rescheduled_op_repr():
    op = operation(str, name="t", rescheduled=1, endured=1)
    assert str(op) == "operation!?(name='t', needs=[], provides=[], fn='str')"
    assert (
        str(op()) == "FunctionalOperation!?(name='t', needs=[], provides=[], fn='str')"
    )


def test_parallel_op_repr():
    op = operation(str, name="t", provides=["a"], parallel=True)
    assert str(op) == "operation|(name='t', needs=[], provides=['a'], fn='str')"
    assert (
        str(op())
        == "FunctionalOperation|(name='t', needs=[], provides=['a'], fn='str')"
    )


def test_marshalled_op_repr():
    op = operation(str, name="t", provides=["a"], marshalled=True)
    assert str(op) == "operation$(name='t', needs=[], provides=['a'], fn='str')"
    assert (
        str(op())
        == "FunctionalOperation$(name='t', needs=[], provides=['a'], fn='str')"
    )


def test_marshalled_parallel_op_repr():
    op = operation(str, name="t", parallel=1, marshalled=1)
    assert str(op) == "operation|$(name='t', needs=[], provides=[], fn='str')"
    assert (
        str(op()) == "FunctionalOperation|$(name='t', needs=[], provides=[], fn='str')"
    )


def test_ALL_op_repr():
    op = operation(str, name="t", rescheduled=1, endured=1, parallel=1, marshalled=1)
    assert str(op) == "operation!?|$(name='t', needs=[], provides=[], fn='str')"
    assert (
        str(op())
        == "FunctionalOperation!?|$(name='t', needs=[], provides=[], fn='str')"
    )


def test_reschedule_outputs():
    op = operation(
        lambda: ["A", "B"], name="t", provides=["a", "b", "c"], rescheduled=True
    )()
    assert op.compute({}) == {"a": "A", "b": "B"}

    # NOTE that for a single return item, it must be a collection.
    op = operation(lambda: ["AA"], name="t", provides=["a", "b"], rescheduled=True)()
    assert op.compute({}) == {"a": "AA"}

    op = operation(lambda: NO_RESULT, name="t", provides=["a", "b"], rescheduled=True)()
    assert op.compute({}) == {}

    op = operation(
        lambda: {"b": "B"}, name="t", provides=["a", "b"], rescheduled=1, returns_dict=1
    )()
    assert op.compute({}) == {"b": "B"}

    op = operation(
        lambda: {"b": "B"},
        name="t",
        provides=["a", "b"],
        aliases={"a": "aa", "b": "bb"},
        rescheduled=1,
        returns_dict=1,
    )()
    assert op.compute({}) == {"b": "B", "bb": "B"}


@pytest.mark.parametrize("attr, value", [("outputs", [1]), ("predicate", lambda: None)])
def test_netop_narrow_attributes(attr, value):
    netop = compose("1", operation(str, name="op1")())
    assert getattr(netop.withset(**{attr: value}), attr) == value


_attr_values = [
    ("rescheduled", None),
    ("rescheduled", 1),
    ("rescheduled", False),
    ("endured", None),
    ("endured", True),
    ("endured", 0),
    ("parallel", None),
    ("parallel", True),
    ("parallel", 0),
    ("marshalled", None),
    ("marshalled", True),
    ("marshalled", 0),
]


@pytest.mark.parametrize("attr, value", _attr_values)
def test_op_withset_conveys_attr(attr, value):
    kw = {attr: value}
    op1 = operation(str)()
    assert getattr(op1, attr) is None

    op2 = op1.withset(**kw)
    assert getattr(op2, attr) == value
    assert getattr(op1, attr) is None

    op3 = op2.withset()
    assert getattr(op3, attr) == value


@pytest.mark.parametrize("attr, value", _attr_values)
def test_netop_conveys_attr_to_ops(attr, value):
    def _opsattrs(ops, attr, value):
        vals = [getattr(op, attr) for op in ops if isinstance(op, Operation)]
        assert all(v == value for v in vals)

    kw = {attr: value}
    _opsattrs(compose("1", operation(str)(), **kw).net.graph, attr, value)
    _opsattrs(
        compose(
            "2", operation(str, name=1)(), operation(str, name=2)(), **kw
        ).net.graph,
        attr,
        value,
    )


@pytest.mark.parametrize(
    "op", [Operation, FunctionalOperation, operation(str)(), operation(lambda: None)()]
)
def test_dill_ops(op):
    dill.loads(dill.dumps(op))
