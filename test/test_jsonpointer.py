# Copyright 2020-2020, Kostis Anagnostopoulos;
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
Test utilities for :term:`json pointer path` modifier.

Copied from pypi/pandalone.
"""
import pytest

from graphtik.jsonpointer import (
    escape_jsonpointer_part,
    iter_jsonpointer_parts,
    iter_jsonpointer_parts_relaxed,
    resolve_jsonpointer,
    set_jsonpointer,
    unescape_jsonpointer_part,
)


def test_jsonpointer_escape_parts():
    def un_esc(part):
        return unescape_jsonpointer_part(escape_jsonpointer_part(part))

    part = "hi/there"
    assert un_esc(part) == part
    part = "hi~there"
    assert un_esc(part) == part
    part = "/hi~there/"
    assert un_esc(part) == part


def test_iter_jsonpointer_empty():
    assert list(iter_jsonpointer_parts("")) == []
    assert list(iter_jsonpointer_parts_relaxed("")) == [""]


def test_iter_jsonpointer_root():
    assert list(iter_jsonpointer_parts("/")) == [""]
    assert list(iter_jsonpointer_parts_relaxed("/")) == ["", ""]


def test_iter_jsonpointer_regular():
    assert list(iter_jsonpointer_parts("/a")) == ["a"]
    assert list(iter_jsonpointer_parts_relaxed("/a")) == ["", "a"]

    assert list(iter_jsonpointer_parts("/a/b")) == ["a", "b"]
    assert list(iter_jsonpointer_parts_relaxed("/a/b")) == ["", "a", "b"]


def test_iter_jsonpointer_folder():
    assert list(iter_jsonpointer_parts("/a/")) == ["a", ""]
    assert list(iter_jsonpointer_parts_relaxed("/a/")) == ["", "a", ""]


def test_iter_jsonpointer_non_absolute():
    with pytest.raises(ValueError):
        list(iter_jsonpointer_parts("a"))
    with pytest.raises(ValueError):
        list(iter_jsonpointer_parts("a/b"))


def test_iter_jsonpointer_None():
    with pytest.raises(AttributeError):
        list(iter_jsonpointer_parts(None))
    with pytest.raises(AttributeError):
        list(iter_jsonpointer_parts_relaxed(None))


def test_iter_jsonpointer_with_spaces():
    assert list(iter_jsonpointer_parts("/ some ")) == [" some "]
    assert list(iter_jsonpointer_parts("/ some /  ")) == [" some ", "  "]

    assert list(iter_jsonpointer_parts_relaxed(" some ")) == [" some "]
    assert list(iter_jsonpointer_parts_relaxed(" some /  ")) == [" some ", "  "]


@pytest.mark.parametrize(
    "inp, exp",
    [
        ("/a", ["a"]),
        ("/a/", ["a", ""]),
        ("/a/b", ["a", "b"]),
        ("/a/b/", ["a", "b", ""]),
        ("/a//b", ["a", "", "b"]),
        ("/a/../b", ["a", "..", "b"]),
        ("/", [""]),
        ("", []),
        ("/ some ", [" some "]),
        ("/ some /", [" some ", ""]),
        ("/ some /  ", [" some ", "  "]),
        ("/ some /  /", [" some ", "  ", ""]),
        (None, AttributeError()),
        ("a", ValueError()),
    ],
)
def test_iter_jsonpointer_massive(inp, exp):
    if isinstance(exp, Exception):
        with pytest.raises(type(exp), match=str(exp)):
            list(iter_jsonpointer_parts(inp))
    else:
        assert list(iter_jsonpointer_parts(inp)) == exp


@pytest.mark.parametrize(
    "inp, exp",
    [
        ("/a", ["", "a"]),
        ("/a/", ["", "a", ""]),
        ("/a/b", ["", "a", "b"]),
        ("/a/b/", ["", "a", "b", ""]),
        ("/a//b", ["", "a", "", "b"]),
        ("/", ["", ""]),
        ("", [""]),
        ("/ some ", ["", " some "]),
        ("/ some /", ["", " some ", ""]),
        ("/ some /  ", ["", " some ", "  "]),
        (None, AttributeError()),
        ("a", ["a"]),
        ("a/", ["a", ""]),
        ("a/b", ["a", "b"]),
        ("a/b/", ["a", "b", ""]),
        ("a/../b/.", ["a", "..", "b", "."]),
        ("a/../b/.", ["a", "..", "b", "."]),
        (" some ", [" some "]),
        (" some /", [" some ", ""]),
        (" some /  ", [" some ", "  "]),
        (" some /  /", [" some ", "  ", ""]),
    ],
)
def test_iter_jsonpointer_relaxed_massive(inp, exp):
    if isinstance(exp, Exception):
        with pytest.raises(type(exp), match=str(exp)):
            list(iter_jsonpointer_parts_relaxed(inp))


def test_resolve_jsonpointer_existing():
    doc = {"foo": 1, "bar": [11, {"a": 222}]}

    path = "/foo"
    assert resolve_jsonpointer(doc, path) == 1

    path = "/bar/0"
    assert resolve_jsonpointer(doc, path) == 11

    path = "/bar/1/a"
    assert resolve_jsonpointer(doc, path) == 222


def test_resolve_jsonpointer_sequence():
    doc = [1, [22, 33]]

    path = "/0"
    assert resolve_jsonpointer(doc, path) == 1

    path = "/1"
    assert resolve_jsonpointer(doc, path) == [22, 33]

    path = "/1/0"
    assert resolve_jsonpointer(doc, path) == 22
    path = "/1/1"
    assert resolve_jsonpointer(doc, path) == 33


def test_resolve_jsonpointer_missing_screams():
    doc = {}

    path = "/foo"
    with pytest.raises(ValueError):
        resolve_jsonpointer(doc, path)


def test_resolve_jsonpointer_empty_path():
    doc = {}
    path = ""
    assert resolve_jsonpointer(doc, path) == doc

    doc = {"foo": 1}
    assert resolve_jsonpointer(doc, path) == doc


def test_resolve_jsonpointer_examples_from_spec():
    def _doc():
        return {
            r"foo": ["bar", r"baz"],
            r"": 0,
            r"a/b": 1,
            r"c%d": 2,
            r"e^f": 3,
            r"g|h": 4,
            r"i\\j": 5,
            r"k\"l": 6,
            r" ": 7,
            r"m~n": 8,
        }

    cases = [
        (r"", _doc()),
        (r"/foo", ["bar", "baz"]),
        (r"/foo/0", "bar"),
        (r"/", 0),
        (r"/a~1b", 1),
        (r"/c%d", 2),
        (r"/e^f", 3),
        (r"/g|h", 4),
        (r"/i\\j", 5),
        (r"/k\"l", 6),
        (r"/ ", 7),
        (r"/m~0n", 8),
    ]
    for path, exp in cases:
        doc = _doc()
        assert resolve_jsonpointer(doc, path) == exp


def test_set_jsonpointer_empty_doc():
    doc = {}
    path = "/foo"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value

    doc = {}
    path = "/foo/bar"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value


def test_resolve_jsonpointer_root_path_only():
    doc = {}
    path = "/"
    with pytest.raises(ValueError):
        assert resolve_jsonpointer(doc, path) == doc

    doc = {"foo": 1}
    with pytest.raises(ValueError):
        assert resolve_jsonpointer(doc, path) == doc

    doc = {"": 1}
    assert resolve_jsonpointer(doc, path) == doc[""]


def test_set_jsonpointer_replace_value():
    doc = {"foo": "bar", 1: 2}
    path = "/foo"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2

    doc = {"foo": 1, 1: 2}
    path = "/foo"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2

    doc = {"foo": {"bar": 1}, 1: 2}
    path = "/foo"
    value = 2
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2


def test_set_jsonpointer_append_path():
    doc = {"foo": "bar", 1: 2}
    path = "/foo/bar"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2

    doc = {"foo": "bar", 1: 2}
    path = "/foo/bar/some/other"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2


def test_set_jsonpointer_append_path_preserves_intermediate():
    doc = {"foo": {"bar": 1}, 1: 2}
    path = "/foo/foo2"
    value = "value"
    set_jsonpointer(doc, path, value)
    print(doc)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2
    assert resolve_jsonpointer(doc, "/foo/bar") == 1


def test_set_jsonpointer_missing():
    doc = {"foo": 1, 1: 2}
    path = "/foo/bar"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2

    doc = {"foo": 1, 1: 2}
    path = "/foo/bar/some/other"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert doc[1] == 2


def test_set_jsonpointer_sequence():
    doc = [1, 2]
    path = "/1"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value

    doc = [1, 2]
    path = "/1/foo/bar"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value


def test_set_jsonpointer_sequence_insert_end():
    doc = [0, 1]
    path = "/2"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, path) == value
    assert resolve_jsonpointer(doc, "/0") == 0
    assert resolve_jsonpointer(doc, "/1") == 1

    doc = [0, 1]
    path = "/-"
    value = "value"
    set_jsonpointer(doc, path, value)
    assert resolve_jsonpointer(doc, "/2") == value
    assert resolve_jsonpointer(doc, "/0") == 0
    assert resolve_jsonpointer(doc, "/1") == 1


def test_set_jsonpointer_sequence_out_of_bounds():
    doc = [0, 1]
    path = "/3"
    value = "value"
    with pytest.raises(ValueError):
        set_jsonpointer(doc, path, value)


def test_set_jsonpointer_sequence_with_str_screams():
    doc = [0, 1]
    path = "/str"
    value = "value"
    with pytest.raises(ValueError):
        set_jsonpointer(doc, path, value)
