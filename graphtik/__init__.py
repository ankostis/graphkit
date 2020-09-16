# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Lightweight :term:`computation` graphs for Python."""

__version__ = "10.2.0"
__release_date__ = "16 Aug 2020, 12:15"
__title__ = "graphtik"
__summary__ = __doc__.splitlines()[0]
__license__ = "Apache-2.0"
__uri__ = "https://github.com/pygraphkit/graphtik"
__author__ = "hnguyen, ankostis"  # chronologically ordered


from .base import AbortedException, IncompleteExecutionError
from .modifier import (
    accessor,
    modify,
    keyword,
    optional,
    sfx,
    sfxed,
    sfxed_vararg,
    sfxed_varargs,
    vararg,
    varargs,
)
from .fnop import NO_RESULT, NO_RESULT_BUT_SFX, operation
from .pipeline import compose

## SEE ALSO: `.plot.active_plotter_plugged()`, `.plot.set_active_plotter()` &
#  `.plot.get_active_plotter()` configs, not imported, unless plot is needed..
