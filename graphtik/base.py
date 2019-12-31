# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Generic or specific utilities"""

import abc
import logging
import random
from collections import defaultdict
from typing import Any, Collection, List, Mapping, Union

Items = Union[Collection, str, None]

log = logging.getLogger(__name__)


class Token(str):
    __slots__ = ("hashid",)

    def __init__(self, *args):
        self.hashid = random.randint(-(2 ** 32), 2 ** 32 - 1)

    def __eq__(self, other):
        return self.hashid == getattr(other, "hashid", None)

    def __hash__(self):
        return self.hashid

    def __getstate__(self):
        return self.hashid

    def __setstate__(self, state):
        self.hashid = state

    def __copy__(self):
        return self

    def __deepcopy__(self, memo):
        return self

    def __bool__(self):
        return True

    def __str__(self):
        return "<%s>" % super().__str__()

    def __repr__(self):
        return self.__str__()


#: When an operation function returns this special value,
#: it implies operation has no result at all,
#: (otherwise, it would have been a single result, ``None``).`
NO_RESULT = Token("NO_RESULT")
UNSET = Token("UNSET")


def aslist(i, argname, allowed_types=list):
    """Utility to accept singular strings as lists, and None --> []."""
    if not i:
        return i if isinstance(i, allowed_types) else []

    if isinstance(i, str):
        i = [i]
    elif not isinstance(i, allowed_types):
        try:
            i = list(i)
        except Exception as ex:
            raise ValueError(f"Cannot list-ize {argname}({i!r}) due to: {ex}") from None

    return i


def astuple(i, argname, allowed_types=tuple):
    if not i:
        return i if isinstance(i, allowed_types) else ()

    if isinstance(i, str):
        i = (i,)
    elif not isinstance(i, allowed_types):
        try:
            i = tuple(i)
        except Exception as ex:
            raise ValueError(
                f"Cannot tuple-ize {argname}({i!r}) due to: {ex}"
            ) from None

    return i


class PushbackWrapper:
    """Adapted from https://stackoverflow.com/a/35664122/548792"""

    def __init__(self, iterator):
        self._iterator = iter(iterator)
        self._pushed = []

    def __iter__(self):
        return PushbackWrapper(self._iterator)

    def __next__(self):
        return self._pushed.pop() if self._pushed else next(self._iterator)

    def pushback(self, item):
        self._pushed.append(item)


def jetsam(ex, locs, *salvage_vars: str, annotation="jetsam", **salvage_mappings):
    """
    Annotate exception with salvaged values from locals() and raise!

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
        They take precendance over`salvae_vars`.

    :raises:
        any exception raised by the wrapped function, annotated with values
        assigned as atrributes on this context-manager

    - Any attrributes attached on this manager are attached as a new dict on
      the raised exception as new  ``jetsam`` attrribute with a dict as value.
    - If the exception is already annotated, any new items are inserted,
      but existing ones are preserved.

    **Example:**

    Call it with managed-block's ``locals()`` and tell which of them to salvage
    in case of errors::


        try:
            a = 1
            b = 2
            raise Exception()
        exception Exception as ex:
            jetsam(ex, locals(), "a", b="salvaged_b", c_var="c")
            raise

    And then from a REPL::

        import sys
        sys.last_value.jetsam
        {'a': 1, 'salvaged_b': 2, "c_var": None}

    ** Reason:**

    Graphs may become arbitrary deep.  Debugging such graphs is notoriously hard.

    The purpose is not to require a debugger-session to inspect the root-causes
    (without precluding one).

    Naively salvaging values with a simple try/except block around each function,
    blocks the debugger from landing on the real cause of the error - it would
    land on that block;  and that could be many nested levels above it.
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
        annotations = getattr(ex, annotation, None)
        if not isinstance(annotations, dict):
            annotations = {}
            setattr(ex, annotation, annotations)

        ## Salvage those asked
        for dst_key, src in salvage_mappings.items():
            try:
                salvaged_value = src(locs) if callable(src) else locs.get(src)
                annotations.setdefault(dst_key, salvaged_value)
            except Exception as ex:
                log.warning(
                    "Supressed error while salvaging jetsam item (%r, %r): %r"
                    % (dst_key, src, ex)
                )
    except Exception as ex2:
        log.warning("Supressed error while annotating exception: %r", ex2, exc_info=1)
        raise ex2


## Defined here, to avoid subclasses importig `plot` module.
class Plotter(abc.ABC):
    """
    Classes wishing to plot their graphs should inherit this and ...

    implement property ``plot`` to return a "partial" callable that somehow
    ends up calling  :func:`.plot.render_pydot()` with the `graph` or any other
    args binded appropriately.
    The purpose is to avoid copying this function & documentation here around.
    """

    def plot(
        self,
        filename=None,
        show=False,
        jupyter_render: Union[None, Mapping, str] = None,
        **kws,
    ):
        """
        Entry-point for plotting ready made operation graphs.

        :param str filename:
            Write diagram into a file.
            Common extensions are ``.png .dot .jpg .jpeg .pdf .svg``
            call :func:`plot.supported_plot_formats()` for more.
        :param show:
            If it evaluates to true, opens the  diagram in a  matplotlib window.
            If it equals `-1`, it plots but does not open the Window.
        :param inputs:
            an optional name list, any nodes in there are plotted
            as a "house"
        :param outputs:
            an optional name list, any nodes in there are plotted
            as an "inverted-house"
        :param solution:
            an optional dict with values to annotate nodes, drawn "filled"
            (currently content not shown, but node drawn as "filled").
            It extracts more infos from a :class:`.Solution` instance, such as,
            if `solution` has an ``executed`` attribute, operations contained in it
            are  drawn as "filled".
        :param title:
            an optional string to display at the bottom of the graph
        :param node_props:
            an optional nested dict of Grapvhiz attributes for certain nodes
        :param edge_props:
            an optional nested dict of Grapvhiz attributes for certain edges
        :param clusters:
            an optional mapping of nodes --> cluster-names, to group them
        :param jupyter_render:
            a nested dictionary controlling the rendering of graph-plots in Jupyter cells,
            if `None`, defaults to :data:`jupyter_render` (you may modify it in place
            and apply for all future calls).
        :param legend_url:
            a URL to the *graphtik* legend; if it evaluates to false, none is added.

        :return:
            a `pydot.Dot <https://pypi.org/project/pydot/>`_ instance
            (for for API reference visit:
            https://pydotplus.readthedocs.io/reference.html#pydotplus.graphviz.Dot)

            .. Tip::
                The :class:`pydot.Dot` instance returned is rendered directly
                in *Jupyter/IPython* notebooks as SVG images.

                You may increase the height of the SVG cell output with
                something like this::

                    netop.plot(jupyter_render={"svg_element_styles": "height: 600px; width: 100%"})

                Check :data:`.default_jupyter_render` for defaults.

        Note that the `graph` argument is absent - Each Plotter provides
        its own graph internally;  use directly :func:`.render_pydot()` to provide
        a different graph.

        .. image:: images/GraphtikLegend.svg
            :alt: Graphtik Legend

        *NODES:*

        oval
            function
        egg
            subgraph operation
        house
            given input
        inversed-house
            asked output
        polygon
            given both as input & asked as output (what?)
        square
            intermediate data, neither given nor asked.
        red frame
            evict-instruction, to free up memory.
        filled
            data node has a value in `solution` OR function has been executed.
        thick frame
            function/data node in execution `steps`.

        *ARROWS*

        solid black arrows
            dependencies (source-data *need*-ed by target-operations,
            sources-operations *provides* target-data)
        dashed black arrows
            optional needs
        blue arrows
            sideffect needs/provides
        wheat arrows
            broken dependency (``provide``) during pruning
        green-dotted arrows
            execution steps labeled in succession


        To generate the **legend**, see :func:`.legend()`.

        **Sample code:**

        >>> from graphtik import compose, operation
        >>> from graphtik.modifiers import optional
        >>> from operator import add

        >>> netop = compose("netop",
        ...     operation(name="add", needs=["a", "b1"], provides=["ab1"])(add),
        ...     operation(name="sub", needs=["a", optional("b2")], provides=["ab2"])(lambda a, b=1: a-b),
        ...     operation(name="abb", needs=["ab1", "ab2"], provides=["asked"])(add),
        ... )

        >>> netop.plot(show=True);                 # plot just the graph in a matplotlib window # doctest: +SKIP
        >>> inputs = {'a': 1, 'b1': 2}
        >>> solution = netop(**inputs)             # now plots will include the execution-plan

        >>> netop.plot('plot1.svg', inputs=inputs, outputs=['asked', 'b1'], solution=solution);           # doctest: +SKIP
        >>> dot = netop.plot(solution=solution);   # just get the `pydoit.Dot` object, renderable in Jupyter
        >>> print(dot)
        digraph G {
            URL="https://graphtik.readthedocs.io/en/latest/_images/GraphtikLegend.svg";
            fontname=italic;
            label=netop;
            a [fillcolor=wheat, shape=invhouse, style=filled, tooltip=1];
        ...

        """
        from .plot import render_pydot

        dot = self._build_pydot(**kws)
        return render_pydot(
            dot, filename=filename, show=show, jupyter_render=jupyter_render
        )

    @abc.abstractmethod
    def _build_pydot(self, **kws):
        pass
