# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight :term:`computation` graphs for Python."""

__version__ = "8.1.0.dev3"
__release_date__ = "10 May 2020, 21:06"
__title__ = "graphtik"
__summary__ = __doc__.splitlines()[0]
__license__ = "Apache-2.0"
__uri__ = "https://github.com/pygraphkit/graphtik"
__author__ = "hnguyen, ankostis"


from .base import AbortedException, IncompleteExecutionError
from .composition import NO_RESULT, NO_RESULT_BUT_SFX, NULL_OP, compose, operation
from .modifiers import (
    mapped,
    optional,
    sfx,
    sfxed,
    sfxed_vararg,
    sfxed_varargs,
    vararg,
    varargs,
)

## SEE ALSO: `.plot.active_plotter_plugged()`, `.plot.set_active_plotter()` &
#  `.plot.get_active_plotter()` configs, not imported, unless plot is needed..
