# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
""" Plotting of graphtik graphs."""
import html
import inspect
import io
import json
import logging
import os
import re
from typing import Any, Callable, List, Mapping, Tuple, Union
from urllib.parse import urlencode

import networkx
import pydot

log = logging.getLogger(__name__)

#: A nested dictionary controlling the rendering of graph-plots in Jupyter cells,
#:
#: as those returned from :meth:`.Plottable.plot()` (currently as SVGs).
#: Either modify it in place, or pass another one in the respective methods.
#:
#: The following keys are supported.
#:
#: :param svg_pan_zoom_json:
#:     arguments controlling the rendering of a zoomable SVG in
#:     Jupyter notebooks, as defined in https://github.com/ariutta/svg-pan-zoom#how-to-use
#:     if `None`, defaults to string (also maps supported)::
#:
#:             "{controlIconsEnabled: true, zoomScaleSensitivity: 0.4, fit: true}"
#:
#: :param svg_element_styles:
#:     mostly for sizing the zoomable SVG in Jupyter notebooks.
#:     Inspect & experiment on the html page of the notebook with browser tools.
#:     if `None`, defaults to string (also maps supported)::
#:
#:         "width: 100%; height: 300px;"
#:
#: :param svg_container_styles:
#:     like `svg_element_styles`, if `None`, defaults to empty string (also maps supported).
default_jupyter_render = {
    "svg_pan_zoom_json": "{controlIconsEnabled: true, fit: true}",
    "svg_element_styles": "width: 100%; height: 300px;",
    "svg_container_styles": "",
}


def _parse_jupyter_render(dot) -> Tuple[str, str, str]:
    jupy_cfg: Mapping[str, Any] = getattr(dot, "_jupyter_render", None)
    if jupy_cfg is None:
        jupy_cfg = default_jupyter_render

    def parse_value(key: str, parser: Callable) -> str:
        if key not in jupy_cfg:
            return parser(default_jupyter_render.get(key, ""))

        val: Union[Mapping, str] = jupy_cfg.get(key)
        if not val:
            val = ""
        elif not isinstance(val, str):
            val = parser(val)
        return val

    def styles_parser(d: Mapping) -> str:
        return "".join(f"{key}: {val};\n" for key, val in d)

    svg_container_styles = parse_value("svg_container_styles", styles_parser)
    svg_element_styles = parse_value("svg_element_styles", styles_parser)
    svg_pan_zoom_json = parse_value("svg_pan_zoom_json", json.dumps)

    return svg_pan_zoom_json, svg_element_styles, svg_container_styles


def _dot2svg(dot):
    """
    Monkey-patching for ``pydot.Dot._repr_html_()` for rendering in jupyter cells.

    Original ``_repr_svg_()`` trick was suggested in https://github.com/pydot/pydot/issues/220.

    .. Note::
        Had to use ``_repr_html_()`` and not simply ``_repr_svg_()`` because
        (due to https://github.com/jupyterlab/jupyterlab/issues/7497)


    .. TODO:
        Render in jupyter cells fully on client-side without SVG, using lib:
        https://visjs.github.io/vis-network/docs/network/#importDot
        Or with plotly https://plot.ly/~empet/14007.embed

    """
    pan_zoom_json, element_styles, container_styles = _parse_jupyter_render(dot)
    svg_txt = dot.create_svg().decode()
    html_txt = f"""
        <div class="svg_container">
            <style>
                .svg_container {{
                    {container_styles}
                }}
                .svg_container SVG {{
                    {element_styles}
                }}
            </style>
            <script src="https://ariutta.github.io/svg-pan-zoom/dist/svg-pan-zoom.min.js"></script>
            <script type="text/javascript">
                var scriptTag = document.scripts[document.scripts.length - 1];
                var parentTag = scriptTag.parentNode;
                var svg_el = parentTag.querySelector(".svg_container svg");
                svgPanZoom(svg_el, {pan_zoom_json});
            </script>
            {svg_txt}
        </</>
    """
    return html_txt


def _monkey_patch_for_jupyter(pydot):
    """Ensure Dot instance render in Jupyter notebooks. """
    if not hasattr(pydot.Dot, "_repr_html_"):
        pydot.Dot._repr_html_ = _dot2svg


def _is_class_value_in_list(lst, cls, value):
    return any(isinstance(i, cls) and i == value for i in lst)


def _merge_conditions(*conds):
    """combines conditions as a choice in binary range, eg, 2 conds --> [0, 3]"""
    return sum(int(bool(c)) << i for i, c in enumerate(conds))


def _apply_user_props(dotobj, user_props, key):
    if user_props and key in user_props:
        dotobj.get_attributes().update(user_props[key])
        # Delete it, to report unmatched ones, AND not to annotate `steps`.
        del user_props[key]


def _report_unmatched_user_props(user_props, kind):
    if user_props and log.isEnabledFor(logging.WARNING):
        unmatched = "\n  ".join(str(i) for i in user_props.items())
        log.warning("Unmatched `%s_props`:\n  +--%s", kind, unmatched)


def quote_dot_word(word: Any):
    """
    Workaround *pydot* parsing of node-id & labels by encoding as HTML.

    - `pydot` library does not quote DOT-keywords anywhere (pydot#111).
    - Char ``:`` denote port/compass-points and break IDs (pydot#224).
    - Non-strings are not quoted_if_necessary by pydot.

    .. Attention::
        It does not correctly handle ``ID:port:compass-point`` format.

    See https://www.graphviz.org/doc/info/lang.html)
    """
    if word is None:
        return word
    word = str(word)
    if not word:
        return word

    if word[0] != "<" or word[-1] != ">":
        word = f"<{html.escape(word)}>"
    word = word.replace(":", "&#58;")

    return word


def as_identifier(s):
    """
    Convert string into a valid ID, both for html & graphviz.

    It must not rely on Graphviz's HTML-like string,
    because it would not be a valid HTML-ID.

    - Adapted from https://stackoverflow.com/a/3303361/548792,
    - HTML rule from https://stackoverflow.com/a/79022/548792
    - Graphviz rules: https://www.graphviz.org/doc/info/lang.html
    """
    s = s.strip()
    # Remove invalid characters
    s = re.sub("[^0-9a-zA-Z_]", "_", s)
    # Remove leading characters until we find a letter
    # (HTML-IDs cannot start with underscore)
    s = re.sub("^[^a-zA-Z]+", "", s)
    if s in pydot.dot_keywords:
        s = f"{s}_"

    return s


def build_pydot(
    graph: networkx.Graph,
    name=None,
    steps=None,
    inputs=None,
    outputs=None,
    solution=None,
    title=None,
    node_props=None,
    edge_props=None,
    clusters=None,
    splines="ortho",
    legend_url="https://graphtik.readthedocs.io/en/latest/_images/GraphtikLegend.svg",
) -> pydot.Dot:
    """
    Build a |pydot.Dot|_ out of a Network graph/steps/inputs/outputs and return it

    to be fed into `Graphviz`_ to render.

    See :meth:`.Plottable.plot()` for the arguments, sample code, and
    the legend of the plots.
    """
    from .op import Operation
    from .modifiers import optional
    from .network import _EvictInstruction

    _monkey_patch_for_jupyter(pydot)

    assert graph is not None

    resched_thickness = 4
    fill_color = "wheat"
    failed_color = "LightCoral"
    cancel_color = "Grey"
    broken_color = "Red"
    overwrite_color = "SkyBlue"
    steps_color = "#009999"
    new_clusters = {}

    def append_or_cluster_node(dot, nx_node, node):
        if not clusters or not nx_node in clusters:
            dot.add_node(node)
        else:
            cluster_name = clusters[nx_node]
            node_cluster = new_clusters.get(cluster_name)
            if not node_cluster:
                node_cluster = new_clusters[cluster_name] = pydot.Cluster(
                    cluster_name, label=cluster_name
                )
            node_cluster.add_node(node)

    def append_any_clusters(dot):
        for cluster in new_clusters.values():
            dot.add_subgraph(cluster)

    def get_node_name(a):
        if isinstance(a, Operation):
            a = a.name
        return quote_dot_word(a)

    dot = pydot.Dot(
        graph_type="digraph",
        label=quote_dot_word(title),
        fontname="italic",
        splines=splines,
    )
    if name:
        dot.set_name(as_identifier(name))

    # draw nodes
    for nx_node in graph.nodes:
        if isinstance(nx_node, str):
            kw = {}

            # FrameColor change by step type
            if steps and nx_node in steps:
                kw = {"color": "#990000"}

            # SHAPE change if with inputs/outputs.
            # tip: https://graphviz.gitlab.io/_pages/doc/info/shapes.html
            choice = _merge_conditions(
                inputs and nx_node in inputs, outputs and nx_node in outputs
            )
            shape = "rect invhouse house hexagon".split()[choice]

            # LABEL change with solution.
            if solution and nx_node in solution:
                kw["style"] = "filled"
                kw["fillcolor"] = (
                    overwrite_color
                    if nx_node in getattr(solution, "overwrites", ())
                    else fill_color
                )
                ## NOTE: SVG tooltips not working without URL:
                #  https://gitlab.com/graphviz/graphviz/issues/1425
                kw["tooltip"] = html.escape(str(solution.get(nx_node)))
            node = pydot.Node(name=quote_dot_word(nx_node), shape=shape, **kw,)
        else:  # Operation
            kw = {"fontname": "italic"}

            if nx_node.rescheduled:
                kw["penwidth"] = resched_thickness
            if hasattr(solution, "is_failed") and solution.is_failed(nx_node):
                kw["style"] = "filled"
                kw["fillcolor"] = failed_color
            elif nx_node in getattr(solution, "executed", ()):
                kw["style"] = "filled"
                kw["fillcolor"] = fill_color
            elif nx_node in getattr(solution, "canceled", ()):
                kw["style"] = "filled"
                kw["fillcolor"] = cancel_color

            try:
                filename = urlencode(inspect.getfile(nx_node.fn))
                kw["URL"] = f"file://{filename}"
            except Exception as ex:
                log.debug("Ignoring error while inspecting file of %s: %s", nx_node, ex)
            try:
                fn_tooltip = inspect.getsource(nx_node.fn)
            except Exception as ex:
                log.debug(
                    "Ignoring error while inspecting source of %s: %s", nx_node, ex
                )
                fn_tooltip = str(nx_node)
            kw["tooltip"] = html.escape(fn_tooltip)

            node = pydot.Node(
                name=quote_dot_word(nx_node.name),
                shape="oval",
                ## NOTE: Jupyter lab is blocking local-urls (e.g. on SVGs).
                **kw,
            )

        _apply_user_props(node, node_props, key=node.get_name())

        append_or_cluster_node(dot, nx_node, node)

    _report_unmatched_user_props(node_props, "node")

    append_any_clusters(dot)

    # draw edges
    for src, dst, data in graph.edges(data=True):
        src_name = get_node_name(src)
        dst_name = get_node_name(dst)

        kw = {}
        if data.get("optional"):
            kw["style"] = "dashed"
        if data.get("sideffect"):
            kw["color"] = "blue"

        if getattr(src, "rescheduled", None) or getattr(src, "endured", None):
            kw["style"] = "dashed"
            if solution and dst not in solution and dst not in steps:
                kw["color"] = broken_color

        edge = pydot.Edge(src=src_name, dst=dst_name, **kw)

        _apply_user_props(edge, edge_props, key=(src, dst))

        dot.add_edge(edge)

    _report_unmatched_user_props(edge_props, "edge")

    # draw steps sequence
    if steps and len(steps) > 1:
        it1 = iter(steps)
        it2 = iter(steps)
        next(it2)
        for i, (src, dst) in enumerate(zip(it1, it2), 1):
            src_name = get_node_name(src)
            dst_name = get_node_name(dst)
            edge = pydot.Edge(
                src=src_name,
                dst=dst_name,
                label=str(i),
                style="dotted",
                color=steps_color,
                fontcolor=steps_color,
                fontname="bold",
                fontsize=18,
                arrowhead="vee",
                splines=True,
            )
            dot.add_edge(edge)

    if legend_url:
        dot.add_node(
            pydot.Node(
                name="legend",
                shape="component",
                style="filled",
                fill_color="yellow",
                URL=legend_url,
            )
        )

    return dot


def supported_plot_formats() -> List[str]:
    """return automatically all `pydot` extensions"""
    return [".%s" % f for f in pydot.Dot().formats]


def render_pydot(dot: pydot.Dot, filename=None, show=False, jupyter_render: str = None):
    """
    Render a |pydot.Dot|_ instance with `Graphviz`_ in a file and/or in a matplotlib window.

    :param dot:
        the pre-built |pydot.Dot|_ instance
    :param str filename:
        Write diagram into a file.
        Common extensions are ``.png .dot .jpg .jpeg .pdf .svg``
        call :func:`.supported_plot_formats()` for more.
    :param show:
        If it evaluates to true, opens the  diagram in a  matplotlib window.
        If it equals `-1`, it returns the image but does not open the Window.
    :param jupyter_render:
        a nested dictionary controlling the rendering of graph-plots in Jupyter cells.
        If `None`, defaults to :data:`default_jupyter_render`
        (you may modify those in place and they will apply for all future calls).

        You may increase the height of the SVG cell output with
        something like this::

            netop.plot(jupyter_render={"svg_element_styles": "height: 600px; width: 100%"})

    :return:
        the matplotlib image if ``show=-1``, or the given `dot` annotated with any
        jupyter-rendering configurations given in `jupyter_render` parameter.

    See :meth:`.Plottable.plot()` for sample code.
    """
    # Save plot
    #
    if filename:
        formats = supported_plot_formats()
        _basename, ext = os.path.splitext(filename)
        if not ext.lower() in formats:
            raise ValueError(
                "Unknown file format for saving graph: %s"
                "  File extensions must be one of: %s" % (ext, " ".join(formats))
            )

        dot.write(filename, format=ext.lower()[1:])

    ## Display graph via matplotlib
    #
    if show:
        import matplotlib.pyplot as plt
        import matplotlib.image as mpimg

        png = dot.create_png()
        sio = io.BytesIO(png)
        img = mpimg.imread(sio)
        if show != -1:
            plt.imshow(img, aspect="equal")
            plt.show()

        return img

    ## Propagate any properties for rendering in Jupyter cells.
    dot._jupyter_render = jupyter_render

    return dot


def legend(
    filename=None,
    show=None,
    jupyter_render: Mapping = None,
    arch_url="https://graphtik.readthedocs.io/en/latest/arch.html",
):
    """
    Generate a legend for all plots (see :meth:`.Plottable.plot()` for args)

    :param arch_url:
        the url to the architecture section explaining *graphtik* glossary.

    See :func:`.render_pydot` for the rest arguments.
    """

    _monkey_patch_for_jupyter(pydot)

    ## From https://stackoverflow.com/questions/3499056/making-a-legend-key-in-graphviz
    # Render it manually with these python commands, and remember to update result in git:
    #
    #   from graphtik.plot import legend
    #   legend('docs/source/images/GraphtikLegend.svg')
    dot_text = """
    digraph {
        rankdir=LR;
        subgraph cluster_legend {
        label="Graphtik Legend";

        operation   [shape=oval fontname=italic
                     tooltip="A function with needs & provides."
                     URL="%(arch_url)s#term-operation"];
        insteps     [label="execution step" fontname=italic
                     tooltip="Either an operation or ean eviction-instruction."
                     URL="%(arch_url)s#term-execution-steps"];
        executed    [shape=oval style=filled fillcolor=wheat fontname=italic
                     tooltip="Operation executed successfully."
                     URL="%(arch_url)s#term-solution"];
        failed      [shape=oval style=filled fillcolor=LightCoral fontname=italic
                     tooltip="Failed operation - downstream ops will cancel."
                     URL="%(arch_url)s#term-endurance"];
        rescheduled  [shape=oval penwidth=4 fontname=italic
                     tooltip="Operation may fail / provide partial outputs so `net` must reschedule."
                     URL="%(arch_url)s#term-reschedulling"];
        canceled    [shape=oval style=filled fillcolor=Grey fontname=italic
                     tooltip="Canceled operation due to failures or partial outputs upstream."
                     URL="%(arch_url)s#term-reschedule"];
        operation -> insteps -> executed -> failed -> rescheduled -> canceled [style=invis];

        data    [shape=rect
                 tooltip="Any data not given or asked."
                 URL="%(arch_url)s#term-graph"];
        input   [shape=invhouse
                 tooltip="Solution value given into the computation."
                 URL="%(arch_url)s#term-inputs"];
        output  [shape=house
                 tooltip="Solution value asked from the computation."
                 URL="%(arch_url)s#term-outputs"];
        inp_out [shape=hexagon label="inp+out"
                 tooltip="Data both given and asked."
                 URL="%(arch_url)s#term-netop"];
        evicted [shape=rect color="#990000"
                 tooltip="Instruction step to erase data from solution, to save memory."
                 URL="%(arch_url)s#term-evictions"];
        sol     [shape=rect style=filled fillcolor=wheat label="in solution"
                 tooltip="Data contained in the solution."
                 URL="%(arch_url)s#term-solution"];
        overwrite [shape=rect style=filled fillcolor=SkyBlue
                 tooltip="More than 1 values exist in solution with this name."
                 URL="%(arch_url)s#term-overwrites"];
        data -> input -> output -> inp_out -> evicted -> sol -> overwrite [style=invis];

        e1          [style=invis];
        e1          -> requirement;
        requirement [color=invis
                     tooltip="Source operation --> target `provides` OR source `needs` --> target operation."
                     URL="%(arch_url)s#term-needs"];
        requirement -> optional     [style=dashed];
        optional    [color=invis
                     tooltip="Target operation may run without source `need` OR source operation may not `provide` target data."
                     URL="%(arch_url)s#term-needs"];
        optional    -> sideffect    [color=blue];
        sideffect   [color=invis
                     tooltip="Fictive data not consumed/produced by operation functions."
                     URL="%(arch_url)s#term-sideffects"];
        sideffect   -> broken       [color="red" style=dashed]
        broken      [color=invis
                     tooltip="Target data was not `provided` by source operation due to failure / partial-outs."
                     URL="%(arch_url)s#term-partial outputs"];
        broken   -> sequence        [color="#009999" penwidth=4 style=dotted
                                     arrowhead=vee label=1 fontcolor="#009999"];
        sequence    [color=invis penwidth=4 label="execution sequence"
                     tooltip="Sequence of execution steps."
                     URL="%(arch_url)s#term-execution-steps"];
        }
    }
    """ % {
        "arch_url": arch_url
    }

    dot = pydot.graph_from_dot_data(dot_text)[0]
    # cluster = pydot.Cluster("Graphtik legend", label="Graphtik legend")
    # dot.add_subgraph(cluster)

    # nodes = dot.Node()
    # cluster.add_node("operation")

    return render_pydot(dot, filename=filename, show=show)
