# Copyright 2020-2020, Kostis Anagnostopoulos;
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
Utility for :term:`json pointer path` modifier

Copied from pypi/pandalone.


.. doctest::
    :hide:

    .. Workaround sphinx-doc/sphinx#6590

    >>> from graphtik.jsonpointer import *
    >>> __name__ = "graphtik.jsonpointer"
"""
import logging
import operator
import re
from collections import abc as cabc
from functools import partial
from typing import (
    Any,
    Collection,
    Iterable,
    List,
    Mapping,
    Optional,
    Sequence,
    Tuple,
    Union,
)

import numpy as np
import pandas as pd


log = logging.getLogger(__name__)
UNSET = ...  # Change this in case of troubles...


def escape_jsonpointer_part(part: str) -> str:
    """convert path-part according to the json-pointer standard"""
    return part.replace("~", "~0").replace("/", "~1")


def unescape_jsonpointer_part(part: str) -> str:
    """convert path-part according to the json-pointer standard"""
    return part.replace("~1", "/").replace("~0", "~")


def jsonp_path(jsonpointer: str) -> List[str]:
    """
    Generates the `path` parts according to jsonpointer spec.

    :param path:
        a path to resolve within document
    :return:
        The parts of the path as generator), without
        converting any step to int, and None if None.
        (the 1st step of absolute-paths is always ``''``)

    In order to support relative & absolute paths along with a sensible
    :func:`.set_path_value()`, it departs from the standard in these aspects:

    - A double slash or a slash at the end of the path restarts from the root.


    :author: Julian Berman, ankostis

    **Examples:**

        >>> jsonp_path('a')
        ['a']
        >>> jsonp_path('a/')
        ['']
        >>> jsonp_path('a/b')
        ['a', 'b']

        >>> jsonp_path('/a')
        ['', 'a']
        >>> jsonp_path('/')
        ['']

        >>> jsonp_path('')
        ['']

    """
    # Optimization: modifier caches splitted parts as a "jsonp" attribute.
    parts = getattr(jsonpointer, "jsonp", None)
    if parts is False:
        parts = [jsonpointer]
    elif parts is None:
        parts = [
            unescape_jsonpointer_part(part)
            for part in re.sub(".+(?:/$|/{2})", "/", jsonpointer).split("/")
        ]
        if parts[1:].count(""):
            last_idx = len(parts) - 1 - parts[::-1].index("")
            parts = parts[last_idx:]
    return parts


class ResolveError(KeyError):
    """
    A :class:`KeyError` raised when a json-pointer does not :func:`resolve <.resolve_path>`.
    """

    # __init__(path, step, i)

    def __str__(self):
        return (
            f"Failed resolving step (#{self.index}){self.part} of json pointer path {self.path!r}."
            "\n  Check debug logs."
        )

    @property
    def path(self):
        """the json-pointer that failed to resolve"""
        return self.args[0]

    @property
    def part(self):
        """the part where the resolution broke"""
        return self.args[1]

    @property
    def index(self) -> int:
        """the part's position where the resolution broke"""
        return self.args[2]


def _log_overwrite(part, doc, child):
    log.warning(
        "Overwritting json-pointer part %r in a %i-len subdoc over scalar %r.",
        part,
        len(doc),
        child,
    )


def resolve_path(
    doc: Union[Sequence, Mapping],
    path: Union[str, Iterable[str]],
    default=UNSET,
    root=UNSET,
    descend_objects=None,
):
    """
    Resolve *roughly like* a json-pointer `path` within the referenced `doc`.

    :param doc:
        the current document to start searching `path`
        (which may be different than `root`)
    :param path:
        An absolute or relative json-pointer expression to resolve within `doc` document
        (or just the unescaped steps).

        .. Attention::
            Relative paths DO NOT support the json-pointer extension
            https://tools.ietf.org/id/draft-handrews-relative-json-pointer-00.html

    :param default:
        the value to return if `path` does not resolve; by default, it raises.
    :param root:
        From where to start resolving absolute paths or double-slashes(``//``), or
        final slashes.
        If ``None``, only relative paths allowed; by default,
        the given `doc` is assumed as root (so absolute paths are also accepted).
    :param descend_objects:
        If true, a last ditch effort is made for each part, whether it matches
        the name of an attribute of the parent item.

    :return:
        the resolved doc-item
    :raises ResolveError:
        if `path` cannot resolve and no `default` given
    :raises ValueError:
        if `path` was an absolute path a  ``None`` `root` had been given.

    In order to support relative & absolute paths along with a sensible
    :func:`.set_path_value()`, it departs from the standard in these aspects:

    - Supports also relative paths (but not the official extension).
    - For arrays, it tries 1st as an integer, and then falls back to normal indexing
      (usefull when accessing pandas).
    - A ``/`` path does not bring the value of empty``''`` key but the whole document
      (aka the "root").
    - A double slash or a slash at the end of the path restarts from the root.


    **Examples:**

        >>> dt = {
        ...     'pi':3.14,
        ...     'foo':'bar',
        ...     'df': pd.DataFrame(np.ones((3,2)), columns=list('VN')),
        ...     'sub': {
        ...         'sr': pd.Series({'abc':'def'}),
        ...     }
        ... }
        >>> resolve_path(dt, '/pi')
        3.14

        >>> resolve_path(dt, 'df/V')
        0    1.0
        1    1.0
        2    1.0
        Name: V, dtype: float64

        >>> resolve_path(dt, '/pi/BAD')
        Traceback (most recent call last):
        ...
        graphtik.jsonpointer.ResolveError: Failed resolving step (#2)BAD of json pointer path '/pi/BAD'.
          Check debug logs.


        >>> resolve_path(dt, '/pi/BAD', 'Hi!')
        'Hi!'

    :author: Julian Berman, ankostis
    """
    part_indexers = [
        lambda doc, part: doc[int(part)],
        operator.getitem,
        *((getattr,) if descend_objects else ()),
    ]

    if root is ...:
        root = doc

    parts = jsonp_path(path) if isinstance(path, str) else path
    for i, part in enumerate(parts):
        if part == "":
            if root is None:
                raise ValueError(
                    f"Absolute step #{i} of json-pointer {path!r} without `root`!"
                )
            doc = root
            continue

        for indexer in part_indexers:
            try:
                doc = indexer(doc, part)
                break
            except Exception as ex:
                log.debug(
                    "indexer %s failed on step (#%i)%s of json-pointer(%r) with doc(%s), due to: %s ",
                    indexer,
                    i,
                    part,
                    path,
                    doc,
                    ex,
                    # Don't log int(part) stack-traces.
                    exc_info=type(ex) is not ValueError
                    or not str(ex).startswith("invalid literal for int()"),
                )
        else:
            if default is ...:
                raise ResolveError(path, part, i)

            return default

    return doc


def contains_path(
    doc: Union[Sequence, Mapping],
    path: Union[str, Iterable[str]],
    root=UNSET,
    descend_objects=None,
) -> bool:
    """Test if `doc` has a value for json-pointer path by calling :func:`.resolve_path()`. """
    try:
        return resolve_path(doc, path, root=root, descend_objects=descend_objects)
    except ResolveError:
        return False


def is_collection(item):
    return isinstance(item, cabc.Collection) and not isinstance(
        item, (str, cabc.ByteString)
    )


def list_scouter(doc, part, container_factory, overwrite):
    """
    Get `doc `list item  by (int) `part`, or create a new one from `container_factory`.

    NOTE: must kick before collection-scouter to handle special non-integer ``.`` index.
    """
    if part == "-":  # It means the position after the last item.
        item = container_factory()
        doc.append(item)
        return item

    part = int(part)  # Let it bubble, to try next (as key-item).
    if overwrite and log.isEnabledFor(logging.WARNING):
        try:
            child = doc[part]
        except LookupError:
            # Out of bounds, but ok for now, will break also on assignment, below.
            pass
        else:
            _log_overwrite(part, doc, child)
    elif not overwrite:
        try:
            child = doc[part]
            if is_collection(child):
                return child
        except LookupError:
            # Ignore resolve errors, assignment errors below are more important.
            pass

    item = container_factory()
    doc[part] = item
    return item


def collection_scouter(doc, part, container_factory, overwrite):
    """Get item `part` from `doc` collection, or create a new ome from `container_factory`."""
    if overwrite and log.isEnabledFor(logging.WARNING) and part in doc:
        _log_overwrite(part, doc, doc[part])
    elif not overwrite:
        try:
            child = doc[part]
            if is_collection(child):
                return child
        except LookupError:
            # Ignore resolve errors, assignment errors below are more important.
            pass

    item = container_factory()
    doc[part] = item
    return item


def object_scouter(doc, part, value, container_factory, overwrite):
    """Get attribute `part` in `doc` object, or create a new one from `container_factory`."""
    if overwrite and log.isEnabledFor(logging.WARNING) and hasattr(doc, part):
        _log_overwrite(part, doc, getattr(doc, part))
    elif not overwrite:
        try:
            child = getattr(doc, part)
            if is_collection(child):
                return child
        except AttributeError:
            # Ignore resolve errors, assignment errors below are more important.
            pass

    item = container_factory()
    setattr(doc, part, item)
    return item


def set_path_value(
    doc: Union[Sequence, Mapping],
    path: Union[str, Iterable[str]],
    value,
    container_factory=dict,
    root=UNSET,
    descend_objects=None,
):
    """
    Resolve a ``jsonpointer`` within the referenced ``doc``.

    :param doc:
        the document to extend & insert `value`
    :param path:
        An absolute or relative json-pointer expression to resolve within `doc` document
        (or just the unescaped steps).

        For sequences (arrays), it supports the special index dahs(``-``) char,
        to refer to the position beyond the last item, as by the spec, BUT
        it does not raise - it always add a new item.

        .. Attention::
            Relative paths DO NOT support the json-pointer extension
            https://tools.ietf.org/id/draft-handrews-relative-json-pointer-00.html

    :param container_factory:
        a factory producing the collection instances to extend missing steps,
        (usually a mapping or a sequence)
    :param root:
        From where to start resolving absolute paths, double-slashes(``//``) or
        final slashes.
        If ``None``, only relative paths allowed; by default,
        the given `doc` is assumed as root (so absolute paths are also accepted).
    :param descend_objects:
        If true, a last ditch effort is made for each part, whether it matches
        the name of an attribute of the parent item.

    :raises: ValueError (if jsonpointer empty, missing, invalid-content)

    See :func:`resolve_path()` for departures from the json-pointer standard
    """

    part_scouters = [
        list_scouter,
        collection_scouter,
        *((object_scouter,) if descend_objects else ()),
    ]

    if root is ...:
        root = doc

    parts = jsonp_path(path) if isinstance(path, str) else list(path)
    last_part = len(parts) - 1
    for i, part in enumerate(parts):
        if part == "":
            if root is None:
                raise ValueError(
                    f"Absolute step #{i} of json-pointer {path!r} without `root`!"
                )
            doc = root
            continue

        if i == last_part:
            fact = lambda: value
            overwrite = True
        else:
            fact = container_factory
            overwrite = False

        for scouter in part_scouters:
            try:
                doc = scouter(doc, part, fact, overwrite)
                break
            except Exception as ex:
                log.debug(
                    "scouter %s failed on step (#%i)%s of json-pointer(%r) with doc(%s), due to: %s",
                    scouter,
                    i,
                    part,
                    path,
                    doc,
                    ex,
                    # Don't log int(part) stack-traces.
                    exc_info=type(ex) is not ValueError
                    or not str(ex).startswith("invalid literal for int()"),
                )
        else:
            raise ValueError(
                f"Failed setting step (#{i}){part} of json pointer path {path!r}!"
                "\n  Check debug logs."
            )


def _update_paths(
    doc,
    paths_vals: Collection[Tuple[List[str], Any]],
    container_factory=dict,
    root=UNSET,
    descend_objects=None,
) -> None:
    # The `group` is a list of paths with common prefix (root)
    # currently being built.
    group_prefix, group = None, ()
    for p, v in paths_vals + [((...,), ...)]:
        assert len(p) >= 1 or v is ..., locals()

        next_prefix = p[0]
        if next_prefix != group_prefix:
            if len(p) == 1 and v is not ...:
                # Assign value and proceed to the next one,
                # THOUGH if a deeper path with this same prefix follows,
                # it will overwrite the value just written.
                doc[next_prefix] = v
            else:
                if group_prefix:  # Is it past the 1st loop?
                    ## Recurse into sub-group.
                    #
                    child = None
                    if group_prefix in doc:
                        child = doc[group_prefix]
                        if not is_collection(child):
                            child = None
                            _log_overwrite(group_prefix, doc, child)

                    if not child:
                        child = doc[group_prefix] = container_factory()
                    _update_paths(child, [(p[1:], v) for p, v in group])

                group_prefix, group = (next_prefix, [(p, v)])  # prepare the next group
        else:
            assert len(p) > 1, locals()  # shortest path switches group.
            group.append((p, v))


def update_paths(
    doc,
    paths_vals: Collection[Tuple[List[str], Any]],
    container_factory=dict,
    root=UNSET,
    descend_objects=None,
) -> None:
    paths_vals = sorted(paths_vals)
    _update_paths(
        doc,
        [(jsonp_path(p), v) for p, v in paths_vals],
        container_factory,
        root,
        descend_objects,
    )



def list_popper(doc: Sequence, part, do_pop):
    """Call :func:`collection_popper()` with integer `part`."""
    return collection_popper(doc, int(part), do_pop)


def collection_popper(doc: Collection, part, do_pop):
    """Resolve `part` in `doc`, or pop it with `default`if `do_pop`."""
    return doc.pop(part) if do_pop else doc[part]


def object_popper(doc: Collection, part, do_pop):
    """Resolve `part` in `doc` attributes, or ``delattr`` it, returning its value or `default`."""
    item = getattr(doc, part)
    if do_pop:
        delattr(doc, part)
    return item


def pop_path(
    doc: Union[Sequence, Mapping],
    path: Union[str, Iterable[str]],
    default=UNSET,
    root=UNSET,
    descend_objects=None,
):
    """
    Delete and return the item referenced by json-pointer `path` from the nested `doc` .

    :param doc:
        the current document to start searching `path`
        (which may be different than `root`)
    :param path:
        An absolute or relative json-pointer expression to resolve within `doc` document
        (or just the unescaped steps).

        .. Attention::
            Relative paths DO NOT support the json-pointer extension
            https://tools.ietf.org/id/draft-handrews-relative-json-pointer-00.html

    :param default:
        the value to return if `path` does not resolve; by default, it raises.
    :param root:
        From where to start resolving absolute paths or double-slashes(``//``), or
        final slashes.
        If ``None``, only relative paths allowed; by default,
        the given `doc` is assumed as root (so absolute paths are also accepted).
    :param descend_objects:
        If true, a last ditch effort is made for each part, whether it matches
        the name of an attribute of the parent item.

    :return:
        the deleted item in `doc`, or `default` if given and `path`  didn't exist
    :raises ResolveError:
        if `path` cannot resolve and no `default` given
    :raises ValueError:
        if `path` was an absolute path a  ``None`` `root` had been given.

    See :func:`resolve_path()` for departures from the json-pointer standard

    **Examples:**

        >>> dt = {
        ...     'pi':3.14,
        ...     'foo':'bar',
        ...     'df': pd.DataFrame(np.ones((3,2)), columns=list('VN')),
        ...     'sub': {
        ...         'sr': pd.Series({'abc':'def'}),
        ...     }
        ... }
        >>> resolve_path(dt, '/pi', default=UNSET)
        3.14

        >>> resolve_path(dt, 'df/V')
        0    1.0
        1    1.0
        2    1.0
        Name: V, dtype: float64

        >>> resolve_path(dt, '/pi/BAD', 'Hi!')
        'Hi!'

    :author: Julian Berman, ankostis
    """
    part_poppers = [
        list_popper,
        collection_popper,
        *((object_popper,) if descend_objects else ()),
    ]

    if root is ...:
        root = doc

    parts = jsonp_path(path) if isinstance(path, str) else list(path)
    last_part = len(parts) - 1
    for i, part in enumerate(parts):
        if part == "":
            if root is None:
                raise ValueError(
                    f"Absolute step #{i} of json-pointer {path!r} without `root`!"
                )
            doc = root
            continue

        for popper in part_poppers:
            try:
                doc = popper(doc, part, i == last_part)
                break
            except Exception as ex:
                log.debug(
                    "popper %s failed on step (#%i)%s of json-pointer(%r) with doc(%s), due to: %s ",
                    popper,
                    i,
                    part,
                    path,
                    doc,
                    ex,
                    # Don't log int(part) stack-traces.
                    exc_info=type(ex) is not ValueError
                    or not str(ex).startswith("invalid literal for int()"),
                )
        else:
            if default is ...:
                raise ResolveError(path, part, i)

            return default

    return doc
