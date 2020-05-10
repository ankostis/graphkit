# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
import functools as fnt
import itertools as itt
import logging
import textwrap
from unittest.mock import MagicMock

import pytest

from graphtik import base, network, operation, pipeline
from graphtik.execution import ExecutionPlan, Solution, _OpTask
from graphtik.op import Operation
from graphtik.pipeline import Pipeline


@pytest.mark.parametrize("locs", [None, (), [], [0], "bad"])
def test_jetsam_bad_locals(locs, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AssertionError, match="Bad `locs`") as excinfo:
        try:
            raise Exception()
        except Exception as ex:
            base.jetsam(ex, locs, a="a")
            raise

    assert not hasattr(excinfo.value, "jetsam")
    assert "Suppressed error while annotating exception" not in caplog.text


@pytest.mark.parametrize("keys", [{"k": None}, {"k": ()}, {"k": []}, {"k": [0]}])
def test_jetsam_bad_keys(keys, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AssertionError, match="Bad `salvage_mappings`") as excinfo:
        try:
            raise Exception("ABC")
        except Exception as ex:
            base.jetsam(ex, {}, **keys)

    assert not hasattr(excinfo.value, "jetsam")
    assert "Suppressed error while annotating exception" not in caplog.text


@pytest.mark.parametrize("locs", [None, (), [], [0], "bad"])
def test_jetsam_bad_locals_given(locs, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AssertionError, match="Bad `locs`") as excinfo:
        try:
            raise Exception("ABC")
        except Exception as ex:
            base.jetsam(ex, locs, a="a")
            raise

    assert not hasattr(excinfo.value, "jetsam")
    assert "Suppressed error while annotating exception" not in caplog.text


@pytest.mark.parametrize("annotation", [None, (), [], [0], "bad"])
def test_jetsam_bad_existing_annotation(annotation, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(Exception, match="ABC") as excinfo:
        try:
            ex = Exception("ABC")
            ex.jetsam = annotation
            raise ex
        except Exception as ex:
            base.jetsam(ex, {}, a="a")
            raise

    assert excinfo.value.jetsam == {"a": None}
    assert "Suppressed error while annotating exception" not in caplog.text


def test_jetsam_dummy_locals(caplog):
    with pytest.raises(Exception, match="ABC") as excinfo:
        try:
            raise Exception("ABC")
        except Exception as ex:
            base.jetsam(ex, {"a": 1}, a="a", bad="bad")
            raise

    assert isinstance(excinfo.value.jetsam, dict)
    assert excinfo.value.jetsam == {"a": 1, "bad": None}
    assert "Suppressed error" not in caplog.text


def _scream(*args, **kwargs):
    raise Exception("ABC")


def _jetsamed_fn(*args, **kwargs):
    b = 1
    try:
        a = 1
        b = 2
        _scream()
    except Exception as ex:
        base.jetsam(ex, locals(), a="a", b="b")
        raise


def test_jetsam_locals_simple(caplog):
    with pytest.raises(Exception, match="ABC") as excinfo:
        _jetsamed_fn()
    assert excinfo.value.jetsam == {"a": 1, "b": 2}
    assert "Suppressed error" not in caplog.text


def test_jetsam_nested():
    def inner():
        try:
            a = 0
            fn = "inner"
            _jetsamed_fn()
        except Exception as ex:
            base.jetsam(ex, locals(), fn="fn")
            raise

    def outer():
        try:

            fn = "outer"
            b = 0
            inner()
        except Exception as ex:
            base.jetsam(ex, locals(), fn="fn")
            raise

    with pytest.raises(Exception, match="ABC") as excinfo:
        outer()

    assert excinfo.value.jetsam == {"fn": "inner", "a": 1, "b": 2}


class _ScreamingOperation(Operation):
    def __init__(self):
        self.name = ("",)
        self.needs = ()
        self.provides = ("a",)
        self.node_props = {}
        self.rescheduled = self.endured = None

    def compute(self, named_inputs, outputs=None):
        _scream()

    def prepare_plot_args(self, _args, **kw):
        _scream()


@pytest.mark.parametrize(
    "acallable, expected_jetsam",
    [
        # NO old-stuff Operation(fn=_jetsamed_fn, name="test", needs="['a']", provides=[]),
        (
            lambda: fnt.partial(
                operation(name="test", needs=["a"], provides=["b"])(_scream).compute,
                named_inputs={"a": 1},
            ),
            "outputs aliases results_fn results_op operation args".split(),
        ),
        (
            lambda: fnt.partial(
                ExecutionPlan(*([None] * 6))._handle_task,
                op=_ScreamingOperation(),
                solution=Solution(MagicMock(), {}),
                future=_OpTask(_ScreamingOperation(), {}, "solid"),
            ),
            "plan solution task".split(),
        ),
        # Not easy to test Network calling a screaming func (see next TC).
    ],
)
def test_jetsam_sites_screaming_func(acallable, expected_jetsam):
    # Check jetsams when the underlying function fails.
    acallable = acallable()
    with pytest.raises(Exception, match="ABC") as excinfo:
        acallable()

    ex = excinfo.value
    assert hasattr(ex, "jetsam"), acallable
    assert set(ex.jetsam.keys()) == set(expected_jetsam)


@pytest.mark.parametrize(
    "acallable, expected_jetsam",
    [
        # NO old-stuff Operation(fn=_jetsamed_fn, name="test", needs="['a']", provides=[]),
        (
            lambda: fnt.partial(
                operation(_scream, name="test").compute, named_inputs=None
            ),
            "outputs aliases results_fn results_op operation args".split(),
        ),
        (
            lambda: fnt.partial(
                ExecutionPlan(*([None] * 6))._handle_task,
                op=operation(_scream, name="Ah!"),
                solution=Solution(MagicMock(), {}),
                future=_OpTask(_ScreamingOperation(), {}, "solid"),
            ),
            "plan solution task".split(),
        ),
        (
            lambda: fnt.partial(
                ExecutionPlan(*([None] * 6)).execute, named_inputs=None
            ),
            ["solution"],
        ),
        (
            lambda: fnt.partial(
                Pipeline([operation(str)], "name").compute,
                named_inputs=None,
                outputs="bad",
            ),
            "network plan solution outputs".split(),
        ),
    ],
)
def test_jetsam_sites_scream(acallable, expected_jetsam):
    # Check jetsams when the site fails.
    acallable = acallable()
    with pytest.raises(Exception) as excinfo:
        acallable()

    ex = excinfo.value
    assert hasattr(ex, "jetsam"), acallable
    assert set(ex.jetsam.keys()) == set(expected_jetsam)


###############
## func_name
##
def _foo():
    pass


class _Foo:
    def foo(self):
        pass


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "eval"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "builtins.eval"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "<built-in function eval>"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "<built-in function eval>"),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "eval"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "builtins.eval"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "<built-in function eval>"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "<built-in function eval>"),
    ],
)
def test_func_name_builtin(kw, exp):
    assert base.func_name(eval, **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "_foo"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base._foo"),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_foo"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._foo"),
    ],
)
def test_func_name_non_partial(kw, exp):
    assert base.func_name(_foo, **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "_foo(...)"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base._foo(...)"),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_foo(...)"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._foo(...)"),
    ],
)
def test_func_name_partial_empty(kw, exp):
    assert base.func_name(fnt.partial(_foo), **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "_foo(1, a=2, ...)"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base._foo(1, a=2, ...)"),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_foo(1, a=2, ...)"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._foo(1, a=2, ...)"),
    ],
)
def test_func_name_partial_args(kw, exp):
    assert base.func_name(fnt.partial(_foo, 1, a=2), **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "_foo(1, a=2, b=3, ...)"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base._foo(1, a=2, b=3, ...)",),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_foo(1, a=2, b=3, ...)"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._foo(1, a=2, b=3, ...)",),
    ],
)
def test_func_name_partial_x2(kw, exp):
    assert base.func_name(fnt.partial(fnt.partial(_foo, 1, a=2), b=3), **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base.foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "foo"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base.foo",),
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_Foo.foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._Foo.foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_Foo.foo"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._Foo.foo",),
    ],
)
def test_func_name_class_method(kw, exp):
    assert base.func_name(_Foo.foo, **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base.foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "foo"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base.foo",),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_Foo.foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._Foo.foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_Foo.foo"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._Foo.foo",),
    ],
)
def test_func_name_object_method(kw, exp):
    assert base.func_name(_Foo().foo, **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "foo"),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base.foo"),
        ({"mod": 0, "fqdn": 0, "human": 1}, "foo(...)"),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base.foo(...)",),
        ## FQDN = 1
        #
        ({"mod": 0, "fqdn": 1, "human": 0}, "_Foo.foo"),
        ({"mod": 1, "fqdn": 1, "human": 0}, "test.test_base._Foo.foo"),
        ({"mod": 0, "fqdn": 1, "human": 1}, "_Foo.foo(...)"),
        ({"mod": 1, "fqdn": 1, "human": 1}, "test.test_base._Foo.foo(...)",),
    ],
)
def test_func_name_partial_method(kw, exp):
    assert base.func_name(fnt.partialmethod(_Foo.foo), **kw) == exp


@pytest.mark.parametrize(
    "kw, exp",
    [
        ## FQDN = 0
        #
        ({"mod": 0, "fqdn": 0, "human": 0}, "<lambda>",),
        ({"mod": 1, "fqdn": 0, "human": 0}, "test.test_base.<lambda>",),
        ({"mod": 0, "fqdn": 0, "human": 1}, "<lambda>",),
        ({"mod": 1, "fqdn": 0, "human": 1}, "test.test_base.<lambda>",),
        ## FQDN = 1
        #
        (
            {"mod": 0, "fqdn": 1, "human": 0},
            "test_func_name_lambda_local.<locals>.<lambda>",
        ),
        (
            {"mod": 1, "fqdn": 1, "human": 0},
            "test.test_base.test_func_name_lambda_local.<locals>.<lambda>",
        ),
        (
            {"mod": 0, "fqdn": 1, "human": 1},
            "test_func_name_lambda_local.<locals>.<lambda>",
        ),
        (
            {"mod": 1, "fqdn": 1, "human": 1},
            "test.test_base.test_func_name_lambda_local.<locals>.<lambda>",
        ),
    ],
)
def test_func_name_lambda_local(kw, exp):
    assert base.func_name(lambda: None, **kw) == exp


def test_func_name_partials_vs_human():
    assert base.func_name(fnt.partialmethod(_Foo.foo), human=1, partials=0) == "foo"
    assert base.func_name(fnt.partialmethod(_Foo.foo), human=None, partials=0) == "foo"
    assert (
        base.func_name(fnt.partialmethod(_Foo.foo), human=1, partials=1) == "foo(...)"
    )
    assert (
        base.func_name(fnt.partialmethod(_Foo.foo), human=None, partials=1)
        == "foo(...)"
    )
    assert (
        base.func_name(fnt.partialmethod(_Foo.foo), human=1, partials=None)
        == "foo(...)"
    )


###############
## func_source
##
@pytest.mark.parametrize(
    "fn", [_foo, fnt.partial(_foo), fnt.partial(_foo, 1),],
)
def test_func_source_func(fn):
    exp = "def _foo():\n    pass"
    assert base.func_source(fn, human=1).strip() == exp


@pytest.mark.parametrize(
    "fn",
    [
        _Foo.foo,
        fnt.partial(_Foo.foo),
        _Foo().foo,
        fnt.partial(_Foo().foo),
        fnt.partialmethod(_Foo.foo),
    ],
)
def test_func_source_method(fn):
    exp = "def foo(self):\n        pass"
    assert base.func_source(fn, human=1).strip() == exp


@pytest.mark.parametrize("fn", [eval, fnt.partial(eval)])
def test_func_source_builtin_id(fn):
    exp = str(eval)
    got = base.func_source(fn, human=0)
    assert got == exp


@pytest.mark.parametrize("fn", [eval, fnt.partial(eval)])
def test_func_source_builtin_human(fn):
    exp = "Evaluate the given source in the context of globals and locals."
    got = base.func_source(fn, human=1)
    assert got == exp


###############
## func_sourcelines
##
@pytest.mark.parametrize(
    "fn", [_foo, fnt.partial(_foo), fnt.partial(_foo, 1),],
)
def test_func_sourcelines_func(fn):
    exp = "def _foo():\n    pass"
    got = base.func_sourcelines(fn, human=1)
    assert "".join(got[0]).strip() == exp
    assert got[1] > 200


@pytest.mark.parametrize(
    "fn",
    [
        _Foo.foo,
        fnt.partial(_Foo.foo),
        _Foo().foo,
        fnt.partial(_Foo().foo),
        fnt.partialmethod(_Foo.foo),
    ],
)
def test_func_sourcelines_method(fn):
    exp = "def foo(self):\n        pass"
    got = base.func_sourcelines(fn, human=1)
    assert "".join(got[0]).strip() == exp
    assert got[1] > 200


@pytest.mark.parametrize("fn", [eval, fnt.partial(eval)])
def test_func_sourcelines_builtin(fn):
    exp = ["<built-in function eval>"]
    got = base.func_sourcelines(fn, human=1)
    assert got == (exp, -1)
