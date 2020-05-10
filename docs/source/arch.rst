.. _arch:

============
Architecture
============


.. default-role:: term
.. glossary::

    compute
    computation
    phase
        |v440-flowchart|
        The definition & execution of networked operation is split in 1+2 phases:

        - `composition`
        - `compilation`
        - `execution`

        ... it is constrained by these IO data-structures:

        - `operation`\(s)
        - `dependencies <dependency>` (`needs` & `provides`)
        - given `inputs`
        - asked `outputs`

        ... populates these low-level data-structures:

        - `network graph` (COMPOSE time)
        - `execution dag` (COMPILE time)
        - `execution steps` (COMPILE time)
        - `solution` (EXECUTE time)

        ... and utilizes these main classes:

        .. autosummary::

            graphtik.composition.FunctionalOperation
            graphtik.composition.Pipeline
            graphtik.network.Network
            graphtik.execution.ExecutionPlan
            graphtik.execution.Solution

        ... plus those for `plotting`:

        .. autosummary::

            graphtik.plot.Plotter
            graphtik.plot.Theme

    compose
    composition
        The :term:`phase` where `operation`\s are constructed and grouped
        into `pipeline`\s and corresponding `network`\s based on their `dependencies
        <dependency>`.

        .. Tip::
            - Use :func:`.operation` factory to construct :class:`.FunctionalOperation`
              instances (a.k.a. operations).
            - Use :func:`.compose()` factory to build :class:`.Pipeline`
              instances (a.k.a. pipelines).

    combine pipelines
        When `operation`\s and/or `pipeline`\s are `compose`\d together, there are
        two ways to combine the operations contained into the new pipeline:
        `operation merging` (default) and `operation nesting`.

        They are selected by the ``nest`` parameter of :func:`.compose()` factory.

    operation merging
        The default method to `combine pipelines`, also applied when simply merging `operation`\s.

        Any identically-named operations override each other,
        with the operations added earlier in the ``.compose()`` call
        (further to the left) winning over those added later (further to the right).

        :seealso: :ref:`operation-merging`

    operation nesting
        The elaborate method to `combine pipelines` forming *clusters*.

        The original pipelines are preserved intact in "isolated" clusters,
        by prefixing the names of their operations (and optionally data)
        by the name of the respective original pipeline that contained them
        (or the user defines the renames).

        :seealso: :ref:`operation-nesting`, :func:`.compose`, :class:`.RenArgs`,
            :func:`.nest_any_node()`, :func:`.dep_renamed()`, :attr:`.PlotArgs.clusters`

    compile
    compilation
        The :term:`phase` where the :class:`.Network` creates a new `execution plan`
        by `pruning` all `graph` nodes into a subgraph `dag`, and  deriving
        the `execution steps`.

    execute
    execution
    sequential
        The :term:`phase` where the :class:`.ExecutionPlan` calls the underlying functions
        of all `operation`\s contained in `execution steps`, with `inputs`/`outputs`
        taken from the `solution`.

        Currently there are 2 ways to execute:

        - *sequential*
        - *parallel*, with a :class:`multiprocessing.pool.ProcessPool`

        Plans may abort their execution by setting the `abort run` global flag.

    net
    network
        the :class:`.Network` contains a `graph` of `operation`\s and can
        `compile` (and cache) `execution plan`\s, or `prune` a cloned *network* for
        given `inputs`/`outputs`/`node predicate`.

    plan
    execution plan
        Class :class:`.ExecutionPlan` perform the `execution` phase which contains
        the `dag` and the `steps`.

        `compile`\ed *execution plans* are cached in :attr:`.Network._cached_plans`
        across runs with (`inputs`, `outputs`, `predicate`) as key.

    solution
        A :class:`.Solution` instance created internally by :meth:`.Pipeline.compute()`
        to hold the values both `inputs` & `outputs`, and the status of *executed* operations.
        It is based on a :class:`collections.ChainMap`, to keep one dictionary
        for each `operation` executed +1 for inputs.

        The results of the last operation executed "wins" in the *outputs* produced,
        and the base (least precedence) is the *inputs* given when the `execution` started.

    graph
    network graph
        A graph of `operation`\s linked by their `dependencies <dependency>` forming a `pipeline`.

        The :attr:`.Network.graph` (currently a DAG) contains all :class:`.FunctionalOperation`
        and data-nodes (string or `modifier`) of a `pipeline`.

        They are layed out and connected by repeated calls of
        :meth:`.Network._append_operation()` by Network constructor during `composition`.

        This graph is then `prune`\d to extract the `dag`, and the `execution steps`
        are calculated, all ingredients for a new :class:`.ExecutionPlan`.

    prune
    pruning
        A subphase of `compilation` performed by method :meth:`.Network._prune_graph()`,
        which extracts a subgraph `dag` that does not contain any `unsatisfied operation`\s.

        It topologically sorts the `graph`, and *prunes* based on given `inputs`,
        asked `outputs`, `node predicate` and `operation` `needs` & `provides`.

    unsatisfied operation
        The core of `pruning` & `rescheduling`, performed by
        :func:`.network.unsatisfied_operations()` function, which collects
        all `operation`\s with unreachable `dependencies <dependency>`:

        - they have `needs` that do not correspond to any of the given `inputs` or
          the intermediately `compute`\d `outputs` of the `solution`;
        - all their `provides` are NOT needed by any other operation, nor are asked
          as *outputs*.

    dag
    execution dag
    solution dag
        There are 2 *directed-acyclic-graphs* instances used:

        - the :attr:`.ExecutionPlan.dag`,  in the `execution plan`, which contains
          the `prune`\d  nodes, used to decide the `execution steps`;
        - the :attr:`.Solution.dag` in the `solution`, which derives the
          `canceled operation`\s due to `reschedule`\d/failed operations upstream.

    steps
    execution steps
        The `plan` contains a list of the operation-nodes only from the `dag`,
        topologically sorted, and interspersed with *instruction steps* needed to
        `compute` the asked `outputs` from the given `inputs`.

        They are built by :meth:`.Network._build_execution_steps()` based on
        the subgraph `dag`.

        The only *instruction* step is for performing `evictions`.

    evictions
        A memory footprint optimization where intermediate `inputs` & `outputs`
        are erased from `solution` as soon as they are not needed further down the `dag`.

        *Evictions* are pre-calculated during `compilation`, where :class:`._EvictInstruction`
        `steps` are inserted in the `execution plan`.

    overwrite
        Values in the `solution` that have been written by more than one `operation`\s,
        accessed by :attr:`.Solution.overwrites`.
        Note that a `sideffected` `dependency` produce usually an *overwrite*.

    inputs
        The named input values that are fed into an `operation` (or `pipeline`)
        through :meth:`.Operation.compute()` method according to its `needs`.

        These values are either:

        - given by the user to the outer `pipeline`, at the start of a `computation`, or
        - derived from `solution` using *needs* as keys, during intermediate `execution`.

    outputs
        The dictionary of computed values returned by an `operation` (or a `pipeline`)
        matching its `provides`, when method :meth:`.Operation.compute()` is called.

        Those values are either:

        - retained in the `solution`, internally during `execution`, keyed by
          the respective *provide*, or
        - returned to user after the outer *netop* has finished `computation`.

        When no specific outputs requested from a *netop*, :meth:`.Pipeline.compute()`
        returns all intermediate `inputs` along with the *outputs*, that is,
        no `evictions` happens.

        An *operation* may return `partial outputs`.

    netop
    pipeline
        The :class:`.Pipeline` class holding a `network` of `operation`\s
        and `dependencies <dependency>`.

    operation
        Either the abstract notion of an action with specified `needs` and `provides`,
        *dependencies*, or the concrete wrapper :class:`.FunctionalOperation` for
        (any :func:`callable`), that feeds on `inputs` and update `outputs`,
        from/to `solution`, or given-by/returned-to the user by a `pipeline`.

        The distinction between *needs*/*provides* and *inputs*/*outputs* is akin to
        function *parameters* and *arguments* during define-time and run-time,
        respectively.

    dependency
        The name of a `solution` value an `operation` `needs` or `provides`.

        - *Dependencies* are declared during `composition`, when building
          :class:`.FunctionalOperation` instances.
          *Operations* are then interlinked together, by matching the *needs* & *provides*
          of all *operations* contained in a `pipeline`.

        - During `compilation` the `graph` is then `prune`\d based on the :term:`reachability
          <unsatisfied operation>` of the *dependencies*.

        - During `execution` :meth:`.Operation.compute()` performs 2 "matchings":

          - *inputs* & *outputs* in *solution* are accessed by the *needs* & *provides*
            names of the *operations*;
          - operation *needs* & *provides* are zipped against the underlying function's
            arguments and results.

          These matchings are affected by `modifier`\s.

    needs
    fn_needs
        The list of `dependency` names an `operation` requires from `solution` as `inputs`,

        roughly corresponding to underlying function's arguments (**fn_needs**).

        Specifically, :meth:`.Operation.compute()` extracts input values
        from *solution* by these names, and matches them against function arguments,
        mostly by their positional order.
        Whenever this matching is not 1-to-1, and function-arguments  differ from
        the regular *needs*, `modifier`\s must be used.

    provides
    op_provides
    fn_provides
        The list of `dependency` names an `operation` writes to the `solution` as `outputs`,

        roughly corresponding to underlying function's results (**fn_provides**).

        Specifically, :meth:`.Operation.compute()` "zips" this list-of-names
        with the `output <outputs>` values produced when the `operation`'s
        function is called.
        Whenever this "zipping" is not 1-to-1, and function-results  differ from
        the regular *operation* (**op_provides**) (or results are not a list),
        it is possible to:

        - mark the *operation* that its function `returns dictionary`,
        - artificially extended the *provides* with `alias`\ed *fn_provides*, or
        - use `modifier`\s to annotate certain names as `sideffects`,

    alias
        Map an existing name in `fn_provides` into a duplicate, artificial one in `op_provides` .

        You cannot alias an *alias*.  See :ref:`aliases`

    returns dictionary
        When an `operation` is marked with :attr:`FunctionalOperation.returns_dict` flag,
        the underlying function is not expected to return `fn_provides` as a sequence
        but as a dictionary; hence, no "zipping" of function-results --> `fn_provides`
        takes place.

        Usefull for operations returning `partial outputs` to have full control
        over which `outputs` were actually produced, or to cancel `sideffects`.

    modifier
        A `modifier` change `dependency` behavior during `compilation` or `execution`.

        For instance, `needs` may be annotated as `optionals` function arguments,
        `provides` and *needs* can be annotated as "ghost" `sideffects`

        See :mod:`graphtik.modifiers` module.

    optionals
        A `needs` only `modifier` for a `inputs` that do not hinder `operation` execution
        (`prune`) if absent from `solution`.

        In the underlying function it corresponds to either:

        - non-compulsory function arguments (with defaults), annotated with
          :func:`.optional`, or
        - `varargish` arguments, annotated with :func:`.vararg` or :func:`.varargs`.

    varargish
        A `needs` only `modifier` for `inputs` to be appended as ``*args``
        (if present in `solution`).

        There are 2 kinds, both, by definition, `optionals`:

        - the :func:`.vararg` annotates any *solution* value to be appended *once*
          in the ``*args``;
        - the :func:`.varargs` annotates *iterable* values and all its items are appended
          in the ``*args`` one-by-one.

        .. include:: ../../graphtik/modifiers.py
            :start-after: .. varargs-mistake-start
            :end-before: .. varargs-mistake-end

    sideffects
        A `modifier` denoting a fictive `dependency` linking `operation`\s into virtual flows,
        without real data exchanges.

        The side-effect modification may happen to some internal state
        not fully represented in the `graph` & `solution`.

        There are actually 2 relevant *modifiers*:

        - An *abstract sideffect* modifier (annotated with :func:`.sfx`)
          describing modifications taking place beyond the scope of the solution.

        - The `sideffected` modifier (annotated with :func:`.sfxed`)
          denoting modifications on a *real* dependency read from and written to
          the solution.

        Both kinds of sideffects participate in the `compilation` of the graph,
        and both may be given or asked in the `inputs` & `outputs` of a `pipeline`,
        but they are never given to functions.
        A function of a `returns dictionary` operation can return a falsy value
        to declare it as :term:`canceled <partial outputs>`.

    sideffected
        A `modifier` that denotes `sideffects` on a `dependency` that exists in `solution`,
        allowing to declare an `operation` that both `needs` and `provides` that
        *sideffected dependency*.

        .. Note::
            To be precise, the *"sideffected dependency"* is the name held in
            :attr:`._Modifier.sideffected` attribute of a *modifier* created by
            :func:`.sfxed` function.

        The `outputs` of a *sideffected dependency* will produce an `overwrite` if
        the *sideffected dependency* is declared both as *needs* and *provides*
        of some operation.

        It is annotated with :func:`.sfxed`.

    reschedule
    rescheduling
    partial outputs
    canceled operation
        The partial `pruning` of the `solution`'s dag during `execution`.
        It happens when any of these 2 conditions apply:

        - an `operation` is marked with the :attr:`.FunctionalOperation.rescheduled`
          attribute, which means that its underlying *callable* may produce
          only a subset of its `provides` (*partial outputs*);
        - `endurance` is enabled, either globally (in the `configurations`), or
          for a specific *operation*.

        the *solution* must then *reschedule* the remaining operations downstream,
        and possibly *cancel* some of those ( assigned in :attr:`.Solution.canceled`).

        *Partial operations* are usually declared with `returns dictionary` so that
        the underlying function can control which of the outputs are returned.

        See :ref:`rescheduled`

    endurance
    endured
        Keep executing as many `operation`\s as possible, even if some of them fail.
        Endurance for an operation  is enabled if :func:`.set_endure_operations()`
        is true globally in the `configurations` or if :attr:`.FunctionalOperation.endured`
        is true.

        You may interrogate :attr:`.Solution.executed` to discover the status
        of each executed operations or call one of :meth:`.check_if_incomplete()`
        or :meth:`.scream_if_incomplete()`.

        See :ref:`endured`

    predicate
    node predicate
        A callable(op, node-data) that should return true for nodes to be
        included in `graph` during `compilation`.

    abort run
        A global `configurations` flag that when set with :func:`.abort_run()` function,
        it halts the execution of all currently or future `plan`\s.

        It is reset automatically on every call of :meth:`.Pipeline.compute()`
        (after a successful intermediate :term:`compilation`), or manually,
        by calling :func:`.reset_abort()`.

    parallel
    parallel execution
    execution pool
    task
        `execute` `operation`\s *in parallel*, with a `thread pool` or `process pool`
        (instead of `sequential`).
        Operations and `pipeline` are marked as such on construction, or enabled globally
        from `configurations`.

        Note a `sideffects` are not expected to function with *process pools*,
        certainly not when `marshalling` is enabled.

    process pool
        When the :class:`multiprocessing.pool.Pool` class is used for `parallel` execution,
        the `task`\s  must be communicated to/from the worker process, which requires
        `pickling <https://docs.python.org/library/pickle.html>`_, and that may fail.
        With pickling failures you may try `marshalling` with *dill* library,
        and see if that helps.

        Note that `sideffects` are not expected to function at all.
        certainly not when `marshalling` is enabled.

    thread pool
        When the :func:`multiprocessing.dummy.Pool` class is used for `parallel` execution,
        the `task`\s are run *in process*, so no `marshalling` is needed.

    marshalling
        Pickling `parallel` `operation`\s and their `inputs`/`outputs` using
        the :mod:`dill` module. It is `configured <configurations>` either globally
        with :func:`.set_marshal_tasks()` or set with a flag on each
        operation / `pipeline`.

        Note that `sideffects` do not work when this is enabled.

    plottable
        Objects that can plot their graph network, such as those inheriting :class:`.Plottable`,
        (:class:`.FunctionalOperation`, :class:`.Pipeline`, :class:`.Network`,
        :class:`.ExecutionPlan`, :class:`.Solution`) or a |pydot.Dot|_ instance
        (the result of the :meth:`.Plottable.plot()` method).

        Such objects may render as SVG in *Jupiter notebooks* (through their ``plot()`` method)
        and can render in a Sphinx site with with the :rst:dir:`graphtik` *RsT directive*.
        You may control the rendered image as explained in the *tip*  of
        the :ref:`plotting` section.

        SVGs are in rendered with the `zoom-and-pan javascript library
        <https://github.com/ariutta/svg-pan-zoom>`_

        .. include:: plotting.rst
            :start-after: .. serve-sphinx-warn-start
            :end-before: .. serve-sphinx-warn-end

    plotter
    plotting
        A :class:`.Plotter` is responsible for rendering `plottable`\s as images.
        It is the `active plotter` that does that, unless overridden in a
        :meth:`.Plottable.plot()` call.
        Plotters can be customized by :ref:`various means <plot-customizations>`,
        such `plot theme`.

    active plotter
    default active plotter
        The `plotter` currently installed "in-context" of the respective `graphtik
        configuration` - this term implies also any :ref:`plot-customizations`
        done on the active plotter (such as `plot theme`).

        Installation happens by calling one of :func:`.active_plotter_plugged()` or
        :func:`.set_active_plotter` functions.

        The **default** *active plotter* is the plotter instance that this project
        comes pre-configured with, ie, when no *plot-customizations* have yet happened.

        .. include:: ../../graphtik/plot.py
            :start-after: .. theme-warn-start
            :end-before: .. theme-warn-end

    plot theme
    current theme
        The mergeable and `expandable styles <style>` contained in a :class:`.plot.Theme` instance.

        The **current theme in-use** is the :attr:`.Plotter.default_theme` attribute of
        the `active plotter`, unless overridden with the :obj:`theme` parameter when
        calling :meth:`.Plottable.plot()` (conveyed internally as the value of the
        :attr:`.PlotArgs.theme` attribute).

    style
    style expansion
        A *style* is an attribute of a `plot theme`, either a scalar value or a dictionary.

        *Styles* are collected in :class:`stacks <.StylesStack>` and are :meth:`merged
        <.StylesStack.merge>` into a single dictionary after performing the following
        :meth:`expansions <.StylesStack.expand>`:

        .. include:: ../../graphtik/plot.py
            :start-after: .. theme-expansions-start
            :end-before: .. theme-expansions-end

        .. tip::
            if :meth:`DEBUG <is_debug>` is enabled, the provenance of all style values
            appears in the tooltips of plotted graphs.

    configurations
    graphtik configuration
        The functions controlling `compile` & `execution` globally  are defined
        in :mod:`.config` module and +1 in :mod:`graphtik.plot` module;
        the underlying global data are stored in :class:`contextvars.ContextVar` instances,
        to allow for nested control.

        All *boolean* configuration flags are **tri-state** (``None, False, True``),
        allowing to "force" all operations, when they are not set to the ``None``
        value.  All of them default to ``None`` (false).


.. default-role:: obj
.. |v440-flowchart| raw:: html
    :file: images/GraphtikFlowchart-v4.4.0.svg
