# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight :term:`computation` graphs for Python."""

__version__ = "7.1.2"
__release_date__ = "6 May 2020, 19:59"
__title__ = "graphtik"
__summary__ = __doc__.splitlines()[0]
__license__ = "Apache-2.0"
__uri__ = "https://github.com/pygraphkit/graphtik"
__author__ = "hnguyen, ankostis"


from .base import NO_RESULT
from .config import (
    abort_run,
    debug_enabled,
    evictions_skipped,
    execution_pool_plugged,
    get_execution_pool,
    is_abort,
    is_debug,
    is_endure_operations,
    is_marshal_tasks,
    is_parallel_tasks,
    is_reschedule_operations,
    is_skip_evictions,
    operations_endured,
    operations_reschedullled,
    reset_abort,
    set_debug,
    set_endure_operations,
    set_execution_pool,
    set_marshal_tasks,
    set_parallel_tasks,
    set_reschedule_operations,
    set_skip_evictions,
    tasks_in_parallel,
    tasks_marshalled,
)

## SEE ALSO: `.plot.active_plotter_plugged()`, `.plot.set_active_plotter()` &
#  `.plot.get_active_plotter()` configs, not imported, unless plot is needed..

from .modifiers import mapped, optional, vararg, varargs, sideffect, sideffected
from .netop import compose
from .network import AbortedException, IncompleteExecutionError
from .op import operation
