Operations
==========

An :term:`operation` is a function in a computation :term:`pipeline`,
abstractly represented by the :class:`.Operation` class.
This class specifies the :term:`dependencies <dependency>` forming the *pipeline*'s
:term:`network`.


Defining Operations
-------------------
You may inherit the :class:`.Operation` abstract class and override its
:meth:`.Operation.compute()` method to manually do the following:

- read declared as :term:`needs` values from :term:`solution`,
- match those values into function arguments,
- call your function to do it's business,
- "zip" the function's results with the operation's declared :term:`provides`,
  and finally
- hand back those zipped values to *solution* for further actions.

But there is an easier way -- actually half of the code in this project is dedicated
to retrofitting existing *functions* unaware of all these, into :term:`operation`\s.

Operations from existing functions
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
The :class:`.FunctionalOperation` provides a concrete lightweight wrapper
around any arbitrary function to define and execute within a *pipeline*.
Use the :func:`.operation()` factory to instantiate one:

   >>> from operator import add
   >>> from graphtik import operation

   >>> add_op = operation(add,
   ...                    needs=['a', 'b'],
   ...                    provides=['a_plus_b'])
   >>> add_op
   FunctionalOperation(name='add', needs=['a', 'b'], provides=['a_plus_b'], fn='add')

You may still call the original function at :attr:`.FunctionalOperation.fn`,
bypassing thus any operation pre-processing:

      >>> add_op.fn(3, 4)
      7

But the proper way is to *call the operation* (either directly or by calling the
:meth:`.FunctionalOperation.compute()` method).  Notice though that unnamed
positional parameters are not supported:

      >>> add_op(a=3, b=4)
      {'a_plus_b': 7}

.. tip::
   In case your function needs to access the :mod:`.execution` machinery
   or its wrapping operation, it can do that through the :data:`.task_context`
   (unstable API, not working during :term:`parallel execution`, see :ref:`task-context`)


Builder pattern
^^^^^^^^^^^^^^^
There are two ways to instantiate a :class:`.FunctionalOperation`\s, each one suitable
for different scenarios.

We've seen that calling manually :func:`.operation()` allows putting into a pipeline
functions that are defined elsewhere (e.g. in another module, or are system functions).

But that method is also useful if you want to create multiple operation instances
with similar attributes, e.g. ``needs``:

   >>> op_factory = operation(needs=['a'])

Notice that we specified a `fn`, in order to get back a :class:`.FunctionalOperation`
instance (and not a decorator).

   >>> from graphtik import operation, compose
   >>> from functools import partial

   >>> def mypow(a, p=2):
   ...    return a ** p

   >>> pow_op2 = op_factory.withset(fn=mypow, provides="^2")
   >>> pow_op3 = op_factory.withset(fn=partial(mypow, p=3), name='pow_3', provides='^3')
   >>> pow_op0 = op_factory.withset(fn=lambda a: 1, name='pow_0', provides='^0')

   >>> graphop = compose('powers', pow_op2, pow_op3, pow_op0)
   >>> graphop
   Pipeline('powers', needs=['a'], provides=['^2', '^3', '^0'], x3 ops:
      mypow, pow_3, pow_0)


   >>> graphop(a=2)
   {'a': 2, '^2': 4, '^3': 8, '^0': 1}

.. graphtik::
.. Tip::
  See :ref:`plotting` on how to make diagrams like this.


Decorator specification
^^^^^^^^^^^^^^^^^^^^^^^

If you are defining your computation graph and the functions that comprise it all in the same script,
the decorator specification of ``operation`` instances might be particularly useful,
as it allows you to assign computation graph structure to functions as they are defined.
Here's an example:

   >>> from graphtik import operation, compose

   >>> @operation(needs=['b', 'a', 'r'], provides='bar')
   ... def foo(a, b, c):
   ...   return c * (a + b)

   >>> graphop = compose('foo_graph', foo)

.. graphtik::

- Notice that if ``name`` is not given, it is deduced from the function name.


Specifying graph structure: ``provides`` and ``needs``
------------------------------------------------------
Each :term:`operation` is a node in a computation :term:`graph`,
depending and supplying data from and to other nodes (via the :term:`solution`),
in order to :term:`compute`.

This graph structure is specified (mostly) via the ``provides`` and ``needs`` arguments
to the :func:`.operation` factory, specifically:

``needs``
   this argument names the list of (positionally ordered) :term:`inputs` data the `operation`
   requires to receive from *solution*.
   The list corresponds, roughly, to the arguments of the underlying function
   (plus any :term:`sideffects`).

   It can be a single string, in which case a 1-element iterable is assumed.

   :seealso: :term:`needs`, :term:`modifier`, :attr:`.FunctionalOperation.needs`,
      :attr:`.FunctionalOperation.op_needs`, :attr:`.FunctionalOperation._fn_needs`

``provides``
   this argument names the list of (positionally ordered) :term:`outputs` data
   the operation provides into the *solution*.
   The list corresponds, roughly, to the returned values of the `fn`
   (plus any :term:`sideffects` & :term:`alias`\es).

   It can be a single string, in which case a 1-element iterable is assumed.

   If they are more than one, the underlying function must return an iterable
   with same number of elements (unless it :term:`returns dictionary`).

   :seealso: :term:`provides`, :term:`modifier`, :attr:`.FunctionalOperation.provides`,
      :attr:`.FunctionalOperation.op_provides`, :attr:`.FunctionalOperation._fn_provides`

Declarations of *needs* and *provides* is affected by :term:`modifier`\s like
:func:`.keyword`:

Map inputs to different function arguments
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. autofunction:: graphtik.modifiers.keyword
   :noindex:

Operations may execute with missing inputs
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. autofunction:: graphtik.modifiers.optional
   :noindex:

Calling functions with varargs (``*args``)
^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^
.. autofunction:: graphtik.modifiers.vararg
   :noindex:
.. autofunction:: graphtik.modifiers.varargs
   :noindex:


.. _aliases:

Aliased `provides`
^^^^^^^^^^^^^^^^^^
Sometimes, you need to interface functions & operations where they name a
:term:`dependency` differently.
This is doable without introducing "pipe-through" interface operation, either
by annotating certain `needs` with :func:`.keyword` `modifiers` (above), or
by :term:`alias`\sing certain `provides` to different names:

   >>> op = operation(str,
   ...                name="`provides` with `aliases`",
   ...                needs="anything",
   ...                provides="real thing",
   ...                aliases=("real thing", "phony"))

.. graphtik::


.. _conveyor-function:

Default conveyor operation
^^^^^^^^^^^^^^^^^^^^^^^^^^
If you don't specify a callable, the :term:`default identity function` get assigned,
as long a `name` for the operation is given, and the number of `needs` matches
the number of `provides`.

This facilitates conveying :term:`inputs` into renamed :term:`outputs` without the need
to define a trivial *identity function* matching the `needs` & `provides` each time:

    >>> from graphtik import keyword, optional, vararg
    >>> op = operation(
    ...     name="a",
    ...     needs=[optional("opt"), vararg("vararg"), "pos", keyword("kw")],
    ...     # positional vararg, keyword, optional
    ...     provides=["pos", "vararg", "kw", "opt"],
    ... )()
    >>> op(opt=5, vararg=6, pos=7, kw=8)
    {'pos': 7, 'vararg': 6, 'kw': 5, 'opt': 8}

Notice that the order of the results is not that of the `needs`
(or that of the `inputs` in the ``compute()`` method), but, as explained in the comment-line,
it follows Python semantics.


Considerations for when building pipelines
------------------------------------------
When many operations are composed into a computation graph, Graphtik matches up
the values in their *needs* and *provides* to form the edges of that graph
(see :ref:`graph-composition` for more on that), like the operations from the script
in :ref:`quick-start`:

   >>> from operator import mul, sub
   >>> from functools import partial
   >>> from graphtik import compose, operation

   >>> def abspow(a, p):
   ...   """Compute |a|^p. """
   ...   c = abs(a) ** p
   ...   return c

   >>> # Compose the mul, sub, and abspow operations into a computation graph.
   >>> graphop = compose("graphop",
   ...    operation(mul, needs=["a", "b"], provides=["ab"]),
   ...    operation(sub, needs=["a", "ab"], provides=["a_minus_ab"]),
   ...    operation(name="abspow1", needs=["a_minus_ab"], provides=["abs_a_minus_ab_cubed"])
   ...    (partial(abspow, p=3))
   ... )
   >>> graphop
   Pipeline('graphop',
                    needs=['a', 'b', 'ab', 'a_minus_ab'],
                    provides=['ab', 'a_minus_ab', 'abs_a_minus_ab_cubed'],
                    x3 ops: mul, sub, abspow1)


- Notice the use of :func:`functools.partial()` to set parameter ``p`` to a constant value.
- And this is done by calling once more the returned "decorator* from :func:`operation()`,
  when called without a functions.

The ``needs`` and ``provides`` arguments to the operations in this script define
a computation graph that looks like this:

.. graphtik::
