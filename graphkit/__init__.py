# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight computation graphs for Python."""

__author__ = "hnguyen"
__version__ = "1.3.0"
__license__ = "Apache-2.0"
__title__ = "graphkit"
__summary__ = __doc__.splitlines()[0]
__uri__ = "https://github.com/yahoo/graphkit"

#: Set this to false if you want the debugger to land on the real-cause
#: of an exception.  But then you loose exception-annotations
#: (see :ref:`debugging`, :func:`base.exception_annotated`).
#:
#: - true: annotate
#: - false: pass-through
#: - None: (default) annotate only if NO debugger attached
annotate_exceptions = None

from .functional import operation, compose
from .modifiers import *  # noqa, on purpose to include any new modifiers

# For backwards compatibility
from .base import Operation
from .network import Network
