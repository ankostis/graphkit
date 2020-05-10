1. `graphtik` with :graphvar:
=============================
.. graphtik::
    :graphvar: pipeline1

    >>> from graphtik import compose, operation

    >>> pipeline1 = compose(
    ...     "pipeline1",
    ...     operation(name="op1", needs=["a", "b"], provides="aa")(lambda a, b: [a, b]),
    ... )


2. Solved `graphtik` WITHOUT :graphvar:
=======================================
.. graphtik::
    :caption: Solved *pipeline2* with ``a=1``, ``b=2``

    >>> pipeline2 = compose(
    ...     "pipeline2",
    ...     operation(name="op1", needs=["a", "b"], provides="aa")(lambda a, b: [a, b]),
    ... )
    >>> sol = pipeline2(a=1, b=2)


3. `graphtik` inherit from literal-block WITHOUT :graphvar:
===========================================================

>>> pipeline3 = compose(
...     "pipeline3",
...     operation(name="op1", needs=["a", "b"], provides="aa")(lambda a, b: [a, b]),
... )

.. graphtik::


4. `graphtik` inherit from doctest-block with :graphvar:
========================================================

>>> pipeline4 = compose(
...     "pipeline4",
...     operation(name="op1", needs=["a", "b"], provides="aa")(lambda a, b: [a, b]),
... )

.. graphtik::
    :graphvar: pipeline4


5. Image for :hide:
===================
.. graphtik::
    :graphvar: pipeline1
    :hide:
    :zoomable: false


6. Nothing for :skipif:
=======================
.. graphtik::
    :graphvar: pipeline1
    :skipif: True



7. Same name, different graph
=============================
.. graphtik::
    :zoomable:

    >>> pipeline5 = compose(
    ...     "pipeline1",
    ...     operation(name="op1", needs="a", provides="aa")(lambda a: a),
    ...     operation(name="op2", needs=["aa", "b"], provides="res")(lambda x, y: [x, y]),
    ... )


8. Multiple plottables with prexistent
======================================
Check order of doctest-globals even if item pre-exists:

.. graphtik::
    :zoomable-opts: {}

    >>> from graphtik import compose, operation
    >>> pipeline1 = compose(
    ...     "pipeline1",
    ...     operation(name="op1", needs=["a", "b"], provides="aa")(lambda a, b: [a, b])
    ... )
    >>> pipeline2 = compose(
    ...     "pipeline2",
    ...     operation(name="op1", needs=["a", "b"], provides="aa")(lambda a, b: [a, b])
    ... )


9. Multiple plottables ignoring 1st
===================================
.. graphtik::

    >>> from graphtik import compose, operation
    >>> pipeline1 = compose(
    ...     "pipelineA",
    ...     operation(name="op1", needs=["A", "b"], provides="aa")(lambda a, b: [a, b])
    ... )

    >>> pipeline2 = compose(
    ...     "pipelineB",
    ...     operation(name="op1", needs=["a", "B"], provides="aa")(lambda a, b: [a, b])
    ... )
