# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.

import abc
import sys
from operator import add
from textwrap import dedent

import pytest

from graphtik import base, compose, network, operation, plot
from graphtik.modifiers import optional
from graphtik.netop import NetworkOperation
from graphtik.plot import build_pydot


@pytest.fixture
def pipeline():
    return compose(
        "netop",
        operation(name="add", needs=["a", "b1"], provides=["ab1"])(add),
        operation(name="sub", needs=["a", optional("b2")], provides=["ab2"])(
            lambda a, b=1: a - b
        ),
        operation(name="abb", needs=["ab1", "ab2"], provides=["asked"])(add),
    )


@pytest.fixture(params=[{"a": 1}, {"a": 1, "b1": 2}])
def inputs(request):
    return {"a": 1, "b1": 2}


@pytest.fixture(params=[None, ("a", "b1")])
def input_names(request):
    return request.param


@pytest.fixture(params=[None, ["asked", "b1"]])
def outputs(request):
    return request.param


@pytest.fixture(params=[None, 1])
def solution(pipeline, inputs, outputs, request):
    return request.param and pipeline(inputs, outputs)


###### TEST CASES #######
##


def test_plotting_docstring():
    common_formats = ".png .dot .jpg .jpeg .pdf .svg".split()
    for ext in common_formats:
        assert ext in NetworkOperation.plot.__doc__
        assert ext in network.Network.plot.__doc__


@pytest.mark.slow
def test_plot_formats(pipeline, tmp_path):
    ## Generate all formats  (not needing to save files)

    # run it here (and not in fixture) to ensure `last_plan` exists.
    inputs = {"a": 1, "b1": 2}
    outputs = ["asked", "b1"]
    solution = pipeline.compute(inputs, outputs)

    # The 1st list does not working on my PC, or travis.
    # NOTE: maintain the other lists manually from the Exception message.
    failing_formats = ".dia .hpgl .mif .mp .pcl .pic .vtx .xlib".split()
    # The subsequent format names producing the same dot-file.
    dupe_formats = [
        ".cmapx_np",  # .cmapx
        ".imap_np",  # .imap
        ".jpeg",  # .jpe
        ".jpg",  # .jpe
        ".plain-ext",  # .plain
    ]
    null_formats = ".cmap .ismap".split()
    forbidden_formats = set(failing_formats + dupe_formats + null_formats)
    formats_to_check = sorted(set(plot.supported_plot_formats()) - forbidden_formats)

    # Collect old dots to detect dupes.
    prev_renders = {}
    dupe_errs = []
    for ext in formats_to_check:
        # Check Network.
        #
        render = pipeline.plot(solution=solution).create(format=ext[1:])
        if not render:
            dupe_errs.append("\n  null: %s" % ext)

        elif render in prev_renders.values():
            dupe_errs.append(
                "\n  dupe: %s <--> %s"
                % (ext, [pext for pext, pdot in prev_renders.items() if pdot == render])
            )
        else:
            prev_renders[ext] = render

    if dupe_errs:
        raise AssertionError("Failed pydot formats: %s" % "".join(sorted(dupe_errs)))


def test_plotters_hierarchy(pipeline: NetworkOperation, inputs, outputs):
    # Plotting original network, no plan.
    base_dot = str(pipeline.plot(inputs=inputs, outputs=outputs))
    assert base_dot
    assert pipeline.name in str(base_dot)

    solution = pipeline.compute(inputs, outputs)

    # Plotting delegates to network plan.
    netop_dot = str(pipeline.plot(inputs=inputs, outputs=outputs))
    assert netop_dot
    assert netop_dot != base_dot
    assert pipeline.name in str(netop_dot)

    # Plotting plan alone has not label.
    plan_dot = str(pipeline.last_plan.plot(inputs=inputs, outputs=outputs))
    assert plan_dot
    assert plan_dot != base_dot
    assert plan_dot != netop_dot
    assert pipeline.name not in str(plan_dot)

    # Plot a plan + solution, which must be different from all before.
    sol_netop_dot = str(
        pipeline.plot(inputs=inputs, outputs=outputs, solution=solution)
    )
    assert sol_netop_dot != base_dot
    assert sol_netop_dot != netop_dot
    assert sol_netop_dot != plan_dot
    assert pipeline.name in str(netop_dot)

    # Plot a solution, which must equal plan + sol.
    sol_plan_dot = str(
        pipeline.last_plan.plot(inputs=inputs, outputs=outputs, solution=solution)
    )
    head1 = "digraph plan_x9_nodes {"
    assert sol_plan_dot.startswith(head1)
    sol_dot = str(solution.plot(inputs=inputs, outputs=outputs))
    head2 = "digraph solution_x9_nodes {"
    assert sol_dot.startswith(head2)
    assert sol_plan_dot[len(head1) :] == sol_dot[len(head2) :]

    plan = pipeline.last_plan
    pipeline.last_plan = None

    # We resetted last_plan to check if it reproduces original.
    base_dot2 = str(pipeline.plot(inputs=inputs, outputs=outputs))
    assert str(base_dot2) == str(base_dot)

    # Calling plot directly on plan misses netop.name
    raw_plan_dot = str(plan.plot(inputs=inputs, outputs=outputs))
    assert pipeline.name not in str(raw_plan_dot)

    # Check plan does not contain solution, unless given.
    raw_sol_plan_dot = str(plan.plot(inputs=inputs, outputs=outputs, solution=solution))
    assert raw_sol_plan_dot != raw_plan_dot


def test_plot_bad_format(pipeline, tmp_path):
    with pytest.raises(ValueError, match="Unknown file format") as exinfo:
        pipeline.plot(filename="bad.format")

    ## Check help msg lists all supported formats
    for ext in plot.supported_plot_formats():
        assert exinfo.match(ext)


def test_plot_write_file(pipeline, tmp_path):
    # Try saving a file from one format.

    fpath = tmp_path / "network.png"
    dot1 = pipeline.plot(str(fpath))
    assert fpath.exists()
    assert dot1


def _check_plt_img(img):
    assert img is not None
    assert len(img) > 0


def test_plot_matpotlib(pipeline, tmp_path):
    ## Try matplotlib Window, but # without opening a Window.

    # do not open window in headless travis
    img = pipeline.plot(show=-1)
    _check_plt_img(img)


def test_plot_jupyter(pipeline, tmp_path):
    ## Try returned  Jupyter SVG.

    dot = pipeline.plot()
    s = dot._repr_html_()
    assert "<svg" in s.lower()


def test_plot_legend(pipeline, tmp_path):
    ## Try returned  Jupyter SVG.

    dot = plot.legend()
    assert dot

    img = plot.legend(show=-1)
    _check_plt_img(img)


def test_link_to_legend(pipeline):
    dot = str(pipeline.plot())
    # last argument of `build_pydot(..., legend_url)`.
    assert build_pydot.__defaults__[-1] in dot
    dot = str(pipeline.plot(legend_url=None))
    assert build_pydot.__defaults__[-1] not in dot
    dot = str(pipeline.plot(legend_url=""))
    assert build_pydot.__defaults__[-1] not in dot

    url = "http://example.org"
    dot = str(pipeline.plot(legend_url=url))
    assert build_pydot.__defaults__[-1] not in dot
    assert url in dot


def test_node_quoting():
    dot_str = str(
        compose(
            "graph",
            operation(
                name="node", needs=["edge", "digraph: strict"], provides=["<graph>"]
            )(add),
        ).plot()
    )
    print(dot_str)
    exp = dedent(
        """\
        digraph graph_ {
        fontname=italic;
        label=<graph>;
        splines=ortho;
        <edge> [shape=invhouse];
        <digraph&#58; strict> [shape=invhouse];
        <node> [fontname=italic, shape=oval, tooltip="FunctionalOperation(name=&#x27;node&#x27;, needs=[&#x27;edge&#x27;, &#x27;digraph: strict&#x27;], provides=[&#x27;&lt;graph&gt;&#x27;], fn=&#x27;add&#x27;)"];
        <graph> [shape=house];
        <edge> -> <node>;
        <digraph&#58; strict> -> <node>;
        <node> -> <graph>;
        legend [URL="https://graphtik.readthedocs.io/en/latest/_images/GraphtikLegend.svg", fill_color=yellow, shape=component, style=filled];
        }
        """
    )
    assert dot_str == exp
