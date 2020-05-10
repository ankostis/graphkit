# Copyright 2016, Yahoo Inc.
# Licensed under the terms of the Apache License, Version 2.0. See the LICENSE file associated with the project for terms.
"""
:term:`modifier`\\s change :term:`dependency` behavior during :term:`compilation` & :term:`execution`.

The `needs` and `provides` annotated with *modifiers* designate, for instance,
:term:`optional <optionals>` function arguments, or "ghost" :term:`sideffects`.

.. note::
    This module (along with :mod:`.op` & :mod:`.pipeline`) is what client code needs
    to define pipelines *on import time* without incurring a heavy price
    (~7ms on a 2019 fast PC)
"""
import enum
from typing import Optional, Tuple, Union

# fmt: off
#: Arguments-presence patterns for :class:`_Modifier` constructor.
#: Combinations missing raise errors.
_modifier_cstor_matrix = {
# (7, kw, opt, sfxed, sfx): (STR, REPR, FUNC) OR None
70000: None,
71000: (       "%(dep)s",                  "'%(dep)s'(%(kw)s)"           , "mapped"),
71100: (       "%(dep)s",                  "'%(dep)s'(?%(kw)s)"          , "optional"),
70200: (       "%(dep)s",                  "'%(dep)s'(*)"                , "vararg"),
70300: (       "%(dep)s",                  "'%(dep)s'(+)"                , "varargs"),
70010: (  "sfx('%(dep)s')",            "sfx('%(dep)s')"                  , "sfx"),
70110: (  "sfx('%(dep)s')",            "sfx('%(dep)s'(?))"               , "sfx"),
70011: ("sfxed('%(dep)s', %(sfx)s)", "sfxed('%(dep)s', %(sfx)s)"         , "sfxed"),
71011: ("sfxed('%(dep)s', %(sfx)s)", "sfxed('%(dep)s'(%(kw)s), %(sfx)s)" , "sfxed"),
71111: ("sfxed('%(dep)s', %(sfx)s)", "sfxed('%(dep)s'(?%(kw)s), %(sfx)s)", "sfxed"),
70211: ("sfxed('%(dep)s', %(sfx)s)", "sfxed('%(dep)s'(*), %(sfx)s)"      , "sfxed_vararg"),
70311: ("sfxed('%(dep)s', %(sfx)s)", "sfxed('%(dep)s'(+), %(sfx)s)"      , "sfxed_varargs"),
}
# fmt: on


def _match_modifier_args(name, *args):
    flags = [int(bool(i)) for i in args]

    # expand optional
    if args[1]:
        flags[1] = args[1].value
    pattern = "".join(str(i) for i in flags)
    pattern = int(f"7{pattern}")
    if pattern not in _modifier_cstor_matrix:
        raise ValueError(f"Invalid modifier arguments: {name}, {args}, {pattern}")

    return _modifier_cstor_matrix[pattern]


class _Optionals(enum.Enum):
    keyword = 1
    vararg = 2
    varargs = 3


class _Modifier(str):
    """
    Annotate a :term:`dependency` with a combination of :term:`modifier`.

    It is private, in the sense that users should use only:

    - the factory functions :func:`.mapped`, :func:`optional` etc,
    - the predicates :func:`is_optional()`, :func:`is_pure_sfx()` predicates, etc,
    - and the :func:`dep_renamed()`, :func:`dep_stripped()` conversion functions

    respectively, or at least :func:`_modifier()`.

    .. Note::
        User code better call :func:`_modifier()` factory which may return a plain string
        if no other arg but ``name`` are given.
    """

    # avoid __dict__ on instances
    __slots__ = (
        "fn_kwarg",
        "optional",
        "sideffected",
        "sfx_list",
        "_repr",
        "_func",
    )

    #: Map my name in `needs` into this kw-argument of the function.
    #: :func:`is_mapped()` returns it.
    fn_kwarg: str
    #: required is None, regular optional or varargish?
    #: :func:`is_optional()` returns it.
    #: All regulars are `mapped`.
    optional: _Optionals
    #: Has value only for sideffects: the pure-sideffect string or
    #: the existing :term:`sideffected` dependency.
    sideffected: str
    #: At least one name(s) denoting the :term:`sideffects` modification(s) on
    #: the :term:`sideffected`, performed/required by the operation.
    #:
    #: - If it is an empty tuple`, it is an abstract sideffect,
    #:    and :func:`is_pure_optional()` returns True.
    #: - If not empty :func:`is_sfxed()` returns true
    #:   (the :attr:`sideffected`).
    sfx_list: Tuple[Union[str, None]]
    #: pre-calculated representation
    _repr: str
    #: needed to reconstruct cstor code in :attr:`cmd`
    _func: str

    def __new__(
        cls, name, fn_kwarg, optional: _Optionals, sideffected, sfx_list, _repr, _func
    ) -> "_Modifier":
        """Warning, returns None! """
        ## sanity checks & preprocessing
        #
        if optional is not None and not isinstance(optional, _Optionals):
            raise ValueError(
                f"Invalid _Optional enum {optional!r}\n  locals={locals()}"
            )
        if sideffected and is_sfx(sideffected):
            raise ValueError(
                f"`sideffected` cannot be sideffect, got {sideffected!r}"
                f"\n  locals={locals()}"
            )
        double_sideffects = [
            f"{type(i).__name__}({i!r})" for i in sfx_list if is_sfx(i)
        ]
        if double_sideffects:
            raise ValueError(
                f"`sfx_list` cannot contain sideffects, got {double_sideffects!r}"
                f"\n  locals={locals()}"
            )

        obj = super().__new__(cls, name)
        obj.fn_kwarg = fn_kwarg
        obj.optional = optional
        obj.sideffected = sideffected
        obj.sfx_list = sfx_list
        obj._repr = _repr
        obj._func = _func

        return obj

    def __repr__(self):
        return self._repr

    @property
    def cmd(self):
        """the code to reproduce it"""
        dep = self.sideffected or str(self)
        items = [f"'{dep}'"]
        if self.sfx_list:
            items.append(", ".join(f"'{i}'" for i in self.sfx_list))
        if self.fn_kwarg and self.fn_kwarg != dep:
            fn_kwarg = f"'{self.fn_kwarg}'"
            items.append(f"fn_kwarg={fn_kwarg}" if self.sfx_list else fn_kwarg)
        if self.optional == _Optionals.keyword and self._func != "optional":
            items.append(f"optional=1" if self.sfx_list else "1")
        return f"{self._func}({', '.join(items)})"

    def __getnewargs__(self):
        return (
            str(self),
            self.fn_kwarg,
            self.optional,
            self.sideffected,
            self.sfx_list,
            self._repr,
            self._func,
        )

    def _withset(
        self,
        name=...,
        fn_kwarg=...,
        optional: _Optionals = ...,
        sideffected=...,
        sfx_list=...,
    ) -> Union["_Modifier", str]:
        """
        Make a new modifier with changes -- handle with care.

        :return:
             Delegates to :func:`_modifier`, so returns a plain string if no args left.
        """
        kw = {
            k: getattr(self, k) if v is ... else v
            for k, v in zip(
                "fn_kwarg optional sideffected sfx_list".split(),
                (fn_kwarg, optional, sideffected, sfx_list),
            )
        }
        if name is ...:
            name = self.sideffected or str(self)

        return _modifier(name=name, **kw)


def _modifier(
    name, fn_kwarg=None, optional: _Optionals = None, sideffected=None, sfx_list=(),
):
    """
    A :class:`_Modifier` factory that may return a plain str when no other args given.

    It decides the final `name` and `_repr` for the new modifier by matching
    the given inputs with the :data:`_modifier_cstor_matrix`.
    """
    formats = _match_modifier_args(name, fn_kwarg, optional, sideffected, sfx_list,)
    if not formats:
        # Make a plain string instead.
        return str(name)

    str_fmt, repr_fmt, func = formats
    fmt_args = {
        "dep": name,
        # avoid a sole ``?>`` symbol on pure optionals
        "kw": f">'{fn_kwarg}'" if fn_kwarg != name else ">" if not optional else "",
        "sfx": ", ".join(f"'{i}'" for i in sfx_list),
    }
    name = str_fmt % fmt_args
    _repr = repr_fmt % fmt_args

    return _Modifier(name, fn_kwarg, optional, sideffected, sfx_list, _repr, func)


def mapped(name: str, fn_kwarg: str = None):
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
                            needs=['a', 'name-in-inputs'(>'b')],
                            provides=['sum'],
                            fn='myadd')

        >>> graph = compose('mygraph', myadd)
        >>> graph
        Pipeline('mygraph', needs=['a', 'name-in-inputs'], provides=['sum'], x1 ops: myadd)

        >>> sol = graph.compute({"a": 5, "name-in-inputs": 4})['sum']
        >>> sol
        9

        .. graphtik::
    """
    # Must pass a truthy `fn_kwarg` bc cstor cannot not know its mapped.
    return _modifier(name, fn_kwarg=fn_kwarg or name)


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
        Pipeline('mygraph',
                         needs=['a', 'b'(?)],
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
                            needs=['a', 'quasi-real'(?>'b')],
                            provides=['sum'],
                            fn='myadd')

    """
    # Must pass a truthy `fn_kwarg` as cstor-matrix requires.
    return _modifier(name, fn_kwarg=fn_kwarg or name, optional=_Optionals.keyword)


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
        FunctionalOperation(name='addall',
                            needs=['a', 'b'(*), 'c'(*)],
                            provides=['sum'],
                            fn='addall')


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
    return _modifier(name, optional=_Optionals.vararg)


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
        Pipeline('mygraph',
                         needs=['a', 'b'(?)],
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
            1. Expected needs['b'(+)] to be non-str iterables!
            +++inputs: ['a', 'b']
            +++FunctionalOperation(name='enlist', needs=['a', 'b'(+)], provides=['sum'], fn='enlist')

    .. varargs-mistake-start
    .. Attention::
        To avoid user mistakes, *varargs* do not accept :class:`str` :term:`inputs`
        (though iterables):

        >>> graph(a=5, b="mistake")
        Traceback (most recent call last):
        ...
        graphtik.base.MultiValueError: Failed preparing needs:
            1. Expected needs['b'(+)] to be non-str iterables!
            +++inputs: ['a', 'b']
            +++FunctionalOperation(name='enlist',
                                   needs=['a', 'b'(+)],
                                   provides=['sum'],
                                   fn='enlist')

    .. varargs-mistake-end
    """
    return _modifier(name, optional=_Optionals.varargs)


def sfx(name, optional: bool = None):
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
        If modifications involve some input/output, prefer the :func:`.sfxed`
        modifier.

        You may still convey this relationships by including the dependency name
        in the string - in the end, it's just a string - but no enforcement of any kind
        will happen from *graphtik*, like:

        >>> from graphtik import sfx

        >>> sfx("price[sales_df]")
        sfx('price[sales_df]')

    **Example:**

    A typical use-case is to signify changes in some "global" context,
    outside `solution`:


        >>> from graphtik import operation, compose, sfx

        >>> @operation(provides=sfx("lights off"))  # sideffect names can be anything
        ... def close_the_lights():
        ...    pass

        >>> graph = compose('strip ease',
        ...     close_the_lights,
        ...     operation(
        ...         name='undress',
        ...         needs=[sfx("lights off")],
        ...         provides="body")(lambda: "TaDa!")
        ... )
        >>> graph
        Pipeline('strip ease',
                         needs=[sfx('lights off')],
                         provides=[sfx('lights off'), 'body'],
                         x2 ops: close_the_lights, undress)

        >>> sol = graph()
        >>> sol
        {'body': 'TaDa!'}

    .. graphtik::
        :name: sideffect

    .. note::
        Something has to provide a sideffect for a function needing it to execute -
        this could be another operation, like above, or the user-inputs;
        just specify some truthy value for the sideffect:

            >>> sol = graph.compute({sfx("lights off"): True})

        .. graphtik::

    """
    return _modifier(
        name, optional=_Optionals.keyword if optional else None, sideffected=name,
    )


def sfxed(
    dependency: str,
    sfx0: str,
    *sfx_list: str,
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

    Like :func:`.sfx` but annotating a *real* :term:`dependency` in the solution,
    allowing that dependency to be present both in :term:`needs` and :term:`provides`
    of the same function.

    **Example:**

    A typical use-case is to signify columns required to produce new ones in
    pandas dataframes (:gray:`emulated with dictionaries`):


        >>> from graphtik import operation, compose, sfxed

        >>> @operation(needs="order_items",
        ...            provides=sfxed("ORDER", "Items", "Prices"))
        ... def new_order(items: list) -> "pd.DataFrame":
        ...     order = {"items": items}
        ...     # Pretend we get the prices from sales.
        ...     order['prices'] = list(range(1, len(order['items']) + 1))
        ...     return order

        >>> @operation(
        ...     needs=[sfxed("ORDER", "Items"), "vat rate"],
        ...     provides=sfxed("ORDER", "VAT")
        ... )
        ... def fill_in_vat(order: "pd.DataFrame", vat: float):
        ...     order['VAT'] = [i * vat for i in order['prices']]
        ...     return order

        >>> @operation(
        ...     needs=[sfxed("ORDER", "Prices", "VAT")],
        ...     provides=sfxed("ORDER", "Totals")
        ... )
        ... def finalize_prices(order: "pd.DataFrame"):
        ...     order['totals'] = [p + v for p, v in zip(order['prices'], order['VAT'])]
        ...     return order

    To view all internal :term:`dependencies <dependency>`, enable DEBUG
    in :term:`configurations`:

        >>> from graphtik.config import debug_enabled

        >>> with debug_enabled(True):
        ...     finalize_prices
        FunctionalOperation(name='finalize_prices',
                            needs=[sfxed('ORDER', 'Prices'), sfxed('ORDER', 'VAT')],
                            op_needs=[sfxed('ORDER', 'Prices'), sfxed('ORDER', 'VAT')],
                            _fn_needs=['ORDER'],
                            provides=[sfxed('ORDER', 'Totals')],
                            op_provides=[sfxed('ORDER', 'Totals')],
                            _fn_provides=['ORDER'],
                            fn='finalize_prices')

    Notice that declaring a single *sideffected* with many items in `sfx_list`,
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
    return _modifier(
        dependency,
        optional=_Optionals.keyword if optional else None,
        fn_kwarg=dependency if optional and not fn_kwarg else fn_kwarg,
        sideffected=dependency,
        sfx_list=(sfx0, *sfx_list),
    )


def sfxed_vararg(dependency: str, sfx0: str, *sfx_list: str):
    """Like :func:`sideffected` + :func:`vararg`. """
    return _modifier(
        dependency,
        optional=_Optionals.vararg,
        sideffected=dependency,
        sfx_list=(sfx0, *sfx_list),
    )


def sfxed_varargs(dependency: str, sfx0: str, *sfx_list: str):
    """Like :func:`sideffected` + :func:`varargs`. """
    return _modifier(
        dependency,
        optional=_Optionals.varargs,
        sideffected=dependency,
        sfx_list=(sfx0, *sfx_list),
    )


def is_mapped(dep) -> Optional[str]:
    """
    Check if a :term:`dependency` is mapped (and get it).

    All non-varargish optionals are "mapped" (including sideffected ones).

    :return:
        the :attr:`fn_kwarg`
    """
    return getattr(dep, "fn_kwarg", None)


def is_optional(dep) -> bool:
    """
    Check if a :term:`dependency` is optional.

    Varargish & optional sideffects are included.

    :return:
        the :attr:`optional`
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
    return dep.optional in (_Optionals.vararg, _Optionals.vararg)


def is_sfx(dep) -> bool:
    """
    Check if a dependency is :term:`sideffects` or :term:`sideffected`.

    :return:
        the :attr:`sideffected`
    """
    return getattr(dep, "sideffected", None)


def is_pure_sfx(dep) -> bool:
    """Check if it is :term:`sideffects` but not a :term:`sideffected`."""
    return getattr(dep, "sideffected", None) and not getattr(dep, "sfx_list", None)


def is_sfxed(dep) -> bool:
    """Check if it is :term:`sideffected`."""
    return getattr(dep, "sideffected", None) and getattr(dep, "sfx_list", None)


def dependency(dep):
    """
    For non-sideffects, it coincides with str(), otherwise,
    the the pure-sideffect string or the existing :term:`sideffected` dependency
    stored in :attr:`sideffected`.
    """
    return str(dep) if is_sfx(dep) else dep.sideffected


def dep_renamed(dep, ren):
    """
    Renames `dep` as `ren` or call `ren`` (if callable) to decide its name,

    preserving any :func:`mapped` to old-name.

    For :term:`sideffected` it renames the dependency (not the *sfx-list*) --
    you have to do it that manually with a custom renamer-function, if ever
    the need arise.
    """
    if callable(ren):
        renamer = ren
    else:
        renamer = lambda n: ren

    if isinstance(dep, _Modifier):
        if is_sfx(dep):
            new_name = renamer(dep.sideffected)
            dep = dep._withset(name=new_name, sideffected=new_name)
        else:
            dep = dep._withset(name=renamer(str(dep)))
    else:  # plain string
        dep = renamer(dep)

    return dep


def dep_singularized(dep):
    """
    Yield one sideffected for each sfx in :attr:`.sfx_list`, or iterate `dep` in other cases.
    """
    return (
        (dep._withset(sfx_list=(s,)) for s in dep.sfx_list) if is_sfxed(dep) else (dep,)
    )


def dep_stripped(dep):
    """
    Return the :attr:`_Modifier.sideffected` if `dep` is :term:`sideffected`, `dep` otherwise,

    conveying all other properties of the original modifier to the stripped dependency.
    """
    if is_sfxed(dep):
        dep = dep._withset(name=dep.sideffected, sideffected=None, sfx_list=())
    return dep
