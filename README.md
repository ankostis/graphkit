# Graphtik

![Supported Python versions of latest release in PyPi](https://img.shields.io/pypi/pyversions/graphtik.svg?label=Python)
![Latest version in PyPI](https://img.shields.io/pypi/v/graphtik.svg?label=PyPi%20version)
![Latest version in GitHub](https://img.shields.io/github/v/release/pygraphkit/graphtik.svg?label=GitHub%20release&include_prereleases)
![Build Status](https://travis-ci.org/pygraphkit/graphtik.svg?branch=master)
![Doc Status](https://img.shields.io/readthedocs/graphtik.svg?branch=master)
![codecov](https://codecov.io/gh/pygraphkit/graphtik/branch/master/graph/badge.svg)
![License](https://img.shields.io/pypi/l/graphtik.svg)

![Github watchers](https://img.shields.io/github/watchers/pygraphkit/graphtik.svg?style=social)
![Github stargazers](https://img.shields.io/github/stars/pygraphkit/graphtik.svg?style=social)
![Github forks](https://img.shields.io/github/forks/pygraphkit/graphtik.svg?style=social)
![Issues count](http://img.shields.io/github/issues/pygraphkit/graphtik.svg?style=social)

> It's a DAG all the way down

<img src="docs/source/images/barebone_2ops.svg" width=100 alt="simple graphtik computation">

## Lightweight computation graphs for Python

**Graphtik** is an an understandable and lightweight Python module for building and running
ordered graphs of computations.
The API posits a fair compromise between features and complexity without precluding any.
It might be of use in computer vision, machine learning and other data science domains,
or become the core of a custom ETL pipelne.

*Graphtik* is a temporary fork of [*Graphkit*](https://github.com/yahoo/graphkit)
to experiment with Python 3.6+ features.


## Quick start

Here's how to install:

    pip install graphtik

OR with dependencies for plotting support (and you need to install [`Graphviz`](https://graphviz.org)
program separately with your OS tools):

    pip install graphtik[plot]

Here's a Python script with an example Graphtik computation graph that produces
multiple outputs (`a * b`, `a - a * b`, and `abs(a - a * b) ** 3`):

    >>> from operator import mul, sub
    >>> from graphtik import compose, operation

    >>> # Computes |a|^p.
    >>> def abspow(a, p):
    ...     c = abs(a) ** p
    ...     return c

    >>> # Compose the mul, sub, and abspow operations into a computation graph.
    >>> graphop = compose(name="graphop")(
    ...     operation(name="mul1", needs=["a", "b"], provides=["ab"])(mul),
    ...     operation(name="sub1", needs=["a", "ab"], provides=["a_minus_ab"])(sub),
    ...     operation(name="abspow1", needs=["a_minus_ab"], provides=["abs_a_minus_ab_cubed"], params={"p": 3})(abspow)
    ... )

<img src="docs/source/images/barebone_3ops.svg" width=100
alt="simple graphtik computation">

    >>> # Run the graph and request all of the outputs.
    >>> out = graphop({'a': 2, 'b': 5})
    >>> print(out)
    {'a': 2, 'b': 5, 'ab': 10, 'a_minus_ab': -8, 'abs_a_minus_ab_cubed': 512}

    >>> # Run the graph and request a subset of the outputs.
    >>> out = graphop({'a': 2, 'b': 5}, outputs=["a_minus_ab"])
    >>> print(out)
    {'a_minus_ab': -8}

<img src="docs/source/images/executed_3ops.svg" width=120
 alt="simple graphtik computation">

As you can see, any function can be used as an operation in Graphtik, even ones imported from system modules!


## Plotting

For debugging the above graph-operation you may plot either the newly omposed graph or the *execution plan* of the last computation executed,
using these methods:

```python
graphop.plot(show=True)                # open a matplotlib window
graphop.plot("graphop.svg")            # other supported formats: png, jpg, pdf, ...
graphop.plot()                         # without arguments return a pydot.DOT object
graphop.plot(solution=out)             # annotate graph with solution values
```

![Graphtik Legend](docs/source/images/GraphtikLegend.svg "Graphtik Legend")

> **TIP:** The `pydot.Dot` instances returned by `plot()` are rendered as SVG in *Jupyter/IPython*.

# License

Code licensed under the Apache License, Version 2.0 license. See LICENSE file for terms.
