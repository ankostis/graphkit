# Copyright 2020-2020, Kostis Anagnostopoulos;
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""Test :term:`accessor` & :term:`hierarchical data`. """
import operator
import os
import re
from collections import namedtuple

import pytest

from graphtik import NO_RESULT, compose, opcb, operation, sfxed, vararg
from graphtik.base import RenArgs
from graphtik.config import solution_layered
from graphtik.modifier import dep_renamed, modify


pytestmark = pytest.mark.usefixtures("log_levels")


def test_solution_accessor_simple():
    acc = (operator.contains, operator.getitem, operator.setitem, operator.delitem)

    copy_values = compose(
        "copy values in solution: a+b-->A+BB",
        operation(
            (lambda *a: a),
            needs=[modify("a", accessor=acc), modify("b", accessor=acc)],
            provides=[modify("A", accessor=acc), modify("BB", accessor=acc)],
        ),
    )
    sol = copy_values.compute({"a": 1, "b": 2})
    assert sol == {"a": 1, "b": 2, "A": 1, "BB": 2}


def test_jsonp_disabled():
    no_jsonp_res = operation(
        fn=None,
        name="copy a+b-->A+BB",
        needs=[modify("inputs/a"), modify("inputs/b")],
        provides=["RESULTS/A", modify("RESULTS/BB", jsonp=False)],
    )
    assert "provides=['RESULTS/A'($), RESULTS/BB" in str(no_jsonp_res)
    res = compose("", no_jsonp_res).compute({"inputs": {"a": 1, "b": 2}})
    assert res == {"inputs": {"a": 1, "b": 2}, "RESULTS": {"A": 1}, "RESULTS/BB": 2}

    no_jsonp_inp = operation(
        name="copy a+b-->A+BB",
        needs=["inputs/a", modify("inputs/b", jsonp=False)],
        provides=["RESULTS/A", "RESULTS/BB"],
    )()
    assert "needs=['inputs/a'($), inputs/b]" in str(no_jsonp_inp)
    res = compose("", no_jsonp_inp).compute({"inputs": {"a": 1}, "inputs/b": 2})
    assert res == {"inputs": {"a": 1}, "inputs/b": 2, "RESULTS": {"A": 1, "BB": 2}}


def test_jsonp_and_conveyor_fn_simple():
    copy_values = operation(
        name="copy a+b-->A+BB",
        needs=["inputs/a", "inputs/b"],
        provides=["RESULTS/A", "RESULTS/BB"],
    )()

    ## Ops are unaware of subdocs, Solution does.
    #
    with pytest.raises(ValueError, match="Missing compulsory needs"):
        copy_values.compute({"inputs": {"a": 1, "b": 2}})
    results = copy_values.compute({"inputs/a": 1, "inputs/b": 2})
    assert results == {"RESULTS/A": 1, "RESULTS/BB": 2}
    assert repr(results) == "{'RESULTS/A'($): 1, 'RESULTS/BB'($): 2}"

    pipe = compose("t", copy_values)
    sol = pipe.compute({"inputs": {"a": 1, "b": 2}})
    assert sol == {"inputs": {"a": 1, "b": 2}, "RESULTS": {"A": 1, "BB": 2}}
    sol = pipe.compute({"inputs": {"a": 1, "b": 2}}, outputs="RESULTS")
    assert sol == {"RESULTS": {"A": 1, "BB": 2}}
    sol = pipe.compute({"inputs": {"a": 1, "b": 2}}, outputs="RESULTS/A")
    assert sol == {"RESULTS": {"A": 1}}


@pytest.fixture(params=[(True, None), (True, False), (False, True)])
def solution_layered_true(request):
    with_config, compute_param = request.param

    if with_config:
        with solution_layered(True):
            yield compute_param
    else:
        yield compute_param


@pytest.fixture(params=[(True, None), (True, False), (False, None), (False, False)])
def solution_layered_false(request):
    with_config, compute_param = request.param

    if with_config:
        with solution_layered(False):
            yield compute_param
    else:
        yield compute_param


def test_jsonp_and_conveyor_fn_complex_LAYERED(solution_layered_true):
    pipe = compose(
        "t",
        operation(
            name="op1",
            needs=["i/a", "i/a"],  # dupe jsonp needs
            provides=["r/a", modify("a")],
        )(),
        operation(
            lambda x: (x, 2 * x), name="op2", needs=["r/a"], provides=["r/A", "r/AA"]
        ),
    )
    inp = {"i": {"a": 1}}
    sol = pipe.compute(inp, layered_solution=solution_layered_true)
    assert sol == {"i": {"a": 1}, "r": {"A": 1, "AA": 2}, "a": 1}
    sol = pipe.compute(inp, outputs="r", layered_solution=solution_layered_true)
    assert sol == {"r": {"A": 1, "AA": 2}}
    sol = pipe.compute(
        inp, outputs=["r/A", "r/AA"], layered_solution=solution_layered_true
    )
    assert sol == {"r": {"A": 1, "AA": 2}}
    sol = pipe.compute(inp, outputs="r/AA", layered_solution=solution_layered_true)
    assert sol == {"r": {"AA": 2}}


def test_jsonp_and_conveyor_fn_complex_NOT_LAYERED(solution_layered_false):
    pipe = compose(
        "t",
        operation(
            name="op1",
            needs=["i/a", "i/a"],  # dupe jsonp needs
            provides=["r/a", modify("a")],
        )(),
        operation(
            lambda x: (x, 2 * x), name="op2", needs=["r/a"], provides=["r/A", "r/AA"]
        ),
    )
    inp = {"i": {"a": 1}}
    sol = pipe.compute(inp, layered_solution=solution_layered_false)
    assert sol == {**inp, "r": {"a": 1, "A": 1, "AA": 2}, "a": 1}
    sol = pipe.compute(inp, outputs="r", layered_solution=solution_layered_false)
    assert sol == {"r": {"a": 1, "A": 1, "AA": 2}}
    sol = pipe.compute(
        inp, outputs=["r/A", "r/AA"], layered_solution=solution_layered_false
    )
    assert sol == {"r": {"a": 1, "A": 1, "AA": 2}}  ## FIXME: should have evicted r/a!
    sol = pipe.compute(inp, outputs="r/AA", layered_solution=solution_layered_false)
    assert sol == {"r": {"a": 1, "AA": 2}}  ## FIXME: should have evicted r/a!


def test_network_nest_subdocs_LAYERED(solution_layered_true):
    days = ["Monday", "Tuesday", "Wednesday"]
    todos = sfxed("backlog", "todos")

    @operation(
        name="wake up",
        needs=[opcb("op"), "backlog"],
        provides=["tasks", todos],
        rescheduled=True,
    )
    def pick_tasks(backlog, op):
        if not backlog:
            return NO_RESULT
        # Pick from backlog 1/3 of len-of-chars of my operation's (day) name.
        n_tasks = int(len(op.name) / 3)
        my_tasks, todos = backlog[:n_tasks], backlog[n_tasks:]
        return my_tasks, todos

    do_tasks = operation(None, name="work!", needs="tasks", provides="daily_tasks")

    weekday = compose("weekday", pick_tasks, do_tasks)
    weekdays = [weekday.withset(name=d) for d in days]

    def nester(ra: RenArgs):
        dep = ra.name
        if ra.typ == "op":
            return True
        if ra.typ.endswith(".jsonpart"):
            return False
        if dep == "tasks":
            return True
        # if is_sfxed(dep):
        #     return modifier_withset(
        #         dep, sfx_list=[f"{ra.parent.name}.{s}" for s in dep._sfx_list]
        #     )
        if dep == "daily_tasks":
            return dep_renamed(dep, lambda n: f"{n}/{ra.parent.name}")
        return False

    week = compose("week", *weekdays, nest=nester)
    assert str(week) == re.sub(
        r"[\n ]{2,}",  # collapse all space-chars into a single space
        " ",
        """
        Pipeline('week', needs=['backlog', 'Monday.tasks', 'Tuesday.tasks', 'Wednesday.tasks'],
        provides=['Monday.tasks', sfxed('backlog', 'todos'),
                  'daily_tasks/Monday'($), 'Tuesday.tasks', 'daily_tasks/Tuesday'($),
                  'Wednesday.tasks', 'daily_tasks/Wednesday'($)],
        x6 ops: Monday.wake up, Monday.work!, Tuesday.wake up, Tuesday.work!,
        Wednesday.wake up, Wednesday.work!)
        """.strip(),
    )

    ## Add collector after nesting

    @operation(
        name="collect tasks",
        needs=[todos, *(vararg(f"daily_tasks/{d}") for d in days)],
        provides=["weekly_tasks", "todos"],
    )
    def collector(backlog, *daily_tasks):
        return daily_tasks or (), backlog or ()

    week = compose("week", week, collector)
    assert str(week) == re.sub(
        r"[\n ]{2,}",  # collapse all space-chars into a single space
        " ",
        """
        Pipeline('week',
            needs=['backlog',
                'Monday.tasks', 'Tuesday.tasks', 'Wednesday.tasks',
                sfxed('backlog', 'todos'),
                'daily_tasks/Monday'($?), 'daily_tasks/Tuesday'($?), 'daily_tasks/Wednesday'($?)],
            provides=['Monday.tasks',
                sfxed('backlog', 'todos'), 'daily_tasks/Monday'($),
                'Tuesday.tasks', 'daily_tasks/Tuesday'($),
                'Wednesday.tasks', 'daily_tasks/Wednesday'($),
                'weekly_tasks', 'todos'],
            x7 ops: Monday.wake up, Monday.work!, Tuesday.wake up, Tuesday.work!,
                    Wednesday.wake up, Wednesday.work!, collect tasks)
        """.strip(),
    )

    # +3 from week's capacity: 4 + 5 + 5

    sol = week.compute({"backlog": range(17)}, layered_solution=solution_layered_true)
    assert sol == {
        "backlog": range(14, 17),
        "Monday.tasks": range(0, 4),
        "daily_tasks": {"Wednesday": range(9, 14)},
        "Tuesday.tasks": range(4, 9),
        "Wednesday.tasks": range(9, 14),
        "weekly_tasks": (range(0, 4), range(4, 9), range(9, 14)),
        "todos": range(14, 17),
    }

    assert sol.overwrites == {
        "daily_tasks": [
            {"Wednesday": range(9, 14)},
            {"Tuesday": range(4, 9)},
            {"Monday": range(0, 4)},
        ],
        "backlog": [range(14, 17), range(9, 17), range(4, 17), range(0, 17)],
    }

    ## -1 tasks for Wednesday to enact

    sol = week.compute({"backlog": range(9)}, layered_solution=solution_layered_true)
    assert sol == {
        "backlog": range(9, 9),
        "Monday.tasks": range(0, 4),
        "daily_tasks": {"Tuesday": range(4, 9)},
        "Tuesday.tasks": range(4, 9),
        sfxed("backlog", "todos"): False,
        "weekly_tasks": (range(0, 4), range(4, 9)),
        "todos": (),
    }

    assert sol.overwrites == {
        "daily_tasks": [{"Tuesday": range(4, 9)}, {"Monday": range(0, 4)}],
        "backlog": [range(9, 9), range(4, 9), range(0, 9)],
    }

    sol = week.compute(
        {"backlog": range(9)},
        outputs=["backlog", "daily_tasks", "weekly_tasks", "todos"],
        layered_solution=solution_layered_false,
    )
    assert sol == {
        "backlog": range(9, 9),
        "daily_tasks": {"Tuesday": range(4, 9)},
        "weekly_tasks": (range(0, 4), range(4, 9)),
        "todos": (),
    }

    ## Were failing due to eager eviction of "backlog".
    #
    sol = week.compute(
        {"backlog": range(9)},
        outputs=["daily_tasks", "weekly_tasks", "todos"],
        layered_solution=solution_layered_false,
    )
    assert sol == {
        "daily_tasks": {"Tuesday": range(4, 9)},
        "weekly_tasks": (range(0, 4), range(4, 9)),
        "todos": (),
    }

    sol = week.compute(
        {"backlog": range(9)},
        outputs="daily_tasks/Monday",
        layered_solution=solution_layered_true,
    )
    assert sol == {"daily_tasks": {"Monday": range(0, 4)}}
    assert sol.overwrites == {}
    sol = week.compute(
        {"backlog": range(9)},
        outputs="daily_tasks",
        layered_solution=solution_layered_true,
    )
    assert sol == {"daily_tasks": {"Tuesday": range(4, 9)}}
    assert sol.overwrites == {
        "daily_tasks": [{"Tuesday": range(4, 9)}, {"Monday": range(0, 4)}]
    }


def test_network_nest_subdocs_NOT_LAYERED(solution_layered_false):
    days = ["Monday", "Tuesday", "Wednesday"]
    todos = sfxed("backlog", "todos")

    @operation(
        name="wake up",
        needs=[opcb(), "backlog"],
        provides=["tasks", todos],
        rescheduled=True,
    )
    def pick_tasks(op, backlog):
        if not backlog:
            return NO_RESULT
        # Pick from backlog 1/3 of len-of-chars of my operation's (day) name.
        n_tasks = int(len(op.name) / 3)
        my_tasks, todos = backlog[:n_tasks], backlog[n_tasks:]
        return my_tasks, todos

    do_tasks = operation(None, name="work!", needs="tasks", provides="daily_tasks")

    weekday = compose("weekday", pick_tasks, do_tasks)
    weekdays = [weekday.withset(name=d) for d in days]

    def nester(ra: RenArgs):
        dep = ra.name
        if ra.typ == "op":
            return True
        if ra.typ.endswith(".jsonpart"):
            return False
        if dep == "tasks":
            return True
        # if is_sfxed(dep):
        #     return modifier_withset(
        #         dep, sfx_list=[f"{ra.parent.name}.{s}" for s in dep._sfx_list]
        #     )
        if dep == "daily_tasks":
            return dep_renamed(dep, lambda n: f"{n}/{ra.parent.name}")
        return False

    week = compose("week", *weekdays, nest=nester)
    assert str(week) == re.sub(
        r"[\n ]{2,}",  # collapse all space-chars into a single space
        " ",
        """
        Pipeline('week', needs=['backlog', 'Monday.tasks', 'Tuesday.tasks', 'Wednesday.tasks'],
        provides=['Monday.tasks', sfxed('backlog', 'todos'),
                  'daily_tasks/Monday'($), 'Tuesday.tasks', 'daily_tasks/Tuesday'($),
                  'Wednesday.tasks', 'daily_tasks/Wednesday'($)],
        x6 ops: Monday.wake up, Monday.work!, Tuesday.wake up, Tuesday.work!,
        Wednesday.wake up, Wednesday.work!)
        """.strip(),
    )

    ## Add collector after nesting

    @operation(
        name="collect tasks",
        needs=[todos, *(vararg(f"daily_tasks/{d}") for d in days)],
        provides=["weekly_tasks", "todos"],
    )
    def collector(backlog, *daily_tasks):
        return daily_tasks or (), backlog or ()

    week = compose("week", week, collector)
    assert str(week) == re.sub(
        r"[\n ]{2,}",  # collapse all space-chars into a single space
        " ",
        """
        Pipeline('week',
            needs=['backlog',
                'Monday.tasks', 'Tuesday.tasks', 'Wednesday.tasks',
                sfxed('backlog', 'todos'),
                'daily_tasks/Monday'($?), 'daily_tasks/Tuesday'($?), 'daily_tasks/Wednesday'($?)],
            provides=['Monday.tasks',
                sfxed('backlog', 'todos'), 'daily_tasks/Monday'($),
                'Tuesday.tasks', 'daily_tasks/Tuesday'($),
                'Wednesday.tasks', 'daily_tasks/Wednesday'($),
                'weekly_tasks', 'todos'],
            x7 ops: Monday.wake up, Monday.work!, Tuesday.wake up, Tuesday.work!,
                    Wednesday.wake up, Wednesday.work!, collect tasks)
        """.strip(),
    )

    # +3 from week's capacity: 4 + 5 + 5

    sol = week.compute({"backlog": range(17)}, layered_solution=solution_layered_false)
    assert sol == {
        "backlog": range(14, 17),
        "Monday.tasks": range(0, 4),
        "daily_tasks": {
            "Monday": range(0, 4),
            "Tuesday": range(4, 9),
            "Wednesday": range(9, 14),
        },
        "Tuesday.tasks": range(4, 9),
        "Wednesday.tasks": range(9, 14),
        "weekly_tasks": (range(0, 4), range(4, 9), range(9, 14)),
        "todos": range(14, 17),
    }

    assert sol.overwrites == {
        "backlog": [range(14, 17), range(9, 17), range(4, 17), range(0, 17)]
    }

    ## -1 tasks for Wednesday to enact

    sol = week.compute({"backlog": range(9)}, layered_solution=solution_layered_false)
    assert sol == {
        "backlog": range(9, 9),
        "Monday.tasks": range(0, 4),
        "daily_tasks": {
            "Monday": range(0, 4),
            "Tuesday": range(4, 9),
        },
        "Tuesday.tasks": range(4, 9),
        sfxed("backlog", "todos"): False,
        "weekly_tasks": (range(0, 4), range(4, 9)),
        "todos": (),
    }
    assert sol.overwrites == {"backlog": [range(9, 9), range(4, 9), range(0, 9)]}
    sol = week.compute(
        {"backlog": range(9)},
        outputs=["backlog", "daily_tasks", "weekly_tasks", "todos"],
        layered_solution=solution_layered_false,
    )
    assert sol == {
        "backlog": range(9, 9),
        "daily_tasks": {
            "Monday": range(0, 4),
            "Tuesday": range(4, 9),
        },
        "weekly_tasks": (range(0, 4), range(4, 9)),
        "todos": (),
    }

    ## Were failing due to eager eviction of "backlog".
    #
    sol = week.compute(
        {"backlog": range(9)},
        outputs=["daily_tasks", "weekly_tasks", "todos"],
        layered_solution=solution_layered_false,
    )
    assert sol == {
        "daily_tasks": {
            "Monday": range(0, 4),
            "Tuesday": range(4, 9),
        },
        "weekly_tasks": (range(0, 4), range(4, 9)),
        "todos": (),
    }

    sol = week.compute(
        {"backlog": range(9)},
        outputs="daily_tasks/Monday",
        layered_solution=solution_layered_false,
    )
    assert sol == {"daily_tasks": {"Monday": range(0, 4)}}
    assert sol.overwrites == {}
    sol = week.compute(
        {"backlog": range(9)},
        outputs="daily_tasks",
        layered_solution=solution_layered_false,
    )
    assert sol == {
        "daily_tasks": {
            "Monday": range(0, 4),
            "Tuesday": range(4, 9),
        }
    }
    assert sol.overwrites == {}
