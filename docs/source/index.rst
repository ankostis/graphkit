
========
Graphtik
========

|python-ver| |gh-version| |pypi-version| |travis-status| |doc-status| |cover-status|
|dev-status| |downloads-count| |codestyle| |proj-lic|

|gh-watch| |gh-star| |gh-fork| |gh-issues|

**It's a DAG all the way down!** |sample-plot|

Lightweight computation graphs for Python
-----------------------------------------

**Graphtik** is an an understandable and lightweight Python module for building and running
ordered graphs of computations.
The API posits a fair compromise between features and complexity without precluding any.
It might be of use in computer vision, machine learning and other data science domains,
or become the core of a custom ETL pipelne.

*Graphtik* is a temporary fork of `Graphkit <https://github.com/yahoo/graphkit>`_
to experiment with Python 3.6+ features.

.. toctree::
   :maxdepth: 3
   :numbered:

   operations
   composition
   plotting
   reference
   changes


.. _quick-start:

Quick start
-----------

Here's how to install::

   pip install graphtik

OR with dependencies for plotting support (and you need to install `Graphviz
<https://graphviz.org>`_ program separately with your OS tools)::

   pip install graphtik[plot]


Here's a Python script with an example Graphtik computation graph that produces multiple outputs (``a * b``, ``a - a * b``, and ``abs(a - a * b) ** 3``)::

   >>> from operator import mul, sub
   >>> from graphtik import compose, operation

   # Computes |a|^p.
   >>> def abspow(a, p):
   ...    c = abs(a) ** p
   ...    return c

   >>> # Compose the mul, sub, and abspow operations into a computation graph.
   >>> graphop = compose(name="graphop")(
   ...    operation(name="mul1", needs=["a", "b"], provides=["ab"])(mul),
   ...    operation(name="sub1", needs=["a", "ab"], provides=["a_minus_ab"])(sub),
   ...    operation(name="abspow1", needs=["a_minus_ab"], provides=["abs_a_minus_ab_cubed"], params={"p": 3})(abspow)
   ... )

   >>> # Run the graph-operation and request all of the outputs.
   >>> graphop({'a': 2, 'b': 5})
   {'a': 2, 'b': 5, 'ab': 10, 'a_minus_ab': -8, 'abs_a_minus_ab_cubed': 512}

   >>> # Run the graph-operation and request a subset of the outputs.
   >>> graphop({'a': 2, 'b': 5}, outputs=["a_minus_ab"])
   {'a_minus_ab': -8}

As you can see, any function can be used as an operation in Graphtik,
even ones imported from system modules!


License
-------

Code licensed under the Apache License, Version 2.0 license. See LICENSE file for terms.


.. |travis-status| image:: https://travis-ci.org/pygraphkit/graphtik.svg
    :alt: Travis continuous integration testing ok? (Linux)
    :target: https://travis-ci.org/pygraphkit/graphtik/builds

.. |doc-status| image:: https://img.shields.io/readthedocs/graphtik.svg?branch=master
    :alt: ReadTheDocs ok?
    :target: https://graphtik.readthedocs.org

.. |cover-status| image:: https://codecov.io/gh/pygraphkit/graphtik/branch/master/graph/badge.svg
    :target: https://codecov.io/gh/pygraphkit/graphtik

.. |gh-version| image::  https://img.shields.io/github/v/release/pygraphkit/graphtik.svg?label=GitHub%20release&include_prereleases
    :target: https://github.com/pygraphkit/graphtik/releases
    :alt: Latest version in GitHub

.. |pypi-version| image::  https://img.shields.io/pypi/v/graphtik.svg?label=PyPi%20version
    :target: https://pypi.python.org/pypi/graphtik/
    :alt: Latest version in PyPI

.. |python-ver| image:: https://img.shields.io/pypi/pyversions/graphtik.svg?label=Python
    :target: https://pypi.python.org/pypi/graphtik/
    :alt: Supported Python versions of latest release in PyPi

.. |dev-status| image:: https://pypip.in/status/graphtik/badge.svg
    :target: https://pypi.python.org/pypi/graphtik/
    :alt: Development Status

.. |downloads-count| image:: https://pypip.in/download/graphtik/badge.svg?period=month&label=PyPi%20downloads
    :target: https://pypi.python.org/pypi/graphtik/
    :alt: PyPi downloads

.. |codestyle| image:: https://img.shields.io/badge/code%20style-black-black.svg
    :target: https://github.com/ambv/black
    :alt: Code Style

.. |gh-watch| image:: https://img.shields.io/github/watchers/pygraphkit/graphtik.svg?style=social
    :target: https://github.com/pygraphkit/graphtik
    :alt: Github watchers

.. |gh-star| image:: https://img.shields.io/github/stars/pygraphkit/graphtik.svg?style=social
    :target: https://github.com/pygraphkit/graphtik
    :alt: Github stargazers

.. |gh-fork| image:: https://img.shields.io/github/forks/pygraphkit/graphtik.svg?style=social
    :target: https://github.com/pygraphkit/graphtik
    :alt: Github forks

.. |gh-issues| image:: http://img.shields.io/github/issues/pygraphkit/graphtik.svg?style=social
    :target: https://github.com/pygraphkit/graphtik/issues
    :alt: Issues count

.. |proj-lic| image:: https://img.shields.io/pypi/l/graphtik.svg
    :target:  https://www.apache.org/licenses/LICENSE-2.0
    :alt: Apache License, version 2.0

.. |sample-plot| image:: images/barebone_2ops.svg
    :alt: sample graphtik plot
    :width: 120px
    :align: middle
