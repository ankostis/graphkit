# Copyright 2019-2020, Kostis Anagnostopoulos;
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Deferred logs, and :term:`jetsam` utility for annotating exceptions from ``locals()``.

.. doctest::
    :hide:

    .. Workaround sphinx-doc/sphinx#6590

    >>> from graphtik.jetsam import *
    >>> __name__ = "graphtik.jetsam"
"""
import logging
import logging.handlers
import sys
from contextlib import contextmanager
from pathlib import Path
from typing import Union

log = logging.getLogger(__name__)


class Jetsam(dict):
    """
    The :term:`jetsam` is a dict with items accessed also as attributes.

    From https://stackoverflow.com/a/14620633/548792
    """

    def __init__(self, *args, **kwargs):
        super(Jetsam, self).__init__(*args, **kwargs)
        self.__dict__ = self

    def log_n_plot(self, plot=None) -> Path:
        """
        Log collected items, and plot 1st :term:`plottable` in a temp-file, if :ref:`debug`.

        :param plot:
            override DEBUG-flag if given (true, plots, false not)

        :return:
            the name of temp-file, also ERROR-logged along with the rest jetsam
        """
        from tempfile import gettempdir
        from textwrap import indent
        from .config import is_debug
        from . import __title__
        from .plot import save_plot_file_by_sha1

        debug = is_debug() if plot is None else plot

        ## Plot broken
        #
        plot_fpath = None
        if debug and "plot_fpath" not in self:
            for p_type in "solution plan pipeline network".split():
                plottable = self.get(p_type)
                if plottable is not None:
                    try:
                        dir_prefix = Path(gettempdir(), __title__)
                        plot_fpath = save_plot_file_by_sha1(plottable, dir_prefix)
                        self["plot_fpath"] = plot_fpath
                        break
                    except Exception as ex:
                        log.warning(
                            "Suppressed error while plotting jetsam %s: %s(%s)",
                            plottable,
                            type(ex).__name__,
                            ex,
                            exc_info=True,
                        )

        ## Log collected jetsam
        #
        #  NOTE: log jetsam only HERE (pipeline), to avoid repetitive printouts.
        #
        if self:
            items = "".join(
                f"  +--{f'{k}({v.solid})' if hasattr(v, 'solid') else k}:"
                f"\n{indent(str(v), ' ' * 4)}\n"
                for k, v in self.items()
                if v is not None
            )
            logging.getLogger(f"{__name__}.err").error("Salvaged jetsam:\n%s", items)

        return plot_fpath


def save_jetsam(ex, locs, *salvage_vars: str, annotation="jetsam", **salvage_mappings):
    """
    Annotate exception with salvaged values from locals(), log, (if :ref:`debug`) plot.

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

    :return:
        the :class:`Jetsam` annotation, also attached on the exception
    :raises:
        any exception raised by the wrapped function, annotated with values
        assigned as attributes on this context-manager

    - Any attributes attached on this manager are attached as a new dict on
      the raised exception as new  ``jetsam`` attribute with a dict as value.
    - If the exception is already annotated, any new items are inserted,
      but existing ones are preserved.
    - If :ref:`debug` is enabled, plots the 1st found errored in order
      solution/plan/pipeline/net, and log its path.

    **Example:**

    Call it with managed-block's ``locals()`` and tell which of them to salvage
    in case of errors::


        >>> try:
        ...     a = 1
        ...     b = 2
        ...     raise Exception("trouble!")
        ... except Exception as ex:
        ...     save_jetsam(ex, locals(), "a", b="salvaged_b", c_var="c")
        ...     raise
        Traceback (most recent call last):
        Exception: trouble!

    And then from a REPL::

        >>> import sys
        >>> sys.exc_info()[1].jetsam                # doctest: +SKIP
        {'a': 1, 'salvaged_b': 2, "c_var": None}

    .. Note::

        In order not to obfuscate the landing position of post-mortem debuggers
        in the case of errors, use the ``try-finally`` with ``ok`` flag pattern:

        >>> ok = False
        >>> try:
        ...
        ...     pass        # do risky stuff
        ...
        ...     ok = True   # last statement in the try-body.
        ... except Exception as ex:
        ...     if not ok:
        ...         ex = sys.exc_info()[1]
        ...         save_jetsam(...)

    ** Reason:**

    Graphs may become arbitrary deep.  Debugging such graphs is notoriously hard.

    The purpose is not to require a debugger-session to inspect the root-causes
    (without precluding one).
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
        jetsam = getattr(ex, annotation, None)
        if not isinstance(jetsam, Jetsam):
            jetsam = Jetsam()
            setattr(ex, annotation, jetsam)

        ## Salvage those asked
        for dst_key, src in salvage_mappings.items():
            try:
                if dst_key not in jetsam:
                    salvaged_value = src(locs) if callable(src) else locs.get(src)
                    jetsam[dst_key] = salvaged_value
            except Exception as ex:
                log.warning(
                    "Suppressed error while salvaging jetsam item (%r, %r): %s(%s)",
                    dst_key,
                    src,
                    type(ex).__name__,
                    ex,
                    exc_info=True,
                )

        return jetsam
    except Exception as ex2:
        log.warning(
            "Suppressed error while annotating exception: %r", ex2, exc_info=True
        )


# Copyright: Copyright 2007-2020 by the Sphinx team.
# Copyright 2020, Kostis Anagnostopoulos;
# License: BSD
#
# Logging utilities to suppress logs when no errors occurred.
#
# (adapted from :mod:`sphinx.logging`)


class MemoryHandler(logging.handlers.MemoryHandler):
    """A memory-handler ignoring its capacity. """

    def shouldFlush(self, record: logging.LogRecord):
        """Check only if record at the flushLevel or higher (not capacity). """
        return record.levelno >= self.flushLevel

    def discard(self):
        self.buffer.clear()


@contextmanager
def deferred_logging(
    logger: logging.Logger, flush_level: Union[int, str] = logging.ERROR
):
    """
    Contextmanager to suppress logs unless failures or logs above some level are issued.

    :param logger:
        what logger to intercept;  if unspecified, intercepts root-logger.
    :param flush_level:
        which logging-level and above (including) triggers flushing

    For example::

        >>> with deferred_logging(logging.getLogger()):
        ...     pass # unstable_process()


    ... would generate logs only if the `unstable_process` raises an exception,
    or it issues a log at ERROR (by default) level.
    """
    memhandler = MemoryHandler(-1, flush_level, logger, flushOnClose=False)
    ok = False

    try:
        handlers = []
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handlers.append(handler)

        logger.addHandler(memhandler)
        orig_propagate, logger.propagate = logger.propagate, False
        yield memhandler
        ok = True
    finally:
        logger.propagate = orig_propagate
        logger.removeHandler(memhandler)

        for handler in handlers:
            logger.addHandler(handler)

        if not ok:
            memhandler.flush()
        else:
            memhandler.discard()
