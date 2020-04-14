# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
Plotting of graphtik graphs (see also plot-classes on :mod:`.base` module).

Separate from `graphtik.base` to avoid too many imports too early.
from contextlib import contextmanager
"""
import html
import inspect
import io
import json
import logging
import os
import re
import textwrap
from collections import namedtuple
from contextlib import contextmanager
from contextvars import ContextVar
from functools import partial
from typing import Any, Callable, List, Mapping, NamedTuple, Optional, Tuple, Union

import jinja2
import networkx as nx
import pydot
from boltons.iterutils import remap

from .base import PlotArgs, func_name, func_source
from .modifiers import optional
from .network import _EvictInstruction
from .op import Operation

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

        import dot_parser

        def parse_dot_data(s):
            """Patched to fix pydot/pydot#171 by letting ex bubble-up."""
            global top_graphs

            top_graphs = list()
            graphparser = dot_parser.graph_definition()
            graphparser.parseWithTabs()
            tokens = graphparser.parseString(s)
            return list(tokens)

        dot_parser.parse_dot_data = parse_dot_data


_monkey_patch_for_jupyter(pydot)


def _is_class_value_in_list(lst, cls, value):
    return any(isinstance(i, cls) and i == value for i in lst)


def _merge_conditions(*conds):
    """combines conditions as a choice in binary range, eg, 2 conds --> [0, 3]"""
    return sum(int(bool(c)) << i for i, c in enumerate(conds))


def graphviz_html_string(
    s, *, repl_nl=None, repl_colon=None, xmltext=None,
):
    """
    Workaround *pydot* parsing of node-id & labels by encoding as HTML.

    - `pydot` library does not quote DOT-keywords anywhere (pydot#111).
    - Char ``:`` on node-names denote port/compass-points and break IDs (pydot#224).
    - Non-strings are not quoted_if_necessary by pydot.
    - NLs im tooltips of HTML-Table labels `need substitution with the XML-entity
      <see https://stackoverflow.com/a/27448551/548792>`_.
    - HTML-Label attributes (``xmlattr=True``) need both html-escape & quote.

    .. Attention::
        It does not correctly handle ``ID:port:compass-point`` format.

    See https://www.graphviz.org/doc/info/lang.html)
    """
    import html

    if s:
        s = html.escape(s)

        if repl_nl:
            s = s.replace("\n", "&#10;").replace("\t", "&#9;")
        if repl_colon:
            s = s.replace(":", "&#58;")
        if not xmltext:
            s = f"<{s}>"
    return s


def quote_html_tooltips(s):
    """Graphviz HTML-Labels ignore NLs & TABs."""
    if s:
        s = html.escape(s.strip()).replace("\n", "&#10;").replace("\t", "&#9;")
    return s


def quote_node_id(s):
    """See :func:`graphviz_html_string()`"""
    return graphviz_html_string(s, repl_colon=True)


def get_node_name(nx_node):
    if isinstance(nx_node, Operation):
        nx_node = nx_node.name
    return quote_node_id(nx_node)


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


def _pub_props(*d, **kw) -> None:
    """Keep kv-pairs from dictionaries whose keys do not start with underscore(``_``)."""
    return {k: v for k, v in dict(*d, *kw).items() if not str(k).startswith("_")}


class NodeArgs(NamedTuple):
    """All the args for :meth:`.Plotter._make_node()` call. """

    #: Where to add graphviz nodes & stuff.
    dot: pydot.Dot
    #: The node (data(str) or :class:`Operation`) as gotten from nx-graph.
    nx_node: Any = None
    #: Attributes gotten from nx-graph fot the given node.
    node_attrs: dict = None
    #: The pydot-node created
    dot_node: Any = None
    #: Collect the actual clustered `dot_nodes` among the given nodes.
    clustered: dict = None


class Ref:
    """Cross-reference (by default) to :class:`.Style` attribute values."""

    __slots__ = ("ref", "_base")

    def __init__(self, ref, base=None):
        self.ref = ref
        self._base = base

    @property
    def base(self):
        return getattr(self, "_base", None) or Style

    def rebased(self, base):
        """Makes re-based clone to the given class/object."""
        return Ref(self.ref, base)

    @property
    def target(self):
        return getattr(self.base, self.ref, "!missed!")

    def __repr__(self):
        return f"Ref({self.base}, '{self.ref}')"

    def __str__(self):
        return str(self.target)


def _drop_gt_lt(x):
    """SVGs break even with &gt;."""
    return x and re.sub('[<>"]', "_", str(x))


def _escape_or_none(context: jinja2.environment.EvalContext, x, escaper):
    """Do not markup Nones/empties, so `xmlattr` filter soes not include them."""
    return x and jinja2.Markup(escaper(str(x)))


def _truthies(adict):
    import pydot

    try:
        return {k: v for k, v in adict.items() if v}
    except Exception:
        return adict


_jinja2_env = jinja2.Environment()
# Default `escape` filter breaks Nones for xmlattr.

_jinja2_env.filters["ee"] = jinja2.evalcontextfilter(
    partial(_escape_or_none, escaper=html.escape)
)
_jinja2_env.filters["eee"] = jinja2.evalcontextfilter(
    partial(_escape_or_none, escaper=quote_html_tooltips)
)
_jinja2_env.filters["slug"] = jinja2.evalcontextfilter(
    partial(_escape_or_none, escaper=as_identifier)
)
_jinja2_env.filters["hrefer"] = _drop_gt_lt
_jinja2_env.filters["truthies"] = _truthies


def _render_template(tpl: jinja2.Template, **kw) -> str:
    """Ignore falsy values, to skip attributes in template all together. """
    # return tpl.render(**{k: v for k, v in kw.items() if v})
    return tpl.render(**kw)


class Style:
    """
    The poor man's css-like :term:`plot styles` applied like a theme.

    .. NOTE::
        Changing class attributes AFTER the module has loaded WON'T change themes;
        Either patch directly the :attr:`Plotter.style` of :term:`installed plotter`),
        or pass a new styles to a new plotter, as described in :ref:`plot-customizations`.

    """

    ##########
    ## VARIABLES

    fill_color = "wheat"
    failed_color = "LightCoral"
    resched_thickness = 4
    cancel_color = "Grey"
    broken_color = "Red"
    overwrite_color = "SkyBlue"
    steps_color = "#009999"
    in_plan = "#000099"
    #: See :meth:`.Plotter._make_py_item_link()`.
    py_item_url_format: Union[str, Callable[[str], str]] = None
    #: the url to the architecture section explaining *graphtik* glossary,
    #: linked by legend.
    arch_url = "https://graphtik.readthedocs.io/en/latest/arch.html"

    ##########
    ## GRAPH

    kw_graph = {
        "graph_type": "digraph",
        "fontname": "italic",
        # Whether to plot `curved/polyline edges
        # <https://graphviz.gitlab.io/_pages/doc/info/attrs.html#d:splines>`_
        "splines": "ortho",
    }
    #: styles per plot-type
    kw_graph_type = {
        "netop": {},
        "net": {},
        "plan": {},
        "solution": {},
        # nx-graphs without ``_source`` graph-attr
        None: {},
    }
    kw_graph_netop = {}
    kw_graph_net = {}
    kw_graph_plan = {}
    kw_graph_solution = {}

    ##########
    ## DATA node

    kw_data = {}
    kw_data_pruned = {
        "color": Ref("cancel_color"),
        "tooltip": "(pruned node)",
        "fontcolor": Ref("cancel_color"),
    }
    kw_data_in_plan = {"color": Ref("in_plan")}
    kw_data_in_solution = {"style": "filled", "fillcolor": Ref("fill_color")}
    kw_data_overwritten = {"style": "filled", "fillcolor": Ref("overwrite_color")}

    ##########
    ## OPERATION node

    #: Keys to ignore from operation styles & node-attrs,
    #: because they are handled internally by HTML-Label, and/or
    #: interact badly with that label.
    op_bad_html_label_keys = {"shape", "label", "style"}
    op_link_target = fn_link_target = "_top"
    #: props for operation node (outside of label))
    kw_op = {}
    #: props only for HTML-Table label
    kw_op_label = {}
    kw_op_pruned = {"color": Ref("cancel_color"), "fontcolor": Ref("cancel_color")}
    kw_op_executed = {"fillcolor": Ref("fill_color")}
    kw_op_rescheduled = {"penwidth": Ref("resched_thickness")}
    kw_op_endured = {"penwidth": Ref("resched_thickness")}
    kw_op_failed = {"fillcolor": Ref("failed_color")}
    kw_op_canceled = {"fillcolor": Ref("cancel_color")}
    #: Applied only if :attr:`py_item_url_format` defined, or
    #: ``"_op_link_target"`` in nx_node-attributes.

    #: Try to mimic a regular `Graphviz`_ node attributes
    #: (see examples in ``test.test_plot.test_op_template_full()`` for params).
    #: TODO: fix jinja2 template is un-picklable!
    op_template = _jinja2_env.from_string(
        textwrap.dedent(
            """\
            <<TABLE CELLBORDER="0" CELLSPACING="0" STYLE="rounded"
                {{- {
                        'BORDER': penwidth | ee,
                        'COLOR': color | ee,
                        'BGCOLOR': fillcolor | ee
                    } | xmlattr }}
                ><TR><TD BORDER="1" SIDES="b" ALIGN="left"
                {{- {
                        'TOOLTIP': op_tooltip | truncate | eee,
                        'HREF': op_url | hrefer | ee,
                        'TARGET': op_link_target | e
                    } | xmlattr }}
                    >
                    {%- if fontcolor -%}
                        <FONT COLOR="{{ fontcolor }}">
                    {%- endif -%}
                    {{ '<B>OP:</B> <I>%s</I>' % op_name |ee if op_name }}
                    {%- if fontcolor -%}
                        </FONT>
                    {%- endif -%}
                    </TD></TR
                >
                {%- if fn_name -%}
                <TR><TD ALIGN="left" {{- {
                            'TOOLTIP': fn_tooltip | truncate | eee,
                            'HREF': fn_url | hrefer | ee,
                            'TARGET': fn_link_target | e
                        } | xmlattr }}
                    >
                    {%- if fontcolor -%}
                        <FONT COLOR="{{ fontcolor }}">
                    {%- endif -%}
                    <B>FN:</B> {{ fn_name | eee }}
                    {%- if fontcolor -%}
                        </FONT>
                    {%- endif -%}
                    </TD></TR>
                {%- endif %}
            </TABLE>>
            """
        ).strip(),
    )

    ##########
    ## EDGE

    kw_edge = {}
    kw_edge_optional = {"style": "dashed"}
    kw_edge_sideffect = {"color": "blue"}
    kw_edge_rescheduled = {"style": "dotted"}
    kw_edge_endured = {"style": "dotted"}
    kw_edge_broken = {"color": Ref("broken_color")}

    ##########
    ## Other

    #: If ``'URL'``` key missing/empty, no legend icon included in plots.
    kw_legend = {
        "name": "legend",
        "shape": "component",
        "style": "filled",
        "fillcolor": "yellow",
        "URL": "https://graphtik.readthedocs.io/en/latest/_images/GraphtikLegend.svg",
        "target": "_top",
    }

    def __init__(self, **kw):
        """
        Deep-copy class-attributes (to avoid sideffects) and apply user-overrides,

        retargeting any :class:`Ref` on my self (from class's 'values).
        """
        values = _pub_props(vars(type(self)))
        values.update(kw)
        self.resolve_refs(values)

    def resolve_refs(self, values: dict = None) -> None:
        """
        Rebase any refs in styles, and deep copy (for free) as instance attributes.

        :raises:
            Nothing(!), not to CRASH :term:`default installed plotter` on import-time.
            Ref-errors are log-ERROR reported, and the item with the ref is skipped.
        """

        def remap_item(path, k, v):
            if isinstance(v, Ref):
                try:
                    return (k, v.rebased(self).target)
                except Exception as ex:
                    log.error(
                        "Invalid style-ref '%s.%s': '%r' --> '%s' due to: %s",
                        ".".join(path),
                        k,
                        v,
                        v,
                        ex,
                    )
                    return False
            return True

        if values is None:
            values = vars(self)
        vars(self).update(remap(values, remap_item))


class Plotter:
    """
    a :term:`plotter` renders diagram images of :term:`plottable`\\s.

    .. attribute:: Plotter.style

        The :ref:`customizable <plot-customizations>` :class:`.Style` instance
        controlling theming values & dictionaries for plots.
    """

    def __init__(self, style=None):
        self.style = style or Style()

    def copy(self) -> "Plotter":
        """deep copy of all styles"""
        clone = type(self)(self.style)
        clone.__dict__ = remap(vars(self), lambda *a: True)
        return clone

    def plot(self, plot_args: PlotArgs):
        dot = self.build_pydot(plot_args)
        return self.render_pydot(dot, **plot_args.kw_render_pydot)

    def build_pydot(self, plot_args: PlotArgs) -> pydot.Dot:
        """
        Build a |pydot.Dot|_ out of a Network graph/steps/inputs/outputs and return it

        to be fed into `Graphviz`_ to render.

        See :meth:`.Plottable.plot()` for the arguments, sample code, and
        the legend of the plots.
        """
        graph, steps, solution = plot_args.graph, plot_args.steps, plot_args.solution
        style = self.style

        if graph is None:
            raise ValueError("At least `graph` to plot must be given!")

        kw = style.kw_graph.copy()
        kw.update(style.kw_graph_type[graph.graph.get("_source")])
        kw.update(_pub_props(graph.graph))
        dot = pydot.Dot(**kw)

        if plot_args.name:
            dot.set_name(as_identifier(plot_args.name))

        ## NODES
        #
        base_node_args = node_args = NodeArgs(dot=dot, clustered={})
        for nx_node, data in graph.nodes(data=True):
            node_args = base_node_args._replace(nx_node=nx_node, node_attrs=data)

            dot_node = self._make_node(plot_args, node_args)
            node_args = node_args._replace(dot_node=dot_node)

            self._append_or_cluster_node(plot_args, node_args)
        self._append_any_clustered_nodes(plot_args, node_args)

        ## EDGES
        #
        for src, dst, data in graph.edges(data=True):
            src_name = get_node_name(src)
            dst_name = get_node_name(dst)

            kw = {}
            if data.get("optional"):
                kw.update(style.kw_edge_optional)
            if data.get("sideffect"):
                kw.update(style.kw_edge_sideffect)

            is_broken = solution and dst not in solution and dst not in steps
            if getattr(src, "rescheduled", None):
                kw.update(style.kw_edge_rescheduled)
                if is_broken:
                    kw.update(style.kw_edge_broken)
            if getattr(src, "endured", None):
                kw.update(style.kw_edge_endured)
                if is_broken:
                    kw.update(style.kw_edge_broken)

            kw.update(_pub_props(data))
            edge = pydot.Edge(src=src_name, dst=dst_name, **kw)
            dot.add_edge(edge)

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
                    color=style.steps_color,
                    fontcolor=style.steps_color,
                    fontname="bold",
                    fontsize=18,
                    arrowhead="vee",
                    splines=True,
                )
                dot.add_edge(edge)

        self._add_legend_icon(plot_args, node_args)

        return dot

    def _make_node(self, plot_args: PlotArgs, node_args: NodeArgs) -> pydot.Node:
        """
        Customize nodes, e.g. add doc URLs/tooltips, solution tooltips.

        :param plot_args:
            must have, at least, a `graph`, as returned by :class:`.Plottable.prepare_plot_args()`

        :return:
            the updated `plot_args`

        Currently it does the folllowing on operations:

        1. Set fn-link to `fn` documentation url (from edge_props['fn_url'] or discovered).

           .. Note::
               - SVG tooltips may not work without URL on PDFs:
                 https://gitlab.com/graphviz/graphviz/issues/1425

               - Browsers & Jupyter lab are blocking local-urls (e.g. on SVGs),
                 see tip in :term:`plottable`.

        2. Set tooltips with the fn-code for operation-nodes.

        3. Set tooltips with the solution-values for data-nodes.
        """
        from .op import Operation

        nx_node = node_args.nx_node
        node_attrs = node_args.node_attrs
        inputs = plot_args.inputs
        outputs = plot_args.outputs
        steps = plot_args.steps
        solution = plot_args.solution
        style = self.style

        if isinstance(nx_node, str):  # DATA
            # SHAPE change if with inputs/outputs.
            # tip: https://graphviz.gitlab.io/_pages/doc/info/shapes.html
            choice = _merge_conditions(
                inputs and nx_node in inputs, outputs and nx_node in outputs
            )
            shape = "rect invhouse house hexagon".split()[choice]

            kw = {
                "name": quote_node_id(nx_node),
                "shape": shape,
            }

            if steps and nx_node in steps:
                kw.update(style.kw_data_in_plan)

            if solution is not None:
                if nx_node in solution:
                    kw.update(style.kw_data_in_solution)
                    if nx_node in solution.overwrites:
                        kw.update(style.kw_data_overwritten)

                if nx_node not in solution.plan.dag.nodes:
                    kw.update(**style.kw_data_pruned)
                else:
                    val = solution.get(nx_node)
                    tooltip = "None" if val is None else f"({type(val).__name__}) {val}"
                    kw["tooltip"] = quote_html_tooltips(tooltip)

            kw.update(_pub_props(node_attrs))

        else:  # OPERATION
            op_name = nx_node.name
            kw_label = style.kw_op_label.copy()
            kw_label.update(
                {
                    "op_name": op_name,
                    "fn_name": func_name(nx_node.fn, mod=1, fqdn=1, human=1),
                    "op_tooltip": self._make_op_tooltip(plot_args, node_args),
                    "fn_tooltip": self._make_fn_tooltip(plot_args, node_args),
                }
            )

            if steps and nx_node not in steps:
                kw_label.update(style.kw_op_pruned)
            if nx_node.rescheduled:
                kw_label.update(style.kw_op_rescheduled)
            if nx_node.endured:
                kw_label.update(style.kw_op_endured)
            if solution:
                if solution.is_failed(nx_node):
                    kw_label.update(style.kw_op_failed)
                elif nx_node in solution.executed:
                    kw_label.update(style.kw_op_executed)
                elif nx_node in solution.canceled:
                    kw_label.update(style.kw_op_canceled)

            (kw_label["op_url"], kw_label["op_link_target"]) = self._make_op_link(
                plot_args, node_args
            )
            (kw_label["fn_url"], kw_label["fn_link_target"]) = self._make_fn_link(
                plot_args, node_args
            )

            kw_label.update(_pub_props(node_attrs))

            kw = {
                "name": quote_node_id(nx_node.name),
                "shape": "plain",
                "label": _render_template(self.style.op_template, **kw_label),
                "tooltip": graphviz_html_string(op_name),  # or else, "TABLE" shown...
            }

            # Graphviz node attributes interacting badly with HTML-Labels.
            #
            bad_props = style.op_bad_html_label_keys
            kw.update(
                (k, v) for k, v in _pub_props(node_attrs).items() if k not in bad_props
            )

        return pydot.Node(**kw)

    def _make_op_link(
        self, plot_args: PlotArgs, node_args: NodeArgs
    ) -> Tuple[Optional[str], Optional[str]]:
        return self._make_py_item_link(plot_args, node_args, node_args.nx_node, "op")

    def _make_fn_link(
        self, plot_args: PlotArgs, node_args: NodeArgs
    ) -> Tuple[Optional[str], Optional[str]]:
        return self._make_py_item_link(plot_args, node_args, node_args.nx_node.fn, "fn")

    def _make_py_item_link(
        self, plot_args: PlotArgs, node_args: NodeArgs, item, prefix
    ) -> Tuple[Optional[str], Optional[str]]:
        """
        Deduce fn's url (e.g. docs) from style, or from override in  `node_attrs`.

        :return:
            Search and return, in this order, any pair with truthy "url" element:

            1. node-attrs: ``(_{prefix}_url, _{prefix}_link_target)``
            2. style-attributes: ``({prefix}_url, {prefix}_link_target)``
            3. fallback: ``(None, None)``

            An existent link-target from (1) still applies even if (2) is selected.
        """
        node_attrs = node_args.node_attrs
        if f"_{prefix}_url" in node_attrs:
            return (
                node_attrs[f"_{prefix}_url"],
                node_attrs.get(f"_{prefix}_link_target"),
            )

        fn_link = (None, None)
        url_format = self.style.py_item_url_format
        if url_format:
            dot_path = func_name(node_args.nx_node.fn, None, mod=1, fqdn=1, human=0)
            if dot_path:
                url_data = {
                    "dot_path": dot_path,
                    "posix_path": dot_path.replace(".", "/"),
                }

                fn_url = (
                    url_format(url_data)
                    if callable(url_format)
                    else url_format % url_data
                )
                fn_link = (
                    fn_url,
                    node_attrs.get(f"_{prefix}_link_target", self.style.fn_link_target),
                )

        return fn_link

    def _make_op_tooltip(self, plot_args: PlotArgs, node_args: NodeArgs):
        """the string-representation of an operation (name, needs, provides)"""
        return node_args.node_attrs.get("_op_tooltip", str(node_args.nx_node))

    def _make_fn_tooltip(self, plot_args: PlotArgs, node_args: NodeArgs):
        """the sources of the operation-function"""
        if "_fn_tooltip" in node_args.node_attrs:
            return node_args.node_attrs["_fn_tooltip"]

        fn_source = func_source(node_args.nx_node.fn, None, human=1)
        if fn_source:
            fn_source = fn_source

        return fn_source

    def _append_or_cluster_node(self, plot_args: PlotArgs, node_args: NodeArgs) -> None:
        """Add dot-node in dot now, or "cluster" it, to be added later. """
        # TODO remap netsed plot-clusters:
        clusters = plot_args.clusters
        clustered = node_args.clustered
        nx_node = node_args.nx_node

        if not clusters or not nx_node in clusters:
            node_args.dot.add_node(node_args.dot_node)
        else:
            cluster_name = clusters[nx_node]
            node_cluster = clustered.get(cluster_name)
            if not node_cluster:
                node_cluster = clustered[cluster_name] = pydot.Cluster(
                    cluster_name, label=cluster_name
                )
            node_cluster.add_node(node_args.dot_node)

    def _append_any_clustered_nodes(
        self, plot_args: PlotArgs, node_args: NodeArgs
    ) -> None:
        # TODO remap netsed plot-clusters:
        dot = node_args.dot
        for cluster in node_args.clustered.values():
            dot.add_subgraph(cluster)

    def _add_legend_icon(self, plot_args: PlotArgs, node_args: NodeArgs):
        """Optionally add an icon to diagrams linking to legend (if url given)."""
        kw_legend = self.style.kw_legend
        if kw_legend and self.style.kw_legend.get("URL"):
            node_args.dot.add_node(pydot.Node(**kw_legend))

    def render_pydot(
        self, dot: pydot.Dot, filename=None, show=False, jupyter_render: str = None
    ):
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
            If `None`, defaults to :data:`default_jupyter_render`;  you may modify those
            in place and they will apply for all future calls (see :ref:`jupyter_rendering`).

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

    def legend(self, filename=None, show=None, jupyter_render: Mapping = None):
        """
        Generate a legend for all plots (see :meth:`.Plottable.plot()` for args)

        See :meth:`Plotter.render_pydot` for the rest arguments.
        """

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
            rescheduled [shape=oval penwidth=4 fontname=italic label=<endured/rescheduled>
                        tooltip="Operation may fail or provide partial outputs so `net` must reschedule."
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
            evicted [shape=rect color="%(in_plan)s"
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
            broken   -> sequence        [color="%(steps_color)s" penwidth=4 style=dotted
                                        arrowhead=vee label=1 fontcolor="%(steps_color)s"];
            sequence    [color=invis penwidth=4 label="execution sequence"
                        tooltip="Sequence of execution steps."
                        URL="%(arch_url)s#term-execution-steps"];
            }
        }
        """ % {
            "arch_url": self.style.arch_url,
            **vars(self.style),
        }

        dot = pydot.graph_from_dot_data(dot_text)[0]
        # cluster = pydot.Cluster("Graphtik legend", label="Graphtik legend")
        # dot.add_subgraph(cluster)

        # nodes = dot.Node()
        # cluster.add_node("operation")

        return self.render_pydot(dot, filename=filename, show=show)


def legend(
    filename=None, show=None, jupyter_render: Mapping = None, plotter: Plotter = None,
):
    """
    Generate a legend for all plots (see :meth:`.Plottable.plot()` for args)

    :param plotter:
        override the :term:`installed plotter`

    See :meth:`Plotter.render_pydot` for the rest arguments.
    """
    plotter = plotter or get_installed_plotter()
    return plotter.legend(filename, show, jupyter_render)


def supported_plot_formats() -> List[str]:
    """return automatically all `pydot` extensions"""
    return [".%s" % f for f in pydot.Dot().formats]


_installed_plotter: ContextVar[Plotter] = ContextVar(
    "installed_plotter", default=Plotter()
)


@contextmanager
def installed_plotter(plotter: Plotter) -> None:
    """Like :func:`set_installed_plotter()` as a context-manager to reset old value. """
    if not isinstance(plotter, Plotter):
        raise ValueError(f"Cannot install invalid plotter: {plotter}")
    resetter = _installed_plotter.set(plotter)
    try:
        yield
    finally:
        _installed_plotter.reset(resetter)


def set_installed_plotter(plotter: Plotter):
    """
    The default instance to render :term:`plottable`\\s,

    unless overridden with a `plotter` argument in :meth:`.Plottable.plot()`.

    :param plotter:
        the :class:`plotter` instance to install
    """
    if not isinstance(plotter, Plotter):
        raise ValueError(f"Cannot install invalid plotter: {plotter}")
    return _installed_plotter.set(plotter)


def get_installed_plotter() -> Plotter:
    """Get the previously installed  :class:`.Plotter` instance or default one."""
    plotter = _installed_plotter.get()
    if not isinstance(plotter, Plotter):
        raise ValueError(f"Missing or invalid installed plotter: {plotter}")

    return plotter
