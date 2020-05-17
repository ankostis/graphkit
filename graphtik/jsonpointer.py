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
from collections import abc as cabc

import pandas as pd
import numpy as np


def escape_jsonpointer_part(part):
    return part.replace("~", "~0").replace("/", "~1")


def unescape_jsonpointer_part(part):
    return part.replace("~1", "/").replace("~0", "~")


def iter_jsonpointer_parts(jsonpath):
    """
    Generates the ``jsonpath`` parts according to jsonpointer spec.

    :param str jsonpath:  a jsonpath to resolve within document
    :return:              The parts of the path as generator), without
                          converting any step to int, and None if None.

    :author: Julian Berman, ankostis

    Examples::

        >>> list(iter_jsonpointer_parts('/a/b'))
        ['a', 'b']

        >>> list(iter_jsonpointer_parts('/a//b'))
        ['a', '', 'b']

        >>> list(iter_jsonpointer_parts('/'))
        ['']

        >>> list(iter_jsonpointer_parts(''))
        []


    But paths are strings begining (NOT_MPL: but not ending) with slash('/')::

        >>> list(iter_jsonpointer_parts(None))
        Traceback (most recent call last):
        AttributeError: 'NoneType' object has no attribute 'split'

        >>> list(iter_jsonpointer_parts('a'))
        Traceback (most recent call last):
        ValueError: Jsonpointer-path(a) must start with '/'!

        #>>> list(iter_jsonpointer_parts('/a/'))
        #Traceback (most recent call last):
        #ValueError: Jsonpointer-path(a) must NOT ends with '/'!

    """

    #     if jsonpath.endswith('/'):
    #         msg = "Jsonpointer-path({}) must NOT finish with '/'!"
    #         raise ValueError(msg.format(jsonpath))
    parts = jsonpath.split("/")
    if parts.pop(0) != "":
        msg = "Jsonpointer-path({}) must start with '/'!"
        raise ValueError(msg.format(jsonpath))

    for part in parts:
        part = unescape_jsonpointer_part(part)

        yield part


def iter_jsonpointer_parts_relaxed(jsonpointer):
    """
    Like :func:`iter_jsonpointer_parts()` but accepting also non-absolute paths.

    The 1st step of absolute-paths is always ''.

    Examples::

        >>> list(iter_jsonpointer_parts_relaxed('a'))
        ['a']
        >>> list(iter_jsonpointer_parts_relaxed('a/'))
        ['a', '']
        >>> list(iter_jsonpointer_parts_relaxed('a/b'))
        ['a', 'b']

        >>> list(iter_jsonpointer_parts_relaxed('/a'))
        ['', 'a']
        >>> list(iter_jsonpointer_parts_relaxed('/a/'))
        ['', 'a', '']

        >>> list(iter_jsonpointer_parts_relaxed('/'))
        ['', '']

        >>> list(iter_jsonpointer_parts_relaxed(''))
        ['']

    """
    for part in jsonpointer.split("/"):
        yield unescape_jsonpointer_part(part)


_scream = ...


def resolve_jsonpointer(doc, jsonpointer, default=_scream):
    """
    Resolve a ``jsonpointer`` within the referenced ``doc``.

    :param doc:         the referrant document
    :param str path:    a jsonpointer to resolve within document
    :param default:     A value to return if path does not resolve; by default, it raises.
    :return:            the resolved doc-item or raises :class:`ValueError`
    :raises:            ValueError (if cannot resolve path and no `default`)

    Examples:

        >>> dt = {
        ...     'pi':3.14,
        ...     'foo':'bar',
        ...     'df': pd.DataFrame(np.ones((3,2)), columns=list('VN')),
        ...     'sub': {
        ...         'sr': pd.Series({'abc':'def'}),
        ...     }
        ... }
        >>> resolve_jsonpointer(dt, '/pi', default=_scream)
        3.14

        >>> resolve_jsonpointer(dt, '/pi/BAD')
        Traceback (most recent call last):
        ValueError: Unresolvable JSON pointer('/pi/BAD')@(BAD)

        >>> resolve_jsonpointer(dt, '/pi/BAD', 'Hi!')
        'Hi!'

    :author: Julian Berman, ankostis
    """
    for part in iter_jsonpointer_parts(jsonpointer):
        if isinstance(doc, cabc.Sequence):
            # Array indexes should be turned into integers
            try:
                part = int(part)
            except ValueError:
                pass
        try:
            doc = doc[part]
        except (TypeError, LookupError):
            if default is _scream:
                raise ValueError(
                    "Unresolvable JSON pointer(%r)@(%s)" % (jsonpointer, part)
                )
            else:
                return default

    return doc


def resolve_path(doc, path, default=_scream, root=None):
    """
    Like :func:`resolve_jsonpointer` also for relative-paths & attribute-branches.

    :param doc:      the referrant document
    :param str path: An absolute or relative path to resolve within document.
    :param default:  A value to return if path does not resolve.
    :param root:     Document for absolute paths, assumed `doc` if missing.
    :return:         the resolved doc-item or raises :class:`ValueError`
    :raises:     ValueError (if cannot resolve path and no `default`)

    Examples:

        >>> dt = {
        ...     'pi':3.14,
        ...     'foo':'bar',
        ...     'df': pd.DataFrame(np.ones((3,2)), columns=list('VN')),
        ...     'sub': {
        ...         'sr': pd.Series({'abc':'def'}),
        ...     }
        ... }
        >>> resolve_path(dt, '/pi', default=_scream)
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

    def resolve_root(d, p):
        if not p:
            return root or doc
        raise ValueError()

    part_resolvers = [
        lambda d, p: d[int(p)],
        lambda d, p: d[p],
        lambda d, p: getattr(d, p),
        resolve_root,
    ]
    for part in iter_jsonpointer_parts_relaxed(path):
        start_i = 0 if isinstance(doc, cabc.Sequence) else 1
        for resolver in part_resolvers[start_i:]:
            try:
                doc = resolver(doc, part)
                break
            except (ValueError, TypeError, LookupError, AttributeError):
                pass
        else:
            if default is _scream:
                raise ValueError("Unresolvable path(%r)@(%s)" % (path, part))
            return default

    return doc


def set_jsonpointer(doc, jsonpointer, value, object_factory=dict):
    """
    Resolve a ``jsonpointer`` within the referenced ``doc``.

    :param doc: the referrant document
    :param str jsonpointer: a jsonpointer to the node to modify
    :raises: ValueError (if jsonpointer empty, missing, invalid-contet)
    """
    parts = list(iter_jsonpointer_parts(jsonpointer))

    # Will scream if used on 1st iteration.
    #
    pdoc = None
    ppart = None
    for i, part in enumerate(parts):
        if isinstance(doc, cabc.Sequence) and not isinstance(doc, str):
            # Array indexes should be turned into integers
            #
            doclen = len(doc)
            if part == "-":
                part = doclen
            else:
                try:
                    part = int(part)
                except ValueError:
                    raise ValueError(
                        "Expected numeric index(%s) for sequence at (%r)[%i]"
                        % (part, jsonpointer, i)
                    )
                else:
                    if part > doclen:
                        raise ValueError(
                            "Index(%s) out of bounds(%i) of (%r)[%i]"
                            % (part, doclen, jsonpointer, i)
                        )
        try:
            ndoc = doc[part]
        except (LookupError):
            break  # Branch-extension needed.
        except (TypeError):  # Maybe indexing a string...
            ndoc = object_factory()
            pdoc[ppart] = ndoc
            doc = ndoc
            break  # Branch-extension needed.

        doc, pdoc, ppart = ndoc, doc, part
    else:
        doc = pdoc  # If loop exhausted, cancel last assignment.

    # Build branch with value-leaf.
    #
    nbranch = value
    for part2 in reversed(parts[i + 1 :]):
        ndoc = object_factory()
        ndoc[part2] = nbranch
        nbranch = ndoc

    # Attach new-branch.
    try:
        doc[part] = nbranch
    # Inserting last sequence-element raises IndexError("list assignment index
    # out of range")
    except IndexError:
        doc.append(nbranch)
