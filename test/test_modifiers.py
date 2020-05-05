import pytest

from graphtik.modifiers import (
    mapped,
    optional,
    sideffect,
    sideffected,
    sideffected_vararg,
    sideffected_varargs,
    vararg,
    varargs,
    rename_dependency,
)


@pytest.mark.parametrize(
    "mod, exp",
    [
        (lambda: mapped("b", None), "b"),
        (lambda: mapped("b", ""), "b"),
        (lambda: mapped("b", "bb"), "b"),
        (lambda: optional("b"), "b"),
        (lambda: optional("b", "bb"), "b"),
        (lambda: vararg("c"), "c"),
        (lambda: varargs("d"), "d"),
        (lambda: sideffect("e"), "sideffect: 'e'"),
        (lambda: sideffect("e", optional=1), "sideffect: 'e'"),
        (lambda: sideffected("f", "a", "b"), "sideffected('f'<--'a', 'b')"),
        (lambda: sideffected("f", "ff", fn_kwarg="F"), "sideffected('f'<--'ff')",),
        (
            lambda: sideffected("f", "ff", optional=1, fn_kwarg="F"),
            "sideffected('f'<--'ff')",
        ),
        (lambda: sideffected("f", "ff", optional=1), "sideffected('f'<--'ff')"),
    ],
)
def test_modifs_str(mod, exp):
    got = str(mod())
    print(got)
    assert got == exp


@pytest.mark.parametrize(
    "mod, exp",
    [
        (lambda: mapped("b", None), "mapped('b')"),
        (lambda: mapped("b", ""), "mapped('b')"),
        (lambda: mapped("b", "bb"), "mapped('b'-->'bb')"),
        (lambda: optional("b"), "optional('b')"),
        (lambda: optional("b", "bb"), "optional('b'-->'bb')"),
        (lambda: vararg("c"), "vararg('c')"),
        (lambda: varargs("d"), "varargs('d')"),
        (lambda: sideffect("e"), "sideffect: 'e'"),
        (lambda: sideffect("e", optional=1), "sideffect?: 'e'"),
        (lambda: sideffected("f", "a", "b"), "sideffected('f'<--'a', 'b')"),
        (
            lambda: sideffected("f", "ff", fn_kwarg="F"),
            "sideffected('f'<--'ff', fn_kwarg='F')",
        ),
        (
            lambda: sideffected("f", "ff", optional=1, fn_kwarg="F"),
            "sideffected?('f'<--'ff', fn_kwarg='F')",
        ),
        (lambda: sideffected("f", "ff", optional=1), "sideffected?('f'<--'ff')"),
        (lambda: sideffected_vararg("f", "a"), "sideffected*('f'<--'a')"),
        (lambda: sideffected_varargs("f", "a", "b"), "sideffected#('f'<--'a', 'b')"),
    ],
)
def test_modifs_repr(mod, exp):
    got = repr(mod())
    print(got)
    assert got == exp


def test_recreation():
    assert optional(optional("a")) == optional("a")
    assert mapped(mapped("a", 1), 1) == mapped("a", 1)
    assert vararg(vararg("a")) == vararg("a")
    assert varargs(varargs("a")) == varargs("a")


def test_recreation_repr():
    assert repr(optional(optional("a"))) == repr(optional("a"))
    assert repr(mapped(mapped("a", 1), 1)) == repr(mapped("a", 1))
    assert repr(vararg(vararg("a"))) == repr(vararg("a"))
    assert repr(varargs(varargs("a"))) == repr(varargs("a"))


@pytest.mark.parametrize(
    "call, exp",
    [
        (lambda: sideffect(sideffect("a")), "^Expecting "),
        (lambda: sideffected("a", sideffect("a")), "^Expecting "),
        (lambda: sideffected(sideffect("a"), "a"), "^Expecting "),
        (lambda: sideffected_vararg(sideffect("a"), "a"), "^Expecting "),
        (lambda: sideffected_varargs(sideffect("a"), "a"), "^Expecting "),
    ],
)
def test_sideffected_bad(call, exp):
    with pytest.raises(ValueError, match=exp):
        call()


def test_withset_bad_kwargs():
    with pytest.raises(ValueError, match="Invalid kwargs:"):
        mapped("a", "b").withset(j=2)


@pytest.mark.parametrize(
    "mod, exp",
    [
        (lambda: "b", "'p.b'"),
        (lambda: mapped("b", None), "mapped('p.b'-->'b')"),
        (lambda: mapped("b", ""), "mapped('p.b'-->'b')"),
        (lambda: mapped("b", "bb"), "mapped('p.b'-->'bb')"),
        (lambda: optional("b"), "optional('p.b'-->'b')"),
        (lambda: optional("b", "bb"), "optional('p.b'-->'bb')"),
        (lambda: vararg("c"), "vararg('p.c')"),
        (lambda: varargs("d"), "varargs('p.d')"),
        (lambda: sideffect("e"), "sideffect: 'e'"),
        (lambda: sideffect("e", optional=1), "sideffect?: 'e'"),
        (lambda: sideffected("f", "a", "b"), "sideffected('p.f'<--'a', 'b')",),
        (
            lambda: sideffected("f", "ff", fn_kwarg="F"),
            "sideffected('p.f'<--'ff', fn_kwarg='F')",
        ),
        (
            lambda: sideffected("f", "ff", optional=1, fn_kwarg="F"),
            "sideffected?('p.f'<--'ff', fn_kwarg='F')",
        ),
        (
            lambda: sideffected("f", "ff", optional=1),
            "sideffected?('p.f'<--'ff', fn_kwarg='f')",
        ),
        (lambda: sideffected_vararg("f", "a", "b"), "sideffected*('p.f'<--'a', 'b')"),
        (lambda: sideffected_varargs("f", "a"), "sideffected#('p.f'<--'a')"),
    ],
)
def test_modifs_rename_fn(mod, exp):
    renamer = lambda n: f"p.{n}"
    got = repr(rename_dependency(mod(), renamer))
    print(got)
    assert got == exp


@pytest.mark.parametrize(
    "mod, exp",
    [
        (lambda: "s", "'D'"),
        (lambda: mapped("b", "bb"), "mapped('D'-->'bb')"),
        (lambda: optional("b"), "optional('D'-->'b')"),
        (lambda: optional("b", "bb"), "optional('D'-->'bb')"),
        (lambda: vararg("c"), "vararg('D')"),
        (lambda: varargs("d"), "varargs('D')"),
        (lambda: sideffect("e"), "sideffect: 'e'"),
        (
            lambda: sideffected("f", "a", "b", optional=1,),
            "sideffected?('D'<--'a', 'b', fn_kwarg='f')",
        ),
        (
            lambda: sideffected("f", "a", "b", optional=1, fn_kwarg="F"),
            "sideffected?('D'<--'a', 'b', fn_kwarg='F')",
        ),
    ],
)
def test_modifs_rename_str(mod, exp):
    got = repr(rename_dependency(mod(), "D"))
    print(got)
    assert got == exp
