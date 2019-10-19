# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight computation graphs for Python."""

__author__ = "hnguyen"
__version__ = "2.1.1.dev0"
__release_date__ = "20 Oct 2019, 01:30"
__license__ = "Apache-2.0"
__title__ = "graphtik"
__summary__ = __doc__.splitlines()[0]
__uri__ = "https://github.com/pygraphkit/graphtik"

from .nodes import operation, compose
from .modifiers import *  # noqa, on purpose to include any new modifiers
