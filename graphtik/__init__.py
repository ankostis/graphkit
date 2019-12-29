# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight :term:`computation` graphs for Python."""

__version__ = "4.4.1"
__release_date__ = "22 Dec 2019, 21:49"
__license__ = "Apache-2.0"
__title__ = "graphtik"
__author__ = "hnguyen, ankostis"
__summary__ = __doc__.splitlines()[0]
__uri__ = "https://github.com/pygraphkit/graphtik"


from .base import NO_RESULT
from .config import (
    abort_run,
    operations_endured,
    execution_pool,
    get_execution_pool,
    is_abort,
    is_endure_operations,
    is_marshal_tasks,
    is_parallel_tasks,
    is_reschedule_operations,
    is_skip_evictions,
    tasks_marshalled,
    tasks_in_parallel,
    operations_reschedullled,
    set_endure_operations,
    set_execution_pool,
    set_marshal_tasks,
    set_parallel_tasks,
    set_reschedule_operations,
    set_skip_evictions,
    evictions_skipped,
)
from .modifiers import *  # noqa, on purpose to include any new modifiers
from .netop import compose
from .network import AbortedException
from .op import operation
