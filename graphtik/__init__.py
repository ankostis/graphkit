# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight computation graphs for Python."""

__author__ = "hnguyen"
__version__ = "2.0.0b0"
__license__ = "Apache-2.0"
__title__ = "graphtik"
__summary__ = __doc__.splitlines()[0]
__uri__ = "https://github.com/pygraphkit/graphtik"

from .functional import operation, compose
from .modifiers import *  # noqa, on purpose to include any new modifiers

# For backwards compatibility
from .base import Operation
from .network import Network
