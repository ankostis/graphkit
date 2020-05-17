#!/usr/bin/env python
# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
import io
import os
import re
import datetime as dt
import subprocess as sbp
from setuptools import setup, find_packages


def _ask_git_version(default: str) -> str:
    try:
        return sbp.check_output(
            "git describe --always".split(), universal_newlines=True
        ).strip()
    except Exception:
        return default


git_ver = _ask_git_version("x.x.x")

with open("README.rst") as f:
    long_description = (
        f.read()
        .replace("|version|", git_ver)
        .replace("|release|", git_ver)
        .replace("|today|", dt.datetime.now().isoformat())
    )

# Grab the version using convention described by flask
# https://github.com/pallets/flask/blob/master/setup.py#L10
with io.open("graphtik/__init__.py", "rt", encoding="utf8") as f:
    version = re.search(r'__version__ = "(.*?)"', f.read()).group(1)

plot_deps = ["pydot", "jinja2", "importlib_resources; python_version<'3.7'"]
matplot_deps = plot_deps + ["matplotlib"]
sphinx_deps = plot_deps + ["sphinx >=2"]
test_deps = list(
    set(
        matplot_deps
        + sphinx_deps
        + [
            "pytest",
            "pytest-cov",
            "pytest-sphinx",
            "dill",
            "sphinxcontrib-spelling",
            "html5lib",  # for sphinxext TCs
            "readme-renderer",  # for PyPi landing-page
            "pandas",
        ]
    )
)
dev_deps = test_deps + ["black", "pylint", "mypy"]

setup(
    name="graphtik",
    version=version,
    description="A lightweight Python-3.6+ lib for solving & executing graphs of functions",
    long_description=long_description,
    long_description_content_type="text/x-rst",
    author="Kostis Anagnostopoulos, Huy Nguyen, Arel Cordero, Pierre Garrigues, Rob Hess, "
    "Tobi Baumgartner, Clayton Mellina",
    author_email="ankostis@gmail.com",
    url="http://github.com/pygraphkit/graphtik",
    project_urls={
        "Documentation": "https://graphtik.readthedocs.io/",
        "Release Notes": "https://graphtik.readthedocs.io/en/latest/changes.html",
        "Sources": "https://github.com/pygraphkit/graphtik",
        "Bug Tracker": "https://github.com/pygraphkit/graphtik/issues",
    },
    packages=find_packages(exclude=["test"]),
    package_data={"graphtik.sphinxext": ["*.css"]},
    python_requires=">=3.6",
    install_requires=[
        "contextvars; python_version < '3.7'",
        "networkx; python_version >= '3.5'",
        "networkx == 2.2; python_version < '3.5'",
        "boltons",  # for IndexSet
    ],
    extras_require={
        ## NOTE: update also "extras" in README/quick-start section .
        "plot": plot_deps,
        "matplot": matplot_deps,
        "sphinx": sphinx_deps,
        "test": test_deps,
        # May help for pickling `parallel` tasks.
        # See :term:`marshalling` and :func:`set_marshal_tasks()` configuration.
        "dill": ["dill"],
        "all": dev_deps,
        "dev": dev_deps,
    },
    tests_require=test_deps,
    license="Apache-2.0",
    keywords=[
        "graph",
        "computation graph",
        "DAG",
        "directed acyclic graph",
        "executor",
        "scheduler",
        "etl",
        "workflow",
        "pipeline",
    ],
    classifiers=[
        "Development Status :: 4 - Beta",
        "License :: OSI Approved :: Apache Software License",
        "Intended Audience :: Developers",
        "Intended Audience :: Science/Research",
        "Natural Language :: English",
        "Operating System :: MacOS :: MacOS X",
        "Operating System :: Microsoft :: Windows",
        "Operating System :: OS Independent",
        "Operating System :: POSIX",
        "Operating System :: Unix",
        "Programming Language :: Python",
        "Programming Language :: Python :: 3.6",
        "Programming Language :: Python :: 3.7",
        "Programming Language :: Python :: 3.8",
        "Topic :: Software Development :: Libraries",
        "Topic :: Software Development :: Libraries :: Python Modules",
        "Topic :: Scientific/Engineering",
        "Topic :: Software Development",
    ],
    zip_safe=True,
    platforms="Windows,Linux,Solaris,Mac OS-X,Unix",
)
