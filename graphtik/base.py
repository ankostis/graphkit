# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Generic utilities and exceptions.

.. doctest::
    :hide:

    .. Workaround sphinx-doc/sphinx#6590

    >>> from graphtik.base import *
    >>> __name__ = "graphtik.base"
"""

import abc
import inspect
import logging
from functools import partial, partialmethod, wraps
from typing import Collection, Optional, Tuple, Union

Items = Union[Collection, str, None]

log = logging.getLogger(__name__)


class MultiValueError(ValueError):
    def __str__(self):
        """Assuming it has been called with ``MultiValueError(msg, ex1, ...) #"""
        return str(self.args[0])  # pylint: disable=unsubscriptable-object


class AbortedException(Exception):
    """
    Raised from Network when :func:`.abort_run()` is called, and contains the solution ...

    with any values populated so far.
    """


class IncompleteExecutionError(Exception):
    """
    Reported when any :term:`endured`/:term:`reschedule` operations were are canceled.

    The exception contains 3 arguments:

    1. the causal errors and conditions (1st arg),
    2. the list of collected exceptions (2nd arg), and
    3. the solution instance (3rd argument), to interrogate for more.

    Returned by :meth:`.check_if_incomplete()` or raised by :meth:`.scream_if_incomplete()`.
    """

    def __str__(self):
        return self.args[0]  # pylint: disable=unsubscriptable-object


class Token(str):
    """Guarantee equality, not(!) identity, across processes."""

    __slots__ = ("hashid",)

    def __new__(cls, s):
        return super().__new__(cls, f"<{s}>")

    def __init__(self, *args):
        import random

        self.hashid = random.randint(-(2 ** 32), 2 ** 32 - 1)

    def __eq__(self, other):
        return self.hashid == getattr(other, "hashid", None)

    def __hash__(self):
        return self.hashid

    def __getstate__(self):
        return self.hashid

    def __setstate__(self, state):
        self.hashid = state

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __bool__(self):
        """Always `True`, even if empty string."""
        return True

    def __repr__(self):
        """Avoid 'ticks' around repr."""
        return self.__str__()


UNSET = Token("UNSET")


def first_solid(*tristates, default=None):
    """Utility combining multiple tri-state booleans."""
    from boltons.iterutils import first

    return first(tristates, default=default, key=lambda i: i is not None)


def aslist(i, argname, allowed_types=list):
    """Utility to accept singular strings as lists, and None --> []."""
    if not i:
        return i if isinstance(i, allowed_types) else []

    if isinstance(i, str):
        i = [i]
    elif not isinstance(i, allowed_types):
        try:
            i = list(i)
        except Exception as ex:
            raise ValueError(f"Cannot list-ize {argname}({i!r}) due to: {ex}") from None

    return i


def astuple(i, argname, allowed_types=tuple):
    if not i:
        return i if isinstance(i, allowed_types) else ()

    if isinstance(i, str):
        i = (i,)
    elif not isinstance(i, allowed_types):
        try:
            i = tuple(i)
        except Exception as ex:
            raise ValueError(
                f"Cannot tuple-ize {argname}({i!r}) due to: {ex}"
            ) from None

    return i


def func_name(
    fn, default=..., mod=None, fqdn=None, human=None, partials=None
) -> Optional[str]:
    """
    FQDN of `fn`, descending into partials to print their args.

    :param default:
        What to return if it fails; by default it raises.
    :param mod:
        when true, prepend module like ``module.name.fn_name``
    :param fqdn:
        when true, use ``__qualname__`` (instead of ``__name__``)
        which differs mostly on methods, where it contains class(es),
        and locals, respectively (:pep:`3155`).
        *Sphinx* uses `fqdn=True` for generating IDs.
    :param human:
        when true, explain built-ins, and assume ``partials=True`` (if that was None)
    :param partials:
        when true (or omitted & `human` true), partials denote their args
        like ``fn({"a": 1}, ...)``

    :return:
        a (possibly dot-separated) string, or `default` (unless this is ``...```).
    :raises:
        Only if default is ``...``, otherwise, errors debug-logged.


    **Examples**

        >>> func_name(func_name)
        'func_name'
        >>> func_name(func_name, mod=1)
        'graphtik.base.func_name'
        >>> func_name(func_name.__format__, fqdn=0)
        '__format__'
        >>> func_name(func_name.__format__, fqdn=1)
        'function.__format__'

    Even functions defined in docstrings are reported:

        >>> def f():
        ...     def inner():
        ...         pass
        ...     return inner

        >>> func_name(f, mod=1, fqdn=1)
        'graphtik.base.f'
        >>> func_name(f(), fqdn=1)
        'f.<locals>.inner'

    On failures, arg `default` controls the outcomes:

    TBD
    """
    if partials is None:
        partials = human

    if isinstance(fn, (partial, partialmethod)):
        # Always bubble-up errors.
        fn_name = func_name(fn.func, default, mod, fqdn, human)
        if partials:
            args = ", ".join(str(i) for i in fn.args)
            kw = ", ".join(f"{k}={v}" for k, v in fn.keywords.items())
            args_str = ", ".join(i for i in (args, kw) + ("...",) if i)
            fn_name = f"{fn_name}({args_str})"

        return fn_name

    try:
        if human and inspect.isbuiltin(fn):
            return str(fn)
        fn_name = fn.__qualname__ if fqdn else fn.__name__
        assert fn_name

        mod_name = getattr(fn, "__module__", None)
        if mod and mod_name:
            fn_name = ".".join((mod_name, fn_name))
        return fn_name
    except Exception as ex:
        if default is ...:
            raise
        log.debug(
            "Ignored error while inspecting %r name: %s", fn, ex,
        )
        return default


def _un_partial_ize(func):
    """
    Alter functions working on 1st arg being a callable, to descend it if it's a partial.
    """

    @wraps(func)
    def wrapper(fn, *args, **kw):
        if isinstance(fn, (partial, partialmethod)):
            fn = fn.func
        return func(fn, *args, **kw)

    return wrapper


@_un_partial_ize
def func_source(fn, default=..., human=None) -> Optional[Tuple[str, int]]:
    """
    Like :func:`inspect.getsource` supporting partials.

    :param default:
        If given, better be a 2-tuple respecting types,
        or ``...``, to raise.
    :param human:
        when true, denote builtins like python does
    """
    try:
        if inspect.isbuiltin(fn):
            if human:
                return inspect.getdoc(fn).splitlines()[0]
            else:
                return str(fn)
        return inspect.getsource(fn)
    except Exception as ex:
        if default is ...:
            raise
        log.debug(
            "Ignored error while inspecting %r sources: %s", fn, ex,
        )
        return default


@_un_partial_ize
def func_sourcelines(fn, default=..., human=None) -> Optional[Tuple[str, int]]:
    """
    Like :func:`inspect.getsourcelines` supporting partials.

    :param default:
        If given, better be a 2-tuple respecting types,
        or ``...``, to raise.
    """
    try:
        if human and inspect.isbuiltin(fn):
            return [str(fn)], -1
        return inspect.getsourcelines(fn)
    except Exception as ex:
        if default is ...:
            raise
        log.debug(
            "Ignored error while inspecting %r sourcelines: %s", fn, ex,
        )
        return default


def jetsam(ex, locs, *salvage_vars: str, annotation="jetsam", **salvage_mappings):
    """
    Annotate exception with salvaged values from locals() and raise!

    :param ex:
        the exception to annotate
    :param locs:
        ``locals()`` from the context-manager's block containing vars
        to be salvaged in case of exception

        ATTENTION: wrapped function must finally call ``locals()``, because
        *locals* dictionary only reflects local-var changes after call.
    :param annotation:
        the name of the attribute to attach on the exception
    :param salvage_vars:
        local variable names to save as is in the salvaged annotations dictionary.
    :param salvage_mappings:
        a mapping of destination-annotation-keys --> source-locals-keys;
        if a `source` is callable, the value to salvage is retrieved
        by calling ``value(locs)``.
        They take precedence over`salvage_vars`.

    :raises:
        any exception raised by the wrapped function, annotated with values
        assigned as attributes on this context-manager

    - Any attributes attached on this manager are attached as a new dict on
      the raised exception as new  ``jetsam`` attribute with a dict as value.
    - If the exception is already annotated, any new items are inserted,
      but existing ones are preserved.

    **Example:**

    Call it with managed-block's ``locals()`` and tell which of them to salvage
    in case of errors::


        try:
            a = 1
            b = 2
            raise Exception()
        exception Exception as ex:
            jetsam(ex, locals(), "a", b="salvaged_b", c_var="c")
            raise

    And then from a REPL::

        import sys
        sys.last_value.jetsam
        {'a': 1, 'salvaged_b': 2, "c_var": None}

    ** Reason:**

    Graphs may become arbitrary deep.  Debugging such graphs is notoriously hard.

    The purpose is not to require a debugger-session to inspect the root-causes
    (without precluding one).

    Naively salvaging values with a simple try/except block around each function,
    blocks the debugger from landing on the real cause of the error - it would
    land on that block;  and that could be many nested levels above it.
    """
    ## Fail EARLY before yielding on bad use.
    #
    assert isinstance(ex, Exception), ("Bad `ex`, not an exception dict:", ex)
    assert isinstance(locs, dict), ("Bad `locs`, not a dict:", locs)
    assert all(isinstance(i, str) for i in salvage_vars), (
        "Bad `salvage_vars`!",
        salvage_vars,
    )
    assert salvage_vars or salvage_mappings, "No `salvage_mappings` given!"
    assert all(isinstance(v, str) or callable(v) for v in salvage_mappings.values()), (
        "Bad `salvage_mappings`:",
        salvage_mappings,
    )

    ## Merge vars-mapping to save.
    for var in salvage_vars:
        if var not in salvage_mappings:
            salvage_mappings[var] = var

    try:
        annotations = getattr(ex, annotation, None)
        if not isinstance(annotations, dict):
            annotations = {}
            setattr(ex, annotation, annotations)

        ## Salvage those asked
        for dst_key, src in salvage_mappings.items():
            try:
                salvaged_value = src(locs) if callable(src) else locs.get(src)
                annotations.setdefault(dst_key, salvaged_value)
            except Exception as ex:
                log.warning(
                    "Suppressed error while salvaging jetsam item (%r, %r): %r"
                    % (dst_key, src, ex)
                )
    except Exception as ex2:
        log.warning("Suppressed error while annotating exception: %r", ex2, exc_info=1)
        raise ex2
