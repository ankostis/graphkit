# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
import logging

import pytest
import itertools as itt

from graphkit import base


def test_failure_jetsam_without_failure(caplog):
    a = 1
    caplog.set_level(logging.INFO)
    with base.failure_jetsam() as jetsam:
        a = 2
        jetsam.config_salvation({}, a="a")

    assert "No-op jetsam!  Call" not in caplog.text
    assert "Supressed error" not in caplog.text
    assert a == 2


def test_failure_jetsam_not_config_salvationed(caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(Exception, match="ABC") as excinfo:
        with base.failure_jetsam():
            raise Exception("ABC")

    assert excinfo.value.graphkit_jetsam == {}
    assert "No-op jetsam!  Call" in caplog.text
    assert "Supressed error" not in caplog.text


@pytest.mark.parametrize("locs", [None, (), [], [0], "bad"])
def test_failure_jetsam_bad_locals(locs, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AssertionError, match="Bad `locs`") as excinfo:
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation(locs, a="a")
            raise Exception()

    assert isinstance(excinfo.value.graphkit_jetsam, dict)
    assert "No-op jetsam!" in caplog.text
    assert "Supressed error while annotating exception" not in caplog.text


@pytest.mark.parametrize("keys", [{"k": None}, {"k": ()}, {"k": []}, {"k": [0]}])
def test_failure_jetsam_bad_keys(keys, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AssertionError, match="Bad `keys_to_salvage`") as excinfo:
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation({}, **keys)
            raise Exception()

    assert isinstance(excinfo.value.graphkit_jetsam, dict)
    assert "No-op jetsam!" in caplog.text
    assert "Supressed error while annotating exception" not in caplog.text


@pytest.mark.parametrize("annotation", [None, (), [], [0], "bad"])
def test_failure_jetsam_bad_existing_annotation(annotation, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(Exception, match="ABC") as excinfo:
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation({}, a="a")
            ex = Exception("ABC")
            ex.graphkit_jetsam = annotation
            raise ex

    assert isinstance(excinfo.value.graphkit_jetsam, dict)
    assert "Supressed error while annotating exception" not in caplog.text


@pytest.mark.parametrize("locs", [None, (), [], [0], "bad"])
def test_failure_jetsam_bad_locals_given(locs, caplog):
    caplog.set_level(logging.INFO)
    with pytest.raises(AssertionError, match="`locs` given to jetsam") as excinfo:
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation(locs, a="a")
            raise Exception("ABC")

    assert isinstance(excinfo.value.graphkit_jetsam, dict)


def test_failure_jetsam_dummy_locals(caplog):
    with pytest.raises(Exception, match="ABC") as excinfo:
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation({"a": 1}, a="a", bad="bad")

            raise Exception("ABC")

    assert isinstance(excinfo.value.graphkit_jetsam, dict)
    assert excinfo.value.graphkit_jetsam == {"a": 1, "bad": None}
    assert "Supressed error" not in caplog.text


def _jetsamed_fn():
    b = 1
    with base.failure_jetsam() as jetsam:
        jetsam.config_salvation(
            locals(), a=lambda: locals()["a"], b=lambda: locals()["b"]
        )

        a = 1
        b = 2
        raise Exception("ABC", a, b)


def test_failure_jetsam_simple(caplog):
    with pytest.raises(Exception, match="ABC") as excinfo:
        _jetsamed_fn()
    a = 0
    b = 0
    assert isinstance(excinfo.value.graphkit_jetsam, dict)
    # NOTE, `b` defined in neer scope, not visible by locals().
    assert excinfo.value.graphkit_jetsam == {"a": 1, "b": 2}
    assert "Supressed error" not in caplog.text


def test_failure_jetsam_nested():
    def inner():
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation(locals(), fn="fn")

            _fn = "inner"
            raise Exception("ABC")

    def outer():
        with base.failure_jetsam() as jetsam:
            jetsam.config_salvation(locals(), fn="fn")

            _fn = "outer"
            inner()

    with pytest.raises(Exception, match="ABC") as excinfo:
        outer()

    assert excinfo.value.graphkit_jetsam == {"_fn": "inner"}
