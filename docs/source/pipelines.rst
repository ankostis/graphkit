.. _graph-composition:

Pipelines
=========

Graphtik's :func:`.operation.compose()` factory handles the work of tying together ``operation``
instances into a runnable computation graph.

The simplest use case is to assemble a collection of individual operations
into a runnable computation graph.
The sample formula :eq:`sample-formula` from :ref:`quick-start` section illustrates this well:

    >>> from operator import mul, sub
    >>> from functools import partial
    >>> from graphtik import compose, operation

    >>> def abspow(a, p):
    ...    """Computes |a|^p. """
    ...    c = abs(a) ** p
    ...    return c


The call here to ``compose()`` yields a runnable computation graph that looks like this
(where the circles are operations, squares are data, and octagons are parameters):

    >>> # Compose the mul, sub, and abspow operations into a computation graph.
    >>> formula = compose("maths",
    ...    operation(name="mul1", needs=["α", "β"], provides=["α×β"])(mul),
    ...    operation(name="sub1", needs=["α", "α×β"], provides=["α-α×β"])(sub),
    ...    operation(name="abspow1", needs=["α-α×β"], provides=["|α-α×β|³"])
    ...    (partial(abspow, p=3))
    ... )

This yields a graph which looks like this (see :ref:`plotting`):

.. graphtik::
    :name: sample formula :eq:`sample-formula`

    >>> formula.plot('calc_power.svg')  # doctest: +SKIP


.. _graph-computations:

Compiling and Running a pipeline
--------------------------------

The graph composed above can be run by simply calling it with a dictionary of values
with keys corresponding to the named :term:`dependencies <dependency>` (`needs` &
`provides`):

    >>> # Run the graph and request all of the outputs.
    >>> out = formula(α=2, β=5)
    >>> out
    {'α': 2, 'β': 5, 'α×β': 10, 'α-α×β': -8, '|α-α×β|³': 512}

You may plot the solution:

.. graphtik::
    :caption: The solution of the graph.
    :graphvar: out

    >>> out.plot('a_solution.svg')  # doctest: +SKIP

Alternatively, you may :term:`compile` only (and :meth:`~.ExecutionPlan.validate`)
the pipeline, to see which operations will be included in the :term:`graph`
(assuming the graph is solvable at all), based on the given :term:`inputs`/:term:`outputs`
combination:

    >>> plan = formula.compile(inputs=['α', 'β'], outputs='α-α×β')
    >>> plan
    ExecutionPlan(needs=['α', 'β'],
                  provides=['α-α×β'],
                  x5 steps: mul1, β, sub1, α, α×β)
    >>> plan.validate()  # all fine

.. _pruned-explanations:

Plotting the :term:`plan <execution plan>` reveals the :term:`prune`\d operations,
and numbers operations and :term:`eviction`\s (see next section) in the order
of execution:

.. graphtik::
    :caption: Obtaining just the :term:`execution plan`.
    :graphvar: plan

    >>> plan.plot()  # doctest: +SKIP

.. tip::
    Hover over pruned (grey) operations to see why they were excluded from the plan.

But if an impossible combination of `inputs` & `outputs`
is asked, the plan comes out empty:

    >>> plan = formula.compile(inputs='α', outputs="α-α×β")
    >>> plan
    ExecutionPlan(needs=[], provides=[], x0 steps: )
    >>> plan.validate()
    Traceback (most recent call last):
    ValueError: Unsolvable graph:
      +--Network(x8 nodes, x3 ops: mul1, sub1, abspow1)
      +--possible inputs: ['α', 'β', 'α×β', 'α-α×β']
      +--possible outputs: ['α×β', 'α-α×β', '|α-α×β|³']

Evictions: producing a subset of outputs
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^

By default, calling a graph-operation on a set of inputs will yield all of
that graph's :term:`outputs`.
You can use the ``outputs`` parameter to request only a subset.
For example, if ``formula`` is as above:

    >>> # Run the graph-operation and request a subset of the outputs.
    >>> out = formula.compute({'α': 2, 'β': 5}, outputs="α-α×β")
    >>> out
    {'α-α×β': -8}

.. graphtik::

When asking a subset of the graph's `outputs`, Graphtik does 2 things:

- it :term:`prune`\s any :term:`operation`\s that are not on the path from
  given :term:`inputs` to the requested `outputs` (e.g. the ``abspow1`` operation, above,
  is not executed);
- it :term:`evicts <eviction>` any intermediate data from :term:`solution` as soon as
  they are not needed.

You may see (2) in action by including the sequence of :term:`execution steps`
into the plot:

.. graphtik::

    >>> dot = out.plot(theme={"show_steps": True})

.. tip::
   Read :ref:`plot-customizations` to understand the trick with
   the :term:`plot theme`, above.

Short-circuiting a pipeline
^^^^^^^^^^^^^^^^^^^^^^^^^^^

You can short-circuit a graph computation, making certain inputs unnecessary,
by providing a value in the graph that is further downstream in the graph than those inputs.
For example, in the graph-operation we've been working with, you could provide
the value of ``α-α×β`` to make the inputs ``α`` and ``β`` unnecessary:

    >>> # Run the graph-operation and request a subset of the outputs.
    >>> out = formula.compute({"α-α×β": -8})
    >>> out
    {'α-α×β': -8, '|α-α×β|³': 512}

.. graphtik::

When you do this, any ``operation`` nodes that are not on a path from the downstream input
to the requested outputs (i.e. predecessors of the downstream input) are not computed.
For example, the ``mul1`` and ``sub1`` operations are not executed here.

This can be useful if you have a graph-operation that accepts alternative forms of the same input.
For example, if your graph-operation requires a ``PIL.Image`` as input, you could
allow your graph to be run in an API server by adding an earlier ``operation``
that accepts as input a string of raw image data and converts that data into the needed ``PIL.Image``.
Then, you can either provide the raw image data string as input, or you can provide
the ``PIL.Image`` if you have it and skip providing the image data string.


.. _recompute:

Re-computations
^^^^^^^^^^^^^^^
If you take the :term:`solution` from a :term:`pipeline`, change some values in it,
and feed it back into the same pipeline as :term:`inputs`, the recomputation will,
unexpectedly, fail with ``Unsolvable graph`` error -- all :term:`dependencies <dependency>`
have already values, therefore any operations producing them are :term:`prune`\d out,
till no operation remains:

    >>> new_inp = formula.compute({"α": 2, "β": 5})
    >>> new_inp["α"] = 20
    >>> formula.compute(new_inp)
    Traceback (most recent call last):
    ValueError: Unsolvable graph:
    +--Network(x8 nodes, x3 ops: mul1, sub1, abspow1)
    +--possible inputs: ['α', 'β', 'α×β', 'α-α×β']
    +--possible outputs: ['α×β', 'α-α×β', '|α-α×β|³']


One way to proceed is to avoid recompiling, by executing directly the pre-compiled
:term:`plan`, which will run all the original operations on the new values:

    >>> sol = new_inp.plan.execute(new_inp)
    >>> sol
    {'α': 20, 'β': 5, 'α×β': 100, 'α-α×β': -80, '|α-α×β|³': 512000}
    >>> [op.name for op in sol.executed]
    ['mul1', 'sub1', 'abspow1']

.. graphtik::
.. hint::
    Notice that all values have been marked as :term:`overwrite`\s.

But that trick wouldn't work if the modified value is an inner dependency
of the :term:`graph` -- in that case, the operations upstream would simply overwrite it:

    >>> new_inp["α-α×β"] = 123
    >>> sol = new_inp.plan.execute(new_inp)
    >>> sol["α-α×β"]  # should have been 123!
    -80

You can  still do that using the ``recompute_from`` argument of :meth:`.Pipeline.compute()`.
It accepts a string/list of dependencies to :term:`recompute`, *downstream*:

    >>> sol = formula.compute(new_inp, recompute_from="α-α×β")
    >>> sol
    {'α': 20, 'β': 5, 'α×β': 10, 'α-α×β': 123, '|α-α×β|³': 1860867}
    >>> [op.name for op in sol.executed]
    ['abspow1']

.. graphtik::

The old values are retained, although the operations producing them
have been pruned from the plan.

.. Note::
    The value of ``α-α×β`` is no longer *the correct result* of ``sub1`` operation,
    above it (hover to see ``sub1`` inputs & output).


Extending pipelines
-------------------
Sometimes we begin with existing computation graph(s) to which we want to extend
with other operations and/or pipelines.

There are 2 ways to :term:`combine pipelines` together, :term:`merging
<operation merging>` (the default) and :term:`nesting <operation nesting>`.

.. _operation-merging:

Merging
^^^^^^^
This is the default mode for :func:`.compose()` when when combining individual operations,
and it works exactly the same when whole pipelines are involved.

For example, lets suppose that this simple pipeline describes the daily scheduled
workload of an "empty' day:

    >>> weekday = compose("weekday",
    ...     operation(str, name="wake up", needs="backlog", provides="tasks"),
    ...     operation(str, name="sleep", needs="tasks", provides="todos"),
    ... )

.. graphtik::
    :graphvar: weekday

Now let's do some "work":

    >>> weekday = compose("weekday",
    ...     operation(lambda t: (t[:-1], t[-1:]),
    ...               name="work!", needs="tasks", provides=["tasks done", "todos"]),
    ...     operation(str, name="sleep"),
    ...     weekday,
    ... )

.. graphtik::
    :graphvar: weekday

Notice that the pipeline to override was added last, at the bottom; that's because
the operations *added earlier in the call (further to the left) override any
identically-named operations added later*.

Notice also that the overridden "sleep" operation hasn't got any actual role
in the schedule.
We can eliminate "sleep" by using the ``excludes`` argument (it accepts also list):

    >>> weekday = compose("weekday", weekday, excludes="sleep")

.. graphtik::
    :graphvar: weekday


.. _operation-nesting:

Nesting
^^^^^^^
Other times we want preserve all the operations composed, regardless of clashes
in their names.  This is doable with ``compose(..., nest=True))``.

Lets build a schedule for the the 3-day week (covid19 γαρ...), by combining
3 *mutated* copies of the daily schedule we built earlier:

    >>> weekdays = [weekday.withset(name=f"day {i}") for i in range(3)]
    >>> week = compose("week", *weekdays, nest=True)

.. graphtik::
    :graphvar: week

We now have 3 "isolated" clusters because all operations & data have been prefixed
with the name of their pipeline they originally belonged to.

Let's suppose we want to break the isolation, and have all sub-pipelines
consume & produce from a common "backlog" (n.b. in real life, we would have
a "feeder" & "collector" operations).

We do that by passing as the ``nest`` parameter a :func:`.callable` which will decide
which names of the original pipeline (operations & dependencies) should be prefixed
(see also :func:`.compose` & :class:`.RenArgs` for how to use that param):

    >>> def rename_predicate(ren_args):
    ...     if ren_args.name not in ("backlog", "tasks done", "todos"):
    ...         return True

    >>> week = compose("week", *weekdays, nest=rename_predicate)

.. graphtik::
    :graphvar: week

Finally we may run the week's schedule and get the outcome
(whatever that might be :-), hover the results to see them):

    >>> sol = week.compute({'backlog': "a lot!"})
    >>> sol
    {'backlog': 'a lot!',
     'day 0.tasks': 'a lot!',
     'tasks done': 'a lot', 'todos': '!',
     'day 1.tasks': 'a lot!',
     'day 2.tasks': 'a lot!'}
    >>> dot = sol.plot(clusters=True)

.. graphtik::

.. tip::
    We had to plot with ``clusters=True`` so that we prevent the :term:`plan`
    to insert the "after pruning" cluster (see :attr:`.PlotArgs.clusters`).

.. seealso::
    Consult these test-cases from the full sources of the project:

    - :file:`test/test_combine.py`


Advanced pipelines
------------------
.. _endured:

Depending on sideffects
^^^^^^^^^^^^^^^^^^^^^^^
.. autofunction:: graphtik.modifier.sfx
   :noindex:

Modifying existing values in solutions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. autofunction:: graphtik.modifier.sfxed
   :noindex:

Resilience when operations fail (*endurance*)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
It is possible for a pipeline to persist executing operations, even if some of them
are raising errors, if they are marked as :term:`endured`:

    >>> @operation(endured=1, provides=["space", "time"])
    ... def get_out():
    ...     raise ValueError("Quarantined!")
    >>> get_out
    FnOp!(name='get_out', provides=['space', 'time'], fn='get_out')

Notice the exclamation(``!``) before the parenthesis in the string representation
of the operation.

    >>> @operation(needs="space", provides="fun")
    ... def exercise(where):
    ...     return "refreshed"
    >>> @operation(endured=1, provides="time")
    ... def stay_home():
    ...     return "1h"
    >>> @operation(needs="time", provides="fun")
    ... def read_book(for_how_long):
    ...     return "relaxed"

    >>> covid19 = compose("covid19", get_out, stay_home, exercise, read_book)
    >>> covid19
    Pipeline('covid19',
                     needs=['space', 'time'],
                     provides=['space', 'time', 'fun'],
                     x4 ops: get_out, stay_home, exercise, read_book)

.. graphtik::

Notice the thick outlines of the endured (or :term:`reschedule`\d, see below) operations.

When executed, the pipeline produced :term:`outputs`, although one of its operations
has failed:

    >>> sol = covid19()
    >>> sol
    {'time': '1h', 'fun': 'relaxed'}

.. graphtik::

You may still abort on failures, later, by raising an appropriate exception from
:class:`.Solution`:

    >>> sol.scream_if_incomplete()
    Traceback (most recent call last):
    graphtik.base.IncompleteExecutionError:
    Not completed x2 operations ['exercise', 'get_out'] due to x1 failures and x0 partial-ops:
      +--get_out: ValueError('Quarantined!')


.. _rescheduled:

Operations with partial outputs (*rescheduled*)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
In case the actually produce `outputs` depend on some condition in the `inputs`,
the `solution` has to :term:`reschedule` the plan amidst execution, and consider the
actual `provides` delivered:

    >>> @operation(rescheduled=1,
    ...            needs="quarantine",
    ...            provides=["space", "time"],
    ...            returns_dict=True)
    ... def get_out_or_stay_home(quarantine):
    ...     if quarantine:
    ...          return {"time": "1h"}
    ...     else:
    ...          return {"space": "around the block"}
    >>> get_out_or_stay_home
    FnOp?(name='get_out_or_stay_home',
                         needs=['quarantine'],
                         provides=['space', 'time'],
                         fn{}='get_out_or_stay_home')
    >>> @operation(needs="space", provides=["fun", "body"])
    ... def exercise(where):
    ...     return "refreshed", "strong feet"
    >>> @operation(needs="time", provides=["fun", "brain"])
    ... def read_book(for_how_long):
    ...     return "relaxed", "popular physics"

    >>> covid19 = compose("covid19", get_out_or_stay_home, exercise, read_book)

Depending on "quarantine' state we get to execute different part of the pipeline:

.. graphtik::

    >>> sol = covid19(quarantine=True)

.. graphtik::

    >>> sol = covid19(quarantine=False)


In both case, a warning gets raised about the missing outputs, but the execution
proceeds regularly to what it is possible to evaluate.  You may collect a report of
what has been canceled using this:

    >>> print(sol.check_if_incomplete())
    Not completed x1 operations ['read_book'] due to x0 failures and x1 partial-ops:
      +--get_out_or_stay_home: ['time']

In case you wish to cancel the output of a single-result operation,
return the special value :data:`graphtik.NO_RESULT`.


.. _hierarchical-data:

Hierarchical data and further tricks
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
Working with :term:`hierarchical data` relies upon dependencies expressed as
:term:`json pointer path`\s against solution data-tree
Let's retrofit the "weekly tasks" example from :ref:`operation-nesting` section,
above.

In the previous example, we had left out the collection of the tasks
and the TODOs -- this time we're going to:

1. properly distribute & backlog the tasks to be done across the days using
   :term:`sideffected` dependencies to modify the original stack of tasks in-place,
   while the workflow is running,
2. exemplify further the use of :term:`operation nesting` & renaming, and
3. (unstable API) access the wrapping operation and :mod:`.execution` machinery
   from within the function by using :data:`.task_context`, and finally
4. store the input backlog, the work done, and the TODOs from the tasks in
   this data-tree::

       +--backlog
       +--Monday.tasks
       +--Wednesday.tasks
       +--Tuesday.tasks
       +--daily_tasks/
          +--Monday
          +--Tuesday
          +--Wednesday
       +--weekly_tasks
       +--todos

First, let's build the single day's workflow, without any nesting:

    >>> from graphtik import NO_RESULT, sfxed
    >>> from graphtik.base import RenArgs  # type hints for autocompletion.
    >>> from graphtik.execution import task_context
    >>> from graphtik.modifier import dep_renamed

    >>> todos = sfxed("backlog", "todos")
    >>> @operation(name="wake up",
    ...            needs="backlog",
    ...            provides=["tasks", todos],
    ...            rescheduled=True
    ... )
    ... def pick_tasks(backlog):
    ...     if not backlog:
    ...         return NO_RESULT
    ...     # Pick from backlog 1/3 of len-of-chars of my day-name.
    ...     n_tasks = int(len(task_context.get().op.name) / 3)
    ...     my_tasks, todos = backlog[:n_tasks], backlog[n_tasks:]
    ...     return my_tasks, todos

The actual work is emulated with a :term:`conveyor operation`:

    >>> do_tasks = operation(fn=None, name="work!", needs="tasks", provides="daily_tasks")

    >>> weekday = compose("weekday", pick_tasks, do_tasks)

Notice that the "backlog" :term:`sideffected` result of the "wakeup" operation
is also listed in its *needs*;  through this trick, each daily tasks can remove
the tasks it completed from the initial backlog of tasks, for the next day to pick up.
The "todos" sfx is just a name to denote the kind of modification performed
on the "backlog"

Note also that the tasks passed from "wake up" --> "work!" operations are not hierarchical,
but kept "private" in each day by nesting them with a dot(``.``):

.. graphtik::

Now let's clone the daily-task x3 and *nest* it, to make a 3-day workflow:

    >>> days = ["Monday", "Tuesday", "Wednesday"]
    >>> weekdays = [weekday.withset(name=d) for d in days]
    >>> def nester(ra: RenArgs):
    ...     dep = ra.name
    ...     if ra.typ == "op":
    ...         return True  # Nest by.dot.
    ...     if ra.typ.endswith(".jsonpart"):
    ...         return False  # Don't touch the json-pointer parts.
    ...     if dep == "tasks":
    ...         return True  # Nest by.dot
    ...     if dep == "daily_tasks":
    ...         # Nest as subdoc.
    ...         return dep_renamed(dep, lambda n: f"{n}/{ra.parent.name}")
    ...     return False

    >>> week = compose("week", *weekdays, nest=nester)

And this is now the pipeline for a 3 day-week.
Notice the ``tasks-done/{day}`` subdoc nodes at the bottom of the diagram:

.. graphtik::

Finally combine all weekly-work using a "collector" operation:

    >>> from graphtik import vararg
    >>> @operation(
    ...     name="collect tasks",
    ...     needs=[todos, *(vararg(f"daily_tasks/{d}") for d in days)],
    ...     provides=["weekly_tasks", "todos"],
    ... )
    ... def collector(backlog, *daily_tasks):
    ...     return daily_tasks or (), backlog or ()
    ...
    >>> week = compose("week", week, collector)

This is the final week pipeline:

.. graphtik::

We can now feed the week pipeline with a "workload" of 17 imaginary tasks.
We know from each "wake up" operation that *Monday*, *Tuesday* & *Wednesday* will pick
4, 5 & 5 tasks respectively, leaving 3 tasks as "todo":

    >>> sol = week.compute({"backlog": range(17)})
    >>> sol
    {'backlog': range(14, 17),
     'Monday.tasks': range(0, 4),
     'daily_tasks': {'Monday': range(0, 4),
                    'Tuesday': range(4, 9),
                    'Wednesday': range(9, 14)},
     'Tuesday.tasks': range(4, 9),
     'Wednesday.tasks': range(9, 14),
     'weekly_tasks': (range(0, 4), range(4, 9), range(9, 14)),
     'todos': range(14, 17)}

.. graphtik::

Or we can reduce the workload, and see that *Wednesday* is left without any work
to do:

    >>> sol = week.compute(
    ...     solution={"backlog": range(9)},
    ...     outputs=["daily_tasks", "weekly_tasks", "todos"])

.. graphtik::

Hover over the data nodes to see the results.  Specifically check the "daily_tasks"
which is a nested dictionary:

    >>> sol
    {'daily_tasks': {'Monday': range(0, 4),
                    'Tuesday': range(4, 9)},
     'weekly_tasks': (range(0, 4), range(4, 9)),
     'todos': ()}
