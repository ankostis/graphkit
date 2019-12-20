##################
Graphtik Changelog
##################

TODOs
%%%%%
See :gg:`1`.

GitHub Releases
%%%%%%%%%%%%%%%

https://github.com/pygraphkit/graphtik/releases

Changelog
%%%%%%%%%

v4.4.0 (18 Dec 2019, @ankostis): RESCHEDULE for PARTIAL Outputs, on a per op basis
==================================================================================
+ FIX(NET): keep Solution's insertion order also for PARALLEL executions.

+ FIX(NET): use an :class:`.RLock` for spurious errors while operations update
  the various :class:`Solution` collections concurrently
  (manifested as concurrent loop modifications).

+ FEAT(NET, OP): :term:`reschedule` operations with partial outputs;
  they must have :attr:`FunctionalOperation.reschedule` set to true,
  or else they will fail.

+ FEAT(OP, netop): specify :term:`endurance`/`reschedule` on a per operation basis,
  or collectively for all operations grouped under some :term:`netop`.

+ REFACT(NETOP):

  + feat(netop): new method :meth:`.NetworkOperation.compile()`, delegating to
    same-named method of `network`.

  + drop(net): method ``Net.narrowed()``; remember `netop.narrowed(outputs+predicate)`
    and apply them on `netop.compute()` & ``netop.compile()``.

    - PROS: cache narrowed plans.
    - CONS: cannot review network, must review plan of (new) `netop.compile()`.

  + drop(netop): `inputs` args in `narrowed()` didn't make much sense,
    leftover from "unvarying netops";  but existi ni `netop.compile()`.

  + refact(netop): move net-assembly from compose() --> NetOp cstor;
    now reschedule/endured/merge/method args in cstor.

+ fix(build): PyPi README check did not detect forbidden ``raw`` directives,
  and travis auto-depoyments were failing.

+ doc(arch): more terms.


v4.3.0 (16 Dec 2019, @ankostis): Aliases
========================================
+ FEAT(OP): support "aliases" of `provides`, to avoid trivial pipe-through operations,
  just to rename & match operations.


v4.2.0 (16 Dec 2019, @ankostis): ENDURED Execution
==================================================
+ FEAT(NET): when :func:`.set_endure_execution` configuration is set to true,
  a :term:`netop` will keep on calculating solution, skipping any operations
  downstream from failed ones.  The :term:`solution` eventually collects all failures
  in :attr:`.Solution.failures`.

+ ENH(DOC,plot): Links in Legend and :ref:`arch` Workflow SVGs now work,
  and delegate to *architecture* terms.

+ ENH(plot): mark :term:`overwrites`, *failed* & *canceled* in ``repr()``
  (see :term:`endurance`).

+ refact(conf): fully rename confguration opetion ``skip_evictions``.

+ REFACT(jetsam): raise after jetsam in situ, better for Readers & Linters.

+ enh(net): improve logging.


v4.1.0 (13  Dec 2019, @ankostis): ChainMap Solution for Rewrites, stable TOPOLOGICAL sort
=========================================================================================
|v410-flowchart|

+ FIX(NET): TOPOLOGICALLY-sort now break ties respecting operations insertion order.

+ ENH(NET): new :class:`.Solution` class to collect all computation values,
  based on a :class:`collections.ChainMap` to distinguish outputs per operation executed:

  + ENH(NETOP): ``compute()`` return :class:`.Solution`, consolidating:

    + :term:`overwrites`,
    + ``executed`` operations, and
    + the generating :term:`plan`.

  + drop(net): ``_PinInstruction`` class is not needed.
  + drop(netop): `overwrites_collector` parameter; now in :meth:`.Solution.overwrites()`.
  + ENH(plot): ``Solution`` is also a :class:`.Plotter`;  e.g. use ``sol.plot(...)```.

+ DROP(plot): `executed` arg from plotting; now embeded in `solution`.

+ ENH(PLOT.jupyter,doc): allow to set jupyter graph-styling selectively;
  fix instructions for jupyter cell-resizing.

+ fix(plan): time-keeping worked only for sequential execution, not parallel.
  Refactor it to happen centrally.

+ enh(NET,.TC): Add PREDICATE argument also for ``compile()``.

+ FEAT(DOC): add GLOSSARY as new :ref:`arch` section, linked from API HEADERS.



v4.0.1 (12 Dec 2019, @ankostis): bugfix
=======================================
+ FIX(plan): ``plan.repr()`` was failing on empty plans.
+ fix(site): minor badge fix & landing diagram.


v4.0.0 (11 Dec 2019, @ankostis): NESTED merge, revert v3.x Unvarying, immutable OPs, "color" nodes
==================================================================================================
+ BREAK/ENH(NETOP): MERGE NESTED NetOps by collecting all their operations
  in a single Network;  now children netops are not pruned in case
  some of their `needs` are unsatisfied.

  + feat(op): support multiple nesting under other netops.

+ BREAK(NETOP): REVERT Unvarying NetOps+base-plan, and narrow Networks instead;
  netops were too rigid, code was cumbersome, and could not really pinpoint
  the narrowed `needs` always correctly (e.g. when they were also `provides`).

  + A `netop` always narrows its `net` based on given `inputs/outputs`.
    This means that the `net` might be a subset of the one constructed out of
    the given operations.  If you want all nodes, don't specify `needs/provides`.
  + drop 3 :class:`.ExecutionPlan` attributes: ``plan, needs, plan``
  + drop `recompile` flag in ``Network.compute()``.
  + feat(net): new method :meth:`.Network.narrowed()` clones and narrows.
  + ``Network()`` cstor accepts a (cloned) graph to support ``narrowed()`` methods.

+ BREAK/REFACT(OP): simplify hierarchy, make :class:`.Operation` fully abstract,
  without name or requirements.

  + enh: make :class:`.FunctionalOperation` IMMUTABLE, by inheriting
    from class:`.namedtuple`.

+ refact(net): consider as netop `needs` also intermediate data nodes.

+ FEAT(:gg:`1`, net, netop): support prunning based on arbitrary operation attributes
  (e.g. assign "colors" to nodes and solve a subset each time).

+ enh(netop): ``repr()`` now counts number of contained operations.

+ refact(netop): rename ``netop.narrow() --> narrowed()``

+ drop(netop): don't topologically-sort sub-networks before merging them;
  might change some results, but gives controll back to the user to define nets.


v3.1.0 (6 Dec 2019, @ankostis): cooler ``prune()``
==================================================
+ break/refact(NET): scream on ``plan.execute()`` (not ``net.prune()``)
  so as calmly solve `needs` vs `provides`, based on the given `inputs`/`outputs`.
+ FIX(ot): was failing when plotting graphs with ops without `fn` set.
+ enh(net): minor fixes on assertions.


v3.0.0 (2 Dec 2019, @ankostis):  UNVARYING NetOperations, narrowed, API refact
===============================================================================
+ NetworkOperations:

  + BREAK(NET): RAISE if the graph is UNSOLVABLE for the given `needs` & `provides`!
    (see "raises" list of :meth:`~.NetworkOperation.compute()`).

  + BREAK: :meth:`.NetworkOperation.__call__()` accepts solution as keyword-args,
    to mimic API of :meth:`Operation.__call__()`.  ``outputs`` keyword has been dropped.

    .. Tip::
        Use :meth:`.NetworkOperation.compute()` when you ask different `outputs`,
        or set the ``recompile`` flag if just different `inputs` are given.

        Read the next change-items for the new behavior of the ``compute()`` method.

  + UNVARYING NetOperations:

    + BREAK: calling method :meth:`.NetworkOperation.compute()` with a single argument
      is now *UNVARYING*, meaning that all `needs` are demaned, and hence,
      all `provides` are produced, unless the ``recompile`` flag is true or ``outputs`` asked.

    + BREAK: net-operations behave like regular operations when nested inside another netop,
      and always produce all their `provides`, or scream if less `inputs` than `needs`
      are given.

    + ENH: a newly created or cloned netop can be :meth:`~.NetworkOperation.narrowed()`
      to specific `needs` & `provides`, so as not needing to pass `outputs` on every call
      to :meth:`~.NetworkOperation.compute()`.

    + feat: implemented based on the new "narrowed" :attr:`.NetworkOperation.plan` attribute.

  + FIX: netop `needs` are not all *optional* by default; optionality applied
    only if all underlying operations have a certain need as optional.

  + FEAT: support function ``**args`` with 2 new modifiers :class:`.vararg` & :class:`.varargs`,
    acting like :class:`.optional` (but without feeding into underlying functions
    like keywords).

  + BREAK(:gh:`12`): simplify ``compose`` API by turning it from class --> function;
    all args and operations are now given in a single ``compose()`` call.

  + REFACT(net, netop): make Network IMMUTABLE by appending all operations together,
    in :class:`NetworkOperation` constructor.

  + ENH(net): public-size ``_prune_graph()`` --> :meth:`.Network.prune()``
    which can be used to interogate `needs` & `provides` for a given graph.
    It accepts `None` `inputs` & `outputs` to auto-derrive them.

+ FIX(SITE): autodocs `API` chapter were not generated in at all,
  due to import errors, fixed by using `autodoc_mock_imports
  <http://www.sphinx-doc.org/en/master/usage/extensions/autodoc.html#confval-autodoc_mock_imports>`_
  on `networkx`, `pydot` & `boltons` libs.

+ enh(op): polite error-,msg when calling an operation with missing needs
  (instead of an abrupt ``KeyError``).

+ FEAT(CI): test also on Python-3.8


v2.3.0 (24 Nov 2019, @ankostis): Zoomable SVGs & more op jobs
=============================================================
+ FEAT(plot): render Zoomable SVGs in jupyter(lab) notebooks.
+ break(netop): rename execution-method ``"sequential" --> None``.
+ break(netop): move ``overwrites_collector`` & ``method`` args
  from ``netop.__call__()`` --> cstor
+ refact(netop): convert remaining ``**kwargs`` into named args, tighten up API.


v2.2.0 (20 Nov 2019, @ankostis): enhance OPERATIONS & restruct their modules
============================================================================
+ REFACT(src): split module ``nodes.py`` --> ``op.py`` + `netop.py` and
  move :class:`Operation` from ``base.py`` --> ``op.py``, in order to break cycle
  of `base(op) <-- net <-- netop`, and keep utils only in `base.py`.
+ ENH(op): allow Operations WITHOUT any NEEDS.
+ ENH(op): allow Operation FUNCTIONS to return directly Dictionaries.
+ ENH(op): validate function Results against operation `provides`;
  *jetsam* now includes `results` variables: ``results_fn`` & ``results_op``.
+ BREAK(op): drop unused `Operation._after_init()` pickle-hook; use `dill` instead.
+ refact(op): convert :meth:`Operation._validate()` into a function,
  to be called by clients wishing to automate operation construction.
+ refact(op): replace ``**kwargs`` with named-args in class:`FunctionalOperation`,
  because it allowed too wide args, and offered no help to the user.
+ REFACT(configs): privatize :data:`network._execution_configs`; expose more
  config-methods from base package.


v2.1.1 (12 Nov 2019, @ankostis): global configs
===============================================
+ BREAK: drop Python-3.6 compatibility.
+ FEAT: Use (possibly multiple) global configurations for all networks,
  stored in a :class:`contextvars.ContextVar`.
+ ENH/BREAK: Use a (possibly) single `execution_pool` in global-configs.
+ feat: add `abort` flag in global-configs.
+ feat: add `skip_evictions` flag in global-configs.


v2.1.0 (20 Oct 2019, @ankostis): DROP BW-compatible, Restruct modules/API, Plan perfect evictions
=================================================================================================
The first non pre-release for 2.x train.

+ BRAKE API:  DROP Operation's ``params`` - use funtools.partial() instead.

+ BRAKE API: DROP Backward-Compatible ``Data`` & ``Operation`` classes,

+ BRAKE: DROP Pickle workarounds - expected to use ``dill`` instead.

+ break(jetsam): drop "graphtik_` prefix from annotated attribute

+ ENH(op): now ``operation()`` supported the "builder pattern" with
  :meth:`.operation.withset()`.

+ REFACT: renamed internal package `functional --> nodes` and moved classes around,
  to break cycles easier, (``base`` works as suppposed to), not to import early  everything,
  but to fail plot early if ``pydot`` dependency missing.

+ REFACT: move PLAN and ``compute()`` up, from ``Network --> NetworkOperation``.

+ ENH(NET): new PLAN BULDING algorithm produces PERFECT EVICTIONS,
  that is, it gradually eliminates from the solution all non-asked outputs.

  + enh: pruning now cleans isolated data.
  + enh: eviction-instructions are inserted due to two different conditions:
    once for unneeded data in the past, and another for unused produced data
    (those not belonging typo the pruned dag).
  + enh: discard immediately irrelevant inputs.

+ ENH(net): changed results, now unrelated inputs are not included in solution.

+ refact(sideffect): store them as node-attributes in DAG, fix their combination
  with pinning & eviction.

+ fix(parallel): eviction was not working due to a typo 65 commits back!


v2.0.0b1 (15 Oct 2019, @ankostis): Rebranded as *Graphtik* for Python 3.6+
==========================================================================
Continuation of :gh:`30` as :gh:`31`, containing review-fixes in huyng/graphkit#1.

Network
-------
+ FIX: multithreaded operations were failing due to shared
  :attr:`ExecutionPlan.executed`.

+ FIX: prunning sometimes were inserting plan string in DAG.
  (not ``_DataNode``).

+ ENH: heavily reinforced exception annotations ("jetsam"):

  - FIX: (8f3ec3a) outer graphs/ops do not override the inner cause.
  - ENH: retrofitted exception-annotations as a single dictionary, to print it in one shot
    (8f3ec3a & 8d0de1f)
  - enh: more data in a dictionary
  - TCs: Add thorough TCs (8f3ec3a & b8063e5).

+ REFACT: rename `Delete`-->`Evict`, removed `Placeholder` from nadanodes, privatize node-classes.

+ ENH: collect "jetsam" on errors and annotate exceptions with them.

+ ENH(sideffects): make them always DIFFERENT from regular DATA, to allow to co-exist.

+ fix(sideffects): typo in add_op() were mixing needs/provides.

+ enh: accept a single string as `outputs` when running graphs.


Testing & other code:
---------------------
+ TCs: `pytest` now checks sphinx-site builds without any warnings.

+ Established chores with build services:

  + Travis (and auto-deploy to PyPi),
  + codecov
  + ReadTheDocs



v1.3.0 (Oct 2019, @ankostis): NEVER RELEASED: new DAG solver, better plotting & "sideffect"
===========================================================================================

Kept external API (hopefully) the same, but revamped pruning algorithm and
refactored network compute/compile structure, so results may change; significantly
enhanced plotting.  The only new feature actually is the :class:`sideffect`` modifier.

Network:
--------

+ FIX(:gh:`18`, :gh:`26`, :gh:`29`, :gh:`17`, :gh:`20`): Revamped DAG SOLVER
  to fix bad pruning described in :gh:`24` & :gh:`25`

  Pruning now works by breaking incoming provide-links to any given
  intermedediate inputs dropping operations with partial inputs or without outputs.

  The end result is that operations in the graph that do not have all inputs satisfied,
  they are skipped (in v1.2.4 they crashed).

  Also started annotating edges with optional/sideffects, to make proper use of
  the underlying ``networkx`` graph.

  |v130-flowchart|

+ REFACT(:gh:`21`, :gh:`29`): Refactored Network and introduced :class:`ExecutionPlan` to keep
  compilation results (the old ``steps`` list, plus input/output names).

  Moved also the check for when to evict a value, from running the execution-plan,
  to whenbuilding it; thus, execute methods don't need outputs anymore.

+ ENH(:gh:`26`): "Pin* input values that may be overriten by calculated ones.

  This required the introduction of the new :class:`_PinInstruction` in
  the execution plan.

+ FIX(:gh:`23`, :gh:`22`-2.4.3): Keep consistent order of ``networkx.DiGraph``
  and *sets*, to generate deterministic solutions.

  *Unfortunately*, it non-determinism has not been fixed in < PY3.5, just
  reduced the frequency of `spurious failures
  <https://travis-ci.org/yahoo/graphkit/builds/594729787>`_, caused by
  unstable dicts, and the use of subgraphs.

+ enh: Mark outputs produced by :class:`NetworkOperation`'s needs as ``optional``.
  TODO: subgraph network-operations would not be fully functional until
  *"optional outpus"* are dealt with (see :gh:`22`-2.5).

+ enh: Annotate operation exceptions with ``ExecutionPlan`` to aid debug sessions,

+ drop: methods ``list_layers()``/``show layers()`` not needed, ``repr()`` is
  a better replacement.


Plotting:
---------

+ ENH(:gh:`13`, :gh:`26`, :gh:`29`): Now network remembers last plan and uses that
  to overlay graphs with the internals of the planing and execution: |sample-plot|


    - execution-steps & order
    - evict & pin instructions
    - given inputs & asked outputs
    - solution values (just if they are present)
    - "optional" needs & broken links during pruning

+ REFACT: Move all API doc on plotting in a single module, splitted in 2 phases,
  build DOT & render DOT

+ FIX(:gh:`13`): bring plot writing into files up-to-date from PY2; do not create plot-file
  if given file-extension is not supported.

+ FEAT: path `pydot library <https://pypi.org/project/pydot/>`_ to support rendering
  in *Jupyter notebooks*.



Testing & other code:
---------------------

 - Increased coverage from 77% --> 90%.

+ ENH(:gh:`28`): use ``pytest``, to facilitate TCs parametrization.

+ ENH(:gh:`30`): Doctest all code; enabled many assertions that were just print-outs
  in v1.2.4.

+ FIX: ``operation.__repr__()`` was crashing when not all arguments
  had been set - a condition frequtnly met during debugging session or failed
  TCs (inspired by @syamajala's 309338340).

+ enh: Sped up parallel/multihtread TCs by reducing delays & repetitions.

  .. tip::
    You need ``pytest -m slow`` to run those slow tests.


Chore & Docs:
-------------

+ FEAT: add changelog in ``CHANGES.rst`` file, containing  flowcharts
  to compare versions ``v1.2.4 <--> v1.3..0``.
+ enh: updated site & documentation for all new features, comparing with v1.2.4.
+ enh(:gh:`30`): added "API reference' chapter.
+ drop(build): ``sphinx_rtd_theme`` library is the default theme for Sphinx now.
+ enh(build): Add ``test`` *pip extras*.
+ sound: https://www.youtube.com/watch?v=-527VazA4IQ,
  https://www.youtube.com/watch?v=8J182LRi8sU&t=43s



v1.2.4 (Mar 7, 2018)
====================

+ Issues in pruning algorithm: :gh:`24`, :gh:`25`
+ Blocking bug in plotting code for Python-3.x.
+ Test-cases without assertions (just prints).

|v124-flowchart|



1.2.2 (Mar 7, 2018, @huyng): Fixed versioning
=============================================

Versioning now is manually specified to avoid bug where the version
was not being correctly reflected on pip install deployments



1.2.1 (Feb 23, 2018, @huyng): Fixed multi-threading bug and faster compute through caching of `find_necessary_steps`
====================================================================================================================

We've introduced a cache to avoid computing find_necessary_steps multiple times
during each inference call.

This has 2 benefits:

+ It reduces computation time of the compute call
+ It avoids a subtle multi-threading bug in networkx when accessing the graph
  from a high number of threads.



1.2.0 (Feb 13, 2018, @huyng)
============================

Added `set_execution_method('parallel')` for execution of graphs in parallel.


1.1.0 (Nov 9, 2017, @huyng)
===========================

Update setup.py


1.0.4 (Nov 3, 2017, @huyng): Networkx 2.0 compatibility
=======================================================

Minor Bug Fixes:

+ Compatibility fix for networkx 2.0
+ `net.times` now only stores timing info from the most recent run


1.0.3 (Jan 31, 2017, @huyng): Make plotting dependencies optional
=================================================================

+ Merge pull request :gh:`6` from yahoo/plot-optional
+ make plotting dependencies optional


1.0.2 (Sep 29, 2016, @pumpikano): Merge pull request :gh:`5` from yahoo/remove-packaging-dep
============================================================================================

+ Remove 'packaging' as dependency


1.0.1 (Aug 24, 2016)
====================

1.0 (Aug 2, 2016, @robwhess)
============================

First public release in PyPi & GitHub.

+ Merge pull request :gh:`3` from robwhess/travis-build
+ Travis build


.. _substitutions:


.. |sample-plot| image:: docs/source/images/sample_plot.svg
    :alt: sample graphkit plot
    :width: 120px
    :align: bottom
.. |v410-flowchart| raw:: html
    :file: docs/source/images/GraphtikFlowchart-v4.1.0.svg
.. |v130-flowchart| image:: docs/source/images/GraphkitFlowchart-v1.3.0.svg
    :alt: graphkit-v1.3.0 flowchart
    :scale: 75%
.. |v124-flowchart| image:: docs/source/images/GraphkitFlowchart-v1.2.4.svg
    :alt: graphkit-v1.2.4 flowchart
    :scale: 75%
