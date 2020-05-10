# Copyright 2020, Kostis Anagnostopoulos.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""A builder that Render graphtik plots from doctest-runner's globals."""
from collections import OrderedDict
from hashlib import sha1
from pathlib import Path
from typing import Union

import pydot
from boltons.iterutils import first
from docutils import nodes
from sphinx.application import Sphinx
from sphinx.ext import doctest as extdoctest
from sphinx.locale import _, __
from sphinx.util import logging

from ..base import Plottable
from ..config import is_debug
from . import doctestglobs, dynaimage, graphtik_node

PlottableType = Union[None, Plottable, pydot.Dot]

log = logging.getLogger(__name__)

_image_mimetypes = {
    "svg": "image/svg+xml",
    "svgz": "image/svg+xml",
    "pdf": "image/x+pdf",
}


class HistoricDict(OrderedDict):
    def __setitem__(self, k, v):
        super().__setitem__(k, v)
        self.move_to_end(k)


class GraphtikPlotsBuilder(doctestglobs.ExposeGlobalsDocTestBuilder):
    """Retrieve a :term:`plottable` from doctests globals and render them. """

    run_empty_code = True

    def _make_group_globals(self, group: extdoctest.TestGroup):
        return HistoricDict()

    def _warn_out(self, text: str) -> None:
        """
        Silence warnings since errors unrelated to building site, ...

        unless :rst:confval`graphtik_warning_is_error` is true (default false).
        """
        if self.config.graphtik_warning_is_error:
            super()._warn_out(text)
        else:
            log.info(f"WARN-like: {text}", nonl=True)
        self.outfile.write(text)

    def _globals_updated(self, code: extdoctest.TestCode, globs: dict):
        """Collect plottable from doctest-runner globals and render graphtik plot. """
        node: nodes.Node = code.node.parent

        if isinstance(node, graphtik_node):
            plottable = self._retrieve_graphvar_plottable(
                globs, node["graphvar"], (code.filename, code.lineno)
            )
            if plottable:
                dot: pydot.Dot
                if isinstance(plottable, pydot.Dot):
                    dot = plottable
                else:
                    dot = plottable.plot(**self.config.graphtik_plot_keywords)
                img_format = node["img_format"]
                rel_img_path = self._render_dot_image(img_format, dot, node)
                dot_str = (
                    f"{plottable.debugstr()}"
                    if hasattr(plottable, "debugstr")  # a Solution
                    else plottable
                )
                self._upd_image_node(
                    node, rel_img_path, dot_str=str(dot_str), cmap_id=dot.get_name()
                )

    def _is_plottable(self, value):
        return isinstance(value, (Plottable, pydot.Dot))

    def _retrieve_graphvar_plottable(
        self, globs: dict, graphvar, location,
    ) -> PlottableType:
        plottable: PlottableType = None

        if graphvar is None:
            ## Pick last plottable from globals.
            #
            for i, var in enumerate(reversed(list(globs))):
                value = globs[var]
                if self._is_plottable(value):
                    log.debug(
                        __(
                            "picked plottable %r from doctest globals (num %s from the end0)"
                        ),
                        var,
                        i,
                        location=location,
                    )
                    plottable = value
                    break
            else:
                log.error(
                    __("could not find any plottable in doctest globals"),
                    location=location,
                )

        else:
            ## Pick named var from globals.
            #
            try:
                value = globs[graphvar]
                if not self._is_plottable(value):
                    log.warning(
                        __(
                            "value of graphvar %r in doctest globals is not plottable but %r"
                        ),
                        graphvar,
                        type(value).__name__,
                        location=location,
                    )
                else:
                    plottable = value
            except KeyError:
                log.warning(
                    __("could not find graphvar %r in doctest globals"),
                    graphvar,
                    location=location,
                )

        return plottable

    def _fill_graph_url(self, dot: pydot.Dot, fname: str) -> None:
        """Add a link on the graph to open in new tab, if Dot has no URL."""
        if "URL" not in dot.get_attributes():
            dot.set_URL(fname)
            if "target" not in dot.get_attributes():
                dot.set_target("_blank")

    def _render_dot_image(
        self, img_format, dot: pydot.Dot, node: graphtik_node
    ) -> Path:
        """
        Ensure png(+usemap)|svg|svgz|pdf file exist, and return its path.

        :raises:
            any exception from Graphviz program
        """
        fname = f"{node['filename']}.{img_format}"
        ## So that when clicked, a new tab opens
        self._fill_graph_url(dot, fname)

        abs_fpath = Path(self.outdir, self.imagedir, fname)
        log.info(__("Rendering '%s'..."), abs_fpath)

        save_dot_files = self.config.graphtik_save_dot_files
        if save_dot_files is None:
            save_dot_files = is_debug()

        abs_fpath.parent.mkdir(parents=True, exist_ok=True)

        ## Save dot-file before rendering,
        #  to still have it in case of errors.
        #
        if save_dot_files:
            self.env.graphtik_image_purgatory.register_doc_fpath(
                self.env.docname, abs_fpath.with_suffix(".txt")
            )
            with open(abs_fpath.with_suffix(".txt"), "w") as f:
                f.write(str(dot))

        self.env.graphtik_image_purgatory.register_doc_fpath(
            self.env.docname, abs_fpath
        )
        dot.write(abs_fpath, format=img_format)
        if img_format == "png":
            cmap = dot.create(format="cmapx", encoding="utf-8").decode("utf-8")
            node.cmap = cmap

        ## XXX: used to work till active-builder attributes were transferred to self.
        # rel_fpath = Path(self.imgpath, fname)
        rel_fpath = Path(self.imagedir, fname)

        return rel_fpath

    def _upd_image_node(
        self, node: graphtik_node, rel_img_path: Path, dot_str: str, cmap_id: str
    ):
        img_format: str = node["img_format"]
        assert img_format, (img_format, node)

        image_node: dynaimage = first(node.traverse(dynaimage))
        if img_format == "png":
            image_node.tag = "img"
            image_node["src"] = str(rel_img_path)
            image_node["usemap"] = f"#{cmap_id}"
            # HACK: graphtik-node not given to html-visitor.
            image_node.cmap = getattr(node, "cmap", "")
        else:
            image_node.tag = "object"
            # TODO: make sphinx-SVGs zoomable.
            image_node["data"] = str(rel_img_path)
            image_node["type"] = _image_mimetypes[img_format]

        if "alt" not in image_node:
            image_node["alt"] = dot_str


def get_graphtik_builder(app: Sphinx) -> GraphtikPlotsBuilder:
    """Initialize a singleton patched doctest-builder"""
    builder = getattr(app, "graphtik_builder", None)
    if builder is None:
        builder = GraphtikPlotsBuilder(app)
        builder.set_environment(app.env)
        builder.imgpath = app.builder.imgpath
        builder.imagedir = app.builder.imagedir
        builder.init()
        app.graphtik_builder = builder

    return builder
