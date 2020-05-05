# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
A :term:`modifier` change :term:`dependency` behavior during :term:`compilation` or :term:`execution`.

The `needs` and `provides` annotated with *modifiers* designate, for instance,
:term:`optional <optionals>` function arguments, or "ghost" :term:`sideffects`.
"""
import enum
from typing import Optional, Tuple, Union


class _Optionals(enum.Enum):
    optional = 0
    vararg = 1
    varargs = 2

    @property
    def varargish(self):
        """True if the :term:`optionals` is a :term:`varargish`. """
        return self.value > 0

    @property
    def diacritic(self) -> str:
        return "?*#"[self.value]  # pylint: disable=invalid-sequence-index


class _Modifier(str):
    """
    Annotate a :term:`dependency` with a combination of :term:`modifier`.

    Private, in the sense that users should use the factory functions :func:`.mapped`,
    :func:`optional` etc, and :func:`is_optional()` predicates.
    """

    # avoid __dict__ on instances
    __slots__ = (
        "fn_kwarg",
        "optional",
        "sideffected",
        "sideffects",
        "_repr",
    )

    #: Map my name in `needs` into this kw-argument of the function.
    #: :func:`is_mapped()` returns it.
    fn_kwarg: str
    #: required is None, regular optional or varargish?
    #: :func:`is_optional()` returns it.
    #: All regulars are `mapped`.
    optional: _Optionals
    #: An existing `dependency` in `solution` that sustain (must have sustained)
    #: the :term:`sideffects` by(for) the underlying function.
    #: :func:`is_sideffected()` returns it.
    sideffected: str
    #: At least one name(s) denoting the :term:`sideffects` modification(s) on
    #: the :term:`sideffected`, performed/required by the operation.
    #:
    #: - If it is an empty tuple`, it is an abstract sideffect,
    #:    and :func:`is_pure_optional()` returns True.
    #: - If not empty :func:`is_sideffected()` returns true
    #:   (the :attr:`sideffected`).
    sideffects: Tuple[Union[str, None]]
    #: pre-calculated representation
    _repr: str

    def __new__(
        cls,
        name: str,
        fn_kwarg: str = None,
        optional: _Optionals = None,
        sideffects: Tuple[Union[str, None]] = None,
    ) -> "_Modifier":
        sideffected = _repr = None
        ## Sanity checks
        #
        assert optional is None or isinstance(optional, _Optionals), (
            "Invalid optional: ",
            locals(),
        )
        # Validate user inputs.
        #
        if sideffects:
            double_sideffects = [
                f"{type(i).__name__}({i!r})" for i in sideffects if type(i) is not str
            ]
            if double_sideffects:
                raise ValueError(
                    f"Expecting regular strings as sideffects, got: {double_sideffects!r}"
                )
        if optional in (_Optionals.vararg, _Optionals.varargs):
            if fn_kwarg:
                raise ValueError(
                    f"Cannot have `fn_kwargs` with {optional.name} {name!r}"
                )

        ## Ensure all (non-vargish and non-pure-SFX) optionals
        #  are passed in as kwarg.
        #
        if optional == _Optionals.optional and not fn_kwarg and not sideffects == ():
            fn_kwarg = name

        # Decide:
        #  - string-name on sideffects
        #  - repr for all (with diacritics for optionals)
    if sideffects is not None:
            if sideffects == ():
                assert fn_kwarg is None, (
                    "Pure sideffects cannot map `fn_kwarg`:",
                    locals(),
                )
                if is_sideffect(name):
                    # import re
                    # if is_pure_sideffect(name):
                    #     m = re.match(r"sideffect\((.*)\)", name)
                    #     if m:
                    #         name = m.group(1)
                    # else:
                    raise ValueError(
                        "Expecting a regular string for sideffect"
                        f", got: {type(name).__name__}({name!r})"
                    )

                # Repr display also optionality (irrelevant to object's identity)
                qmark = "?" if optional else ""
                _repr = f"sideffect{qmark}: {str(name)!r}"
                name = f"sideffect: {str(name)!r}"
            else:  # sideffected
                if is_sideffect(name):
                    raise ValueError(
                        f"Expecting a non-sideffect for sideffected"
                        f", got: {type(name).__name__}({name!r})"
                    )

                sideffected = str(name)
                sfx_str = ", ".join(repr(i) for i in sideffects)

                qmark = optional.diacritic if optional else ""
                # Mapped string is so convoluted bc it mimics `optional`
                # when `fn_kwarg` given.
                map_str = (
                    f", fn_kwarg={fn_kwarg!r}" if fn_kwarg and fn_kwarg != name else ""
                )
                ## Repr display also optionality & mapped-fn-kw
                #  (irrelevant to object's identity)
                _repr = f"sideffected{qmark}({sideffected!r}<--{sfx_str}{map_str})"

                name = f"sideffected({sideffected!r}<--{sfx_str})"
        elif optional or fn_kwarg:
            map_str = f"-->{fn_kwarg!r}" if fn_kwarg != name else ""
            _repr = f"{'optional' if optional else 'mapped'}('{name}'{map_str})"
        else:
            raise ValueError(f"Shouldn't it be plain string? {name!r}")

        obj = super().__new__(cls, name)

        obj._repr = str(_repr) if _repr is not None else None
        obj.fn_kwarg = fn_kwarg
        obj.optional = optional
        obj.sideffected = sideffected
        obj.sideffects = sideffects

        return obj

    def __repr__(self):
        return super().__repr__() if self._repr is None else self._repr

    def withset(self, **kw):
        """
        Make a new modifier with kwargs: name(or sideffected), fn_kwarg, optional, sideffects

        :param optional:
            either a bool or an :class:`_Optionals` enum, as taken from :attr:`.optional`
            from another modifier instance
        """
        dep = _Modifier(
            kw.pop("name", self.sideffected if self.sideffected else str(self)),
            kw.pop("fn_kwarg", self.fn_kwarg),
            kw.pop("optional", self.optional),
            kw.pop("sideffects", self.sideffects),
        )
        if kw:
            raise ValueError(
                f"Invalid kwargs: {kw}"
                "\n  valid kwargs: name(or sideffected), fn_kwarg, optional, sideffects"
            )
        return dep


def is_mapped(dep) -> Optional[str]:
    """
    Check if a :term:`dependency` is mapped (and get it).

    All non-varargish optionals are "mapped" (including sideffected ones).
    """
    return getattr(dep, "fn_kwarg", None)


def is_optional(dep) -> bool:
    """
    Check (and get) if a :term:`dependency` is optional.

    Varargish & optional sideffects are included.
    """
    return getattr(dep, "optional", None)


def is_vararg(dep) -> bool:
    """Check if an :term:`optionals` dependency is `vararg`."""
    return getattr(dep, "optional", None) == _Optionals.vararg


def is_varargs(dep) -> bool:
    """Check if an :term:`optionals` dependency is `varargs`."""
    return getattr(dep, "optional", None) == _Optionals.varargs


def is_varargish(dep) -> bool:
    """Check if an :term:`optionals` dependency is :term:`varargish`."""
    try:
        return dep.optional.varargish
    except Exception:
        return False


def is_sideffect(dep) -> bool:
    """Check if a dependency is :term:`sideffects` or :term:`sideffected`."""
    return getattr(dep, "sideffects", None) is not None


def is_pure_sideffect(dep) -> bool:
    """Check if it is :term:`sideffects` but not a :term:`sideffected`."""
    return getattr(dep, "sideffects", None) == ()


def is_sideffected(dep) -> bool:
    """Check if it is :term:`sideffected` (and get it)."""
    return getattr(dep, "sideffected", None)


def rename_dependency(dep, ren):
    """
    Renames `dep` as `ren` or call `ren`` (if callable) to decide its name,

    preserving any :func:`mapped` to old-name.
    """
    if callable(ren):
        renamer = ren
    else:
        renamer = lambda n: ren

    if isinstance(dep, _Modifier):
        if is_pure_sideffect(dep):
            pass  # TODO: rename sfx?
        else:
            old_name = dep.sideffected if is_sideffected(dep) else str(dep)
            new_name = renamer(old_name)
            dep = dep.withset(name=new_name)
    else:  # plain string
        dep = renamer(dep)

    return dep


def mapped(name: str, fn_kwarg: str):
    """
    Annotate a :term:`needs` that (optionally) map `inputs` name --> argument-name.

    The value of a mapped dependencies is passed in as *keyword argument*
    to the underlying function.

    :param fn_kwarg:
        The argument-name corresponding to this named-input.
        If it is None, assumed the same as `name`, so as
        to behave always like kw-type arg, and to preserve its fn-name
        if ever renamed.

        .. Note::
            This extra mapping argument is needed either for :term:`optionals`
            (but not :term:`varargish`), or for functions with keywords-only arguments
            (like ``def func(*, foo, bar): ...``),
            since `inputs` are normally fed into functions by-position, not by-name.
    :return:
        a :class:`_Modifier` instance, even if no `fn_kwarg` is given OR
        it is the same as `name`.

    **Example:**

    In case the name of the function arguments is different from the name in the
    `inputs` (or just because the name in the `inputs` is not a valid argument-name),
    you may *map* it with the 2nd argument of :func:`.mapped`:

        >>> from graphtik import operation, compose, mapped

        >>> @operation(needs=['a', mapped("name-in-inputs", "b")], provides="sum")
        ... def myadd(a, *, b):
        ...    return a + b
        >>> myadd
        FunctionalOperation(name='myadd',
                            needs=['a', mapped('name-in-inputs'-->'b')],
                            provides=['sum'],
                            fn='myadd')

        >>> graph = compose('mygraph', myadd)
        >>> graph
        NetworkOperation('mygraph', needs=['a', 'name-in-inputs'], provides=['sum'], x1 ops: myadd)

        >>> sol = graph.compute({"a": 5, "name-in-inputs": 4})['sum']
        >>> sol
        9

        .. graphtik::
    """
    # Must pass a truthy fn_kwarg bc cstor cannot not know its mapped.
    return _Modifier(name, fn_kwarg=fn_kwarg or name)


def optional(name: str, fn_kwarg: str = None):
    """
    Annotate :term:`optionals` `needs` corresponding to *defaulted* op-function arguments, ...

    received only if present in the `inputs` (when operation is invoked).

    The value of an optional is passed in as a *keyword argument*
    to the underlying function.

    :param fn_kwarg:
        the name for the function argument it corresponds;
        if a falsy is given, same as `name` assumed,
        to behave always like kw-type arg and to preserve its fn-name
        if ever renamed.

    **Example:**

        >>> from graphtik import operation, compose, optional

        >>> @operation(name='myadd',
        ...            needs=["a", optional("b")],
        ...            provides="sum")
        ... def myadd(a, b=0):
        ...    return a + b

    Notice the default value ``0`` to the ``b`` annotated as optional argument:

        >>> graph = compose('mygraph', myadd)
        >>> graph
        NetworkOperation('mygraph',
                         needs=['a', optional('b')],
                         provides=['sum'],
                         x1 ops: myadd)

    .. graphtik::

    The graph works both with and without ``c`` provided in the inputs:

        >>> graph(a=5, b=4)['sum']
        9
        >>> graph(a=5)
        {'a': 5, 'sum': 5}

    Like :func:`.mapped` you may map input-name to a different function-argument:

        >>> operation(needs=['a', optional("quasi-real", "b")],
        ...           provides="sum"
        ... )(myadd.fn)  # Cannot wrap an operation, its `fn` only.
        FunctionalOperation(name='myadd',
                            needs=['a', optional('quasi-real'-->'b')],
                            provides=['sum'],
                            fn='myadd')

    """
    return _Modifier(name, fn_kwarg=fn_kwarg, optional=_Optionals.optional)


def vararg(name: str):
    """
    Annotate a :term:`varargish` `needs` to  be fed as function's ``*args``.

    .. seealso::
        Consult also the example test-case in: :file:`test/test_op.py:test_varargs()`,
        in the full sources of the project.

    **Example:**

    We designate ``b`` & ``c`` as `vararg` arguments:

        >>> from graphtik import operation, compose, vararg

        >>> @operation(
        ...     needs=['a', vararg('b'), vararg('c')],
        ...     provides='sum'
        ... )
        ... def addall(a, *b):
        ...    return a + sum(b)
        >>> addall
        FunctionalOperation(name='addall', needs=['a', vararg('b'), vararg('c')], provides=['sum'], fn='addall')


        >>> graph = compose('mygraph', addall)

    .. graphtik::

    The graph works with and without any of ``b`` or ``c`` inputs:

        >>> graph(a=5, b=2, c=4)['sum']
        11
        >>> graph(a=5, b=2)
        {'a': 5, 'b': 2, 'sum': 7}
        >>> graph(a=5)
        {'a': 5, 'sum': 5}

    """
    return _Modifier(name, optional=_Optionals.vararg)


def varargs(name: str):
    """
    An :term:`varargish`  :func:`.vararg`, naming a *iterable* value in the inputs.

    .. seealso::
        Consult also the example test-case in: :file:`test/test_op.py:test_varargs()`,
        in the full sources of the project.

    **Example:**

        >>> from graphtik import operation, compose, varargs

        >>> def enlist(a, *b):
        ...    return [a] + list(b)

        >>> graph = compose('mygraph',
        ...     operation(name='enlist', needs=['a', varargs('b')],
        ...     provides='sum')(enlist)
        ... )
        >>> graph
        NetworkOperation('mygraph',
                         needs=['a', optional('b')],
                         provides=['sum'],
                         x1 ops: enlist)

    .. graphtik::

    The graph works with or without `b` in the inputs:

        >>> graph(a=5, b=[2, 20])['sum']
        [5, 2, 20]
        >>> graph(a=5)
        {'a': 5, 'sum': [5]}
        >>> graph(a=5, b=0xBAD)
        Traceback (most recent call last):
        ...
        graphtik.base.MultiValueError: Failed preparing needs:
            1. Expected needs[varargs('b')] to be non-str iterables!
            +++inputs: ['a', 'b']
            +++FunctionalOperation(name='enlist', needs=['a', varargs('b')], provides=['sum'], fn='enlist')

    .. varargs-mistake-start
    .. Attention::
        To avoid user mistakes, *varargs* do not accept :class:`str` :term:`inputs`
        (though iterables):

        >>> graph(a=5, b="mistake")
        Traceback (most recent call last):
        ...
        graphtik.base.MultiValueError: Failed preparing needs:
            1. Expected needs[varargs('b')] to be non-str iterables!
            +++inputs: ['a', 'b']
            +++FunctionalOperation(name='enlist',
                                   needs=['a', varargs('b')],
                                   provides=['sum'],
                                   fn='enlist')

    .. varargs-mistake-end
    """
    return _Modifier(name, optional=_Optionals.varargs)


def sideffect(name, optional: bool = None):
    """
    :term:`sideffects` denoting modifications beyond the scope of the solution.

    Both `needs` & `provides` may be designated as *sideffects* using this modifier.
    They work as usual while solving the graph (:term:`compilation`) but
    they have a limited interaction with the operation's underlying function;
    specifically:

    - input sideffects must exist in the solution as :term:`inputs` for an operation
      depending on it to kick-in, when the computation starts - but this is not necessary
      for intermediate sideffects in the solution during execution;
    - input sideffects are NOT fed into underlying functions;
    - output sideffects are not expected from underlying functions, unless
      a rescheduled operation with :term:`partial outputs` designates a sideffected
      as *canceled* by returning it with a falsy value (operation must `returns dictionary`).

    .. hint::
        If modifications involve some input/output, prefer the :func:`.sideffected`
        modifier.

        You may still convey this relationships by including the dependency name
        in the string - in the end, it's just a string - but no enforcement of any kind
        will happen from *graphtik*, like:

        >>> from graphtik import sideffect

        >>> sideffect("price[sales_df]")
        sideffect: 'price[sales_df]'

    **Example:**

    A typical use-case is to signify changes in some "global" context,
    outside `solution`:


        >>> from graphtik import operation, compose, sideffect

        >>> @operation(provides=sideffect("lights off"))  # sideffect names can be anything
        ... def close_the_lights():
        ...    pass

        >>> graph = compose('strip ease',
        ...     close_the_lights,
        ...     operation(
        ...         name='undress',
        ...         needs=[sideffect("lights off")],
        ...         provides="body")(lambda: "TaDa!")
        ... )
        >>> graph
        NetworkOperation('strip ease', needs=[sideffect: 'lights off'],
                         provides=[sideffect: 'lights off', 'body'],
                         x2 ops: close_the_lights, undress)

        >>> sol = graph()
        >>> sol
        {'body': 'TaDa!'}

    .. graphtik::
        :name: sideffect

    .. note::
        Something has to provide a sideffect for a function needing it to execute -
        this could be another operation, like above, or the user-inputs;
        just specify some dummy value for the sideffect:

            >>> sol = graph.compute({sideffect("lights off"): True})

        .. graphtik::

    """
    return _Modifier(
        name, optional=_Optionals.optional if optional else None, sideffects=()
    )


def sideffected(
    dependency: str,
    sideffect0: str,
    *sideffects: str,
    fn_kwarg: str = None,
    optional: bool = None,
):
    r"""
    Annotates a :term:`sideffected` dependency in the solution sustaining side-effects.

    :param fn_kwarg:
        the name for the function argument it corresponds.
        When optional, it becomes the same as `name` if falsy, so as
        to behave always like kw-type arg, and to preserve fn-name if ever renamed.
        When not optional, if not given, it's all fine.

    Like :func:`.sideffect` but annotating a *real* :term:`dependency` in the solution,
    allowing that dependency to be present both in :term:`needs` and :term:`provides`
    of the same function.

    **Example:**

    A typical use-case is to signify columns required to produce new ones in
    pandas dataframes (:gray:`emulated with dictionaries`):


        >>> from graphtik import operation, compose, sideffected

        >>> @operation(needs="order_items",
        ...            provides=sideffected("ORDER", "Items", "Prices"))
        ... def new_order(items: list) -> "pd.DataFrame":
        ...     order = {"items": items}
        ...     # Pretend we get the prices from sales.
        ...     order['prices'] = list(range(1, len(order['items']) + 1))
        ...     return order

        >>> @operation(
        ...     needs=[sideffected("ORDER", "Items"), "vat rate"],
        ...     provides=sideffected("ORDER", "VAT")
        ... )
        ... def fill_in_vat(order: "pd.DataFrame", vat: float):
        ...     order['VAT'] = [i * vat for i in order['prices']]
        ...     return order

        >>> @operation(
        ...     needs=[sideffected("ORDER", "Prices", "VAT")],
        ...     provides=sideffected("ORDER", "Totals")
        ... )
        ... def finalize_prices(order: "pd.DataFrame"):
        ...     order['totals'] = [p + v for p, v in zip(order['prices'], order['VAT'])]
        ...     return order

    To view all internal :term:`dependencies <dependency>`, enable DEBUG
    in :term:`configurations`:

        >>> from graphtik import debug_enabled

        >>> with debug_enabled(True):
        ...     finalize_prices
        FunctionalOperation(name='finalize_prices',
                            needs=[sideffected('ORDER'<--'Prices'),
                                   sideffected('ORDER'<--'VAT')],
                            op_needs=[sideffected('ORDER'<--'Prices'),
                                      sideffected('ORDER'<--'VAT')],
                            fn_needs=['ORDER'],
                            provides=[sideffected('ORDER'<--'Totals')],
                            op_provides=[sideffected('ORDER'<--'Totals')],
                            fn_provides=['ORDER'], fn='finalize_prices')

    Notice that declaring a single *sideffected* with multiple *sideffects*,
    expands into multiple  *"singular"* ``sideffected`` dependencies in the network
    (check ``needs`` & ``op_needs`` above).

        >>> proc_order = compose('process order', new_order, fill_in_vat, finalize_prices)
        >>> sol = proc_order.compute({
        ...      "order_items": ["toilet-paper", "soap"],
        ...      "vat rate": 0.18,
        ... })
        >>> sol
        {'order_items': ['toilet-paper', 'soap'],
         'vat rate': 0.18,
         'ORDER': {'items': ['toilet-paper', 'soap'],
                   'prices': [1, 2],
                   'VAT': [0.18, 0.36],
                   'totals': [1.18, 2.36]}}

    .. graphtik::
        :height: 640
        :width: 100%
        :name: sideffecteds

    Notice that although many functions consume & produce the same ``ORDER`` dependency
    (check ``fn_needs`` & ``fn_provides``, above), something that :orange:`would have formed
    cycles`, the wrapping operations *need* and *provide* different sideffected instances,
    breaking the cycles.


    """
    return _Modifier(
        dependency,
        sideffects=(sideffect0,) + sideffects,
        optional=_Optionals.optional if optional else None,
        fn_kwarg=fn_kwarg,
    )


def sideffected_vararg(dependency: str, sideffect0: str, *sideffects: str):
    """Like :func:`sideffected` + :func:`vararg`. """
    return _Modifier(
        dependency, sideffects=(sideffect0,) + sideffects, optional=_Optionals.vararg
    )


def sideffected_varargs(dependency: str, sideffect0: str, *sideffects: str):
    """Like :func:`sideffected` + :func:`varargs`. """
    return _Modifier(
        dependency, sideffects=(sideffect0,) + sideffects, optional=_Optionals.varargs,
    )
