"""Generic lazy-loading registry for pluggable adapters.

Every pluggable subsystem (zh analyzer, pinyin resolver, math source
adapter, ...) maintains an instance of :class:`Registry`. Adapters
register a **loader callable**, not the instance itself, so that the
underlying third-party library (HanLP, g2pW, latex2mathml, ...) is
imported only when the adapter is first requested.

A loader whose optional dependency is genuinely absent — a
:class:`ModuleNotFoundError` — is reported as a :class:`MissingExtraError`
carrying the pip extras hint the user needs. Any other :class:`ImportError`
means the dependency is there and something about it is wrong, so it
propagates untouched (see :meth:`Registry.get`).

The registry can also validate that loaded instances conform to a
:func:`typing.runtime_checkable` Protocol, catching adapter authors
who forget required methods.
"""

from __future__ import annotations

import importlib.util as _importlib_util
import threading as _threading
from contextlib import contextmanager as _contextmanager
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.errors import MissingExtraError, UnknownAdapterError

if _TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Iterator


def _is_internal_import_error(exc: ModuleNotFoundError) -> bool:
    """True when ``exc`` points at a *brailix* module — an adapter-internal
    import bug, not a missing optional third-party dependency.

    The distinguishing signal is the top-level package of the module the
    import failed on (``exc.name``): a ``brailix.*`` name means the adapter's
    own loader tried to import a renamed / mistyped internal module — a code
    bug the user cannot fix by installing an extra. Anything else (an external
    package such as ``hanlp`` / ``PIL``, or a name the interpreter didn't
    record) is left to the caller's ``extra`` handling, preserving the
    "missing optional dependency → MissingExtraError" behaviour.
    """
    name = exc.name
    return name is not None and name.split(".")[0] == "brailix"


def _normalize_probe(
    name: str, probe: str | Iterable[str] | None
) -> tuple[str, ...]:
    """``probe`` as a tuple of module names, refusing anything else.

    A bare string is one module; any other iterable is taken as a sequence of
    them (a plugin computing ``probe`` from a list should not have to convert
    it). ``None`` and an empty sequence both mean "declares nothing", which
    :meth:`Registry.available` answers ``True`` to.

    The names are never imported here — :func:`importlib.util.find_spec` does
    that later, if anyone asks — so this is the last place a wrong *type* can
    be caught while the caller is still on the stack.
    """
    if probe is None:
        return ()
    if isinstance(probe, str):
        candidates: tuple[object, ...] = (probe,)
    else:
        try:
            candidates = tuple(probe)
        except TypeError:
            raise TypeError(
                f"probe for adapter {name!r} must be a module name or an "
                f"iterable of them, got {type(probe).__name__} ({probe!r})"
            ) from None
    for module in candidates:
        if not isinstance(module, str):
            raise TypeError(
                f"probe for adapter {name!r} must name modules as str, got "
                f"{type(module).__name__} ({module!r})"
            )
        if not module:
            raise ValueError(
                f"probe for adapter {name!r} contains an empty module name; "
                f"omit probe entirely to declare no third-party dependency"
            )
    return tuple(str(module) for module in candidates)


class Registry[T]:
    """Lazy-loading registry mapping a string name to an adapter
    instance.

    Parameters
    ----------
    subsystem:
        Human-readable name used in error messages (e.g.
        ``"zh_analyzer"``, ``"pinyin"``, ``"math.latex"``).
    protocol:
        Optional Protocol class. If provided, the registry verifies
        every newly-loaded instance with :func:`isinstance` and raises
        ``TypeError`` on mismatch.
    """

    __slots__ = (
        "subsystem",
        "protocol",
        "_loaders",
        "_cache",
        "_extras",
        "_probes",
        "_generation",
        "_lock",
    )

    def __init__(
        self,
        subsystem: str,
        protocol: type | None = None,
    ) -> None:
        self.subsystem = subsystem
        self.protocol = protocol
        self._loaders: dict[str, Callable[[], T]] = {}
        self._cache: dict[str, T] = {}
        self._extras: dict[str, str] = {}
        # Third-party module names per adapter, for :meth:`available` — see
        # :meth:`register`.
        self._probes: dict[str, tuple[str, ...]] = {}
        # Monotonic count of resolution-surface changes: every ``register``
        # / ``unregister`` / ``clear_cache`` (and an ``overriding`` exit,
        # which restores the entry snapshot) bumps it. What a *name resolves
        # to* is part of a compilation's identity — the compilation
        # fingerprint folds every compilation-relevant registry's generation
        # in, so replacing an adapter under a name a live Pipeline uses
        # advances that pipeline's fingerprint instead of letting caches keep
        # serving output compiled by the previous implementation.
        # ``get`` / ``has`` / ``names`` are pure reads and never bump.
        self._generation = 0
        # The one lock guarding EVERY access to the three dicts: the
        # lazy-load slow path (so concurrent first-access to one name can't
        # both run the loader and hand back different instances) AND every
        # mutation (:meth:`register` / :meth:`unregister` / :meth:`clear_cache`)
        # AND the ``_loaders`` reads (:meth:`has` / :meth:`names`). Registries
        # are module-level singletons a multi-threaded host may share, and a
        # plugin that registers at runtime races the compile threads calling
        # ``get`` — without a single lock a ``register`` popping ``_cache``
        # mid-``get`` leaves the loader / cache / extras views inconsistent.
        # Only the ``get`` FAST path stays lock-free (a single atomic
        # ``dict.get``). Reentrant so a loader that resolves another adapter on
        # the same registry can't self-deadlock.
        self._lock = _threading.RLock()

    def register(
        self,
        name: str,
        loader: Callable[[], T],
        *,
        extra: str | None = None,
        probe: str | Iterable[str] | None = None,
    ) -> None:
        """Register an adapter under ``name``.

        ``loader`` is a zero-arg callable returning the adapter
        instance; it should perform any heavy imports inside its body
        so installation cost is paid only when the adapter is used.

        ``extra`` is the pip extras group that provides the required
        third-party dependency. If the loader raises ``ImportError``,
        the registry re-raises as :class:`MissingExtraError` pointing
        at ``extra``.

        ``probe`` names the third-party module(s) the loader imports, so
        :meth:`available` can answer "is this installed?" **without running
        the loader**. It is separate from ``extra`` because the two are
        different namespaces and do not always agree — the ``g2pm`` extra
        installs the ``g2pM`` module — and guessing one from the other is how
        a probe silently reports every adapter missing. Omit it and
        :meth:`available` answers ``True``: an adapter that declares nothing
        is one this registry cannot rule out, and hiding it would be worse
        than offering it.

        Every argument is checked **here**, at the line that gets it wrong,
        because this is a third party's entry point into the library and the
        registration outlives the call by arbitrarily long. An adapter author
        who writes ``probe=(123,)`` (or a tuple built from a config file that
        yielded an ``int``) was storing a value nobody read until a front-end
        asked what was installed — and :meth:`available_names` walks *every*
        registration, so ``find_spec(123)`` raising ``AttributeError`` took
        down the whole engine list, not just the one adapter. One plugin's
        typo, and the picker cannot be built at all. The same argument covers
        the rest: a non-callable ``loader`` fails at first ``get``, an empty
        ``name`` registers an adapter nobody can select, and a non-string
        ``extra`` reaches the user as a broken "pip install" line.

        An empty ``probe`` tuple means what omitting it means — the adapter
        declares no third-party module — so it normalises to no probe rather
        than being rejected; a caller that computes ``probe=tuple(deps)``
        should not have to special-case an empty ``deps``.

        Thread-safe: the loader swap, the stale-cache eviction and the
        ``extra`` update land together under the lock, so a concurrent
        :meth:`get` sees either the whole old registration or the whole new
        one — never a new loader still paired with the previous cached
        instance. The checks run *before* the lock is taken: a rejected
        registration must leave the registry exactly as it was, and validating
        first is what guarantees that without a rollback path.
        """
        if not isinstance(name, str):
            raise TypeError(
                f"adapter name must be a str, got {type(name).__name__} "
                f"({name!r})"
            )
        if not name:
            raise ValueError("adapter name must not be empty")
        if not callable(loader):
            raise TypeError(
                f"loader for adapter {name!r} must be callable, got "
                f"{type(loader).__name__}"
            )
        if extra is not None and (not isinstance(extra, str) or not extra):
            raise ValueError(
                f"extra for adapter {name!r} must be a non-empty str naming a "
                f"pip extras group, got {extra!r}"
            )
        probes = _normalize_probe(name, probe)

        with self._lock:
            self._loaders[name] = loader
            self._cache.pop(name, None)
            if extra is not None:
                self._extras[name] = extra
            else:
                self._extras.pop(name, None)
            if probes:
                self._probes[name] = probes
            else:
                self._probes.pop(name, None)
            self._generation += 1

    def unregister(self, name: str) -> None:
        with self._lock:
            self._loaders.pop(name, None)
            self._cache.pop(name, None)
            self._extras.pop(name, None)
            self._probes.pop(name, None)
            self._generation += 1

    def get(self, name: str) -> T:
        """Load (or fetch cached) adapter by name.

        Raises
        ------
        KeyError
            If ``name`` is not registered.
        MissingExtraError
            If the loader fails with ``ModuleNotFoundError`` — the dependency
            is genuinely absent — and an ``extra`` was declared. Any other
            ``ImportError`` propagates unchanged.
        TypeError
            If a protocol was specified and the loaded instance does
            not conform, or if the loader returned ``None``.
        """
        # Fast path: a cache hit needs no lock. ``dict.get`` is a SINGLE
        # atomic operation under the GIL, so it can't tear against a
        # concurrent lock-holding ``register`` / ``unregister`` /
        # ``clear_cache`` — it returns either a fully-constructed adapter or
        # ``None``. (A separate ``name in _cache`` test followed by
        # ``_cache[name]`` could race: the key can be popped between the two,
        # raising KeyError.) An adapter is never ``None`` — the slow path
        # refuses one below rather than caching it — so ``None`` here
        # unambiguously means "not cached, take the slow path".
        cached = self._cache.get(name)
        if cached is not None:
            return cached
        # Slow path under the lock so two threads racing the *first* access to
        # one name don't both run the loader and hand out different instances
        # (breaking the a-is-b cache contract and double-paying a heavy import).
        with self._lock:
            if name in self._cache:  # another thread loaded it while we waited
                return self._cache[name]
            if name not in self._loaders:
                raise UnknownAdapterError(
                    f"no adapter named {name!r} registered for subsystem "
                    f"{self.subsystem!r}; available: {sorted(self._loaders)}"
                )
            try:
                instance = self._loaders[name]()
            # ``ModuleNotFoundError`` ONLY, not ``ImportError``, and the
            # difference is the whole classification. "pip install
            # brailix[<extra>]" is the right advice for exactly one failure:
            # the module isn't there. Its sibling — an ImportError raised
            # *from inside* a module that imported fine — means the package IS
            # installed and something about it is wrong: ``from transformers
            # import BertTokenizer`` after the symbol was removed, a circular
            # import, an adapter's own broken import. Those carry the outer
            # package's name in ``exc.name`` too, so wrapping every
            # ImportError sent a user with an up-to-date install off to
            # re-install a dependency they already had, and hid the real
            # error's traceback behind advice that could not work.
            # :class:`~brailix.core.errors.IncompatibleDependencyError` is the
            # answer for the *known* version breakages (an adapter raises it
            # explicitly, and an ``auto`` chain skips past it); an unexplained
            # one propagates, which is the same line
            # :data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS` draws —
            # a load failure nobody has characterised is a bug to see, not a
            # candidate to silently step over.
            except ModuleNotFoundError as e:
                extra = self._extras.get(name)
                # An ``extra`` was declared, but the missing module may still
                # be the adapter's own — a renamed / mistyped internal
                # module — even when the extra IS installed. Blindly wrapping
                # that as "pip install brailix[<extra>]" sends the user chasing
                # a dependency that is already there. Only wrap when the
                # failure is NOT an adapter-internal one; otherwise re-raise
                # the original error with its traceback intact so the real bug
                # is visible.
                if extra is not None and not _is_internal_import_error(e):
                    raise MissingExtraError(
                        adapter=name,
                        extra=extra,
                        missing_module=e.name,
                    ) from e
                raise
            # ``None`` is refused rather than cached, because the fast path
            # above reads a ``None`` from ``_cache.get`` as "not cached" — so a
            # loader returning one would send every later ``get`` down the
            # locked slow path to be handed the same nothing, and the comment
            # promising "adapters are never None" would be a claim with no
            # check behind it. It is also what every caller assumes: an
            # adapter is dereferenced immediately, and a ``None`` surfaces as
            # an ``AttributeError`` deep in a translation run, far from the
            # plugin that returned it. A protocol-configured registry already
            # rejected it (nothing conforms); this holds the same line when no
            # protocol was declared.
            if instance is None:
                raise TypeError(
                    f"loader for adapter {name!r} in subsystem "
                    f"{self.subsystem!r} returned None; a loader must return "
                    f"the adapter instance"
                )
            if self.protocol is not None and not isinstance(
                instance, self.protocol
            ):
                raise TypeError(
                    f"adapter {name!r} in subsystem {self.subsystem!r} does "
                    f"not conform to protocol {self.protocol.__name__}"
                )
            self._cache[name] = instance
            return instance

    def has(self, name: str) -> bool:
        with self._lock:
            return name in self._loaders

    def names(self) -> list[str]:
        # Under the lock so the snapshot is consistent with a concurrent
        # register / unregister, and can't observe a half-applied mutation.
        with self._lock:
            return sorted(self._loaders)

    def available(self, name: str) -> bool:
        """Whether ``name``'s third-party dependency is importable *now*.

        The cheap counterpart to :meth:`get`: it asks
        :func:`importlib.util.find_spec` about the modules the registration
        declared as its ``probe``, so it never runs the loader. That matters
        because running one is not a neutral question to ask — a segmentation
        engine's loader reads a hundred-megabyte model, and a front-end
        populating an engine picker would have loaded *every* engine to find
        out which ones it could offer.

        Answers ``True`` for an adapter that declares no probe (a built-in
        with no third-party dependency, or a plugin that did not say), because
        the honest answer there is "this registry cannot tell" and the safe
        reading of that is to keep offering it.

        A module that resolves only as a **namespace package** — a directory
        with no code in it — reads as unavailable. That is not a hypothetical
        shape: an application bundle that stops shipping an engine leaves the
        engine's *data* directory behind on an upgraded install, and a bare
        directory on ``sys.path`` is importable. The import then succeeds, the
        package has no ``__file__``, and every adapter that locates its data
        relative to one fails on ``None``. "The directory is there, the code
        is not" is exactly the question this method exists to ask.

        This is availability, not health: a probe that finds the module says
        nothing about whether the loader will succeed (an incompatible
        version, a model that fails to download). The loud failure at
        :meth:`get` is still the authority; this exists so a caller can avoid
        *offering* a choice it already knows cannot work.
        """
        with self._lock:
            if name not in self._loaders:
                return False
            modules = self._probes.get(name)
        if not modules:
            return True
        for module in modules:
            try:
                spec = _importlib_util.find_spec(module)
                # ``origin`` is None for a namespace package; a real module —
                # source, extension, or one Nuitka compiled into the binary
                # (verified: its loader reports the bundle path) — always has
                # one.
                if spec is None or spec.origin is None:
                    return False
            except (ImportError, ValueError):
                # find_spec imports parent packages to reach a submodule, so
                # a broken parent raises rather than answering — and a module
                # already in sys.modules with __spec__ unset raises
                # ValueError. Either way the dependency is not usable.
                return False
        return True

    def available_names(self) -> list[str]:
        """:meth:`names` filtered to the ones :meth:`available` accepts."""
        return [name for name in self.names() if self.available(name)]

    @property
    def generation(self) -> int:
        """Monotonic resolution-surface version (see ``__init__``).

        Advances on every :meth:`register` / :meth:`unregister` /
        :meth:`clear_cache` and on an :meth:`overriding` exit; the pure reads
        (:meth:`get` / :meth:`has` / :meth:`names`) never move it. Read
        lock-free — a single ``int`` attribute read is atomic, and a reader
        that races a bump simply sees the value from one side of it, which is
        exactly the point of a version counter.
        """
        return self._generation

    def clear_cache(self) -> None:
        """Drop cached instances; loaders remain registered.

        Advances :attr:`generation`, because "the same loader yields the same
        implementation" is not true in general: an ``auto`` adapter picks its
        delegate by probing what is *currently* available (installed extras,
        downloaded models) and memoises that choice on the instance, so
        discarding the instance can genuinely change what the name resolves
        to — the very thing the fingerprint exists to notice. The bump is
        conservative (a clear with no behaviour change still invalidates
        caches), which is the safe direction: over-invalidation costs a
        recompile, under-invalidation serves braille compiled by an
        implementation that is no longer in play.

        This does not turn the fingerprint into an environment stamp — what
        an ``auto`` name probes to *without* a clear is still outside its
        coverage (see :mod:`brailix.pipeline._fingerprint`). It removes the
        one path where this class's own API silently changed the answer.
        """
        with self._lock:
            self._cache.clear()
            self._generation += 1

    @_contextmanager
    def overriding(
        self,
        name: str | None = None,
        loader: Callable[[], T] | None = None,
        *,
        extra: str | None = None,
    ) -> Iterator[Registry[T]]:
        """Temporarily install an adapter, restoring the prior state on exit.

        The test-support replacement for the ``register(...); try: ...;
        finally: unregister(...)`` dance: it snapshots the registry's
        registrations on entry and restores them on exit, so a temporarily
        installed (or removed) adapter never leaks into a later test — even
        when the body raises.

        With ``name`` (and ``loader``) it registers that one adapter for the
        block. With no arguments it only snapshots, so the body may
        ``register`` / ``unregister`` several names and all are rolled back::

            with segmenter_registry.overriding("zh", ZhSegmenter):
                ...  # "zh" is gone again out here

            with segmenter_registry.overriding():
                segmenter_registry.register("zh", ZhSegmenter)
                segmenter_registry.register("custom", CustomSegmenter)
                ...  # both gone out here

        Concurrency: the lock is taken only to snapshot on entry and to
        restore on exit — it is **not** held across the ``yield``, so worker
        threads spawned inside the block can use this registry freely
        (holding the RLock across the body would deadlock any thread but
        the owner). The flip side of snapshot/restore: exit puts back the
        ENTRY state verbatim, so a registration another thread makes
        *during* the block is rolled back with everything else. That is the
        intended test-support semantics — don't wrap an ``overriding()``
        block around code that must observe concurrent production
        registrations.
        """
        with self._lock:
            # Every per-name dict, ``_probes`` included: ``register`` replaces
            # a registration WHOLE, so a temporary one declaring no probe
            # clears the real adapter's. Leaving it out of the snapshot let a
            # test that swapped an engine for a raising stub hand the next
            # test a registry where that engine reported itself installed.
            saved = (
                dict(self._loaders),
                dict(self._cache),
                dict(self._extras),
                dict(self._probes),
            )
        try:
            if name is not None:
                if loader is None:
                    raise ValueError("overriding(name=...) requires a loader")
                self.register(name, loader, extra=extra)
            yield self
        finally:
            loaders, cache, extras, probes = saved
            with self._lock:
                self._loaders = loaders
                self._cache = cache
                self._extras = extras
                self._probes = probes
                # The restore is a registration-surface change like any
                # other (what a name resolves to may just have flipped
                # back), so it advances the generation too — conservative
                # for a no-op body, but a stale-cache risk never survives.
                self._generation += 1
