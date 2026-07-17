"""Generic lazy-loading registry for pluggable adapters.

Every pluggable subsystem (zh analyzer, pinyin resolver, math source
adapter, ...) maintains an instance of :class:`Registry`. Adapters
register a **loader callable**, not the instance itself, so that the
underlying third-party library (HanLP, g2pW, latex2mathml, ...) is
imported only when the adapter is first requested.

A loader that fails with :class:`ImportError` is reported as a
:class:`MissingExtraError` carrying the pip extras hint the user needs.

The registry can also validate that loaded instances conform to a
:func:`typing.runtime_checkable` Protocol, catching adapter authors
who forget required methods.
"""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager

from brailix.core.errors import MissingExtraError, UnknownAdapterError


def _is_internal_import_error(exc: ImportError) -> bool:
    """True when ``exc`` points at a *brailix* module — an adapter-internal
    import bug, not a missing optional third-party dependency.

    The distinguishing signal is the top-level package of the module the
    import failed on (``exc.name``): a ``brailix.*`` name means the adapter's
    own loader tried to import a renamed / mistyped internal module, or hit a
    circular import — a code bug the user cannot fix by installing an extra.
    Anything else (an external package such as ``hanlp`` / ``PIL``, or a name
    the interpreter didn't record) is left to the caller's ``extra`` handling,
    preserving the "missing optional dependency → MissingExtraError" behaviour.
    ``from x import y`` failures where ``y`` is absent set ``name`` to the
    module ``x`` too, so a self-referential ``from brailix... import`` cycle is
    caught the same way.
    """
    name = exc.name
    return name is not None and name.split(".")[0] == "brailix"


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
        # Monotonic count of registration-surface changes: every
        # ``register`` / ``unregister`` (and an ``overriding`` exit, which
        # restores the entry snapshot) bumps it. What a *name resolves to*
        # is part of a compilation's identity — the compilation fingerprint
        # folds every compilation-relevant registry's generation in, so
        # replacing an adapter under a name a live Pipeline uses advances
        # that pipeline's fingerprint instead of letting caches keep
        # serving output compiled by the previous implementation.
        # ``clear_cache`` does NOT bump: re-running the same loader yields
        # the same implementation, so nothing a cache keys on changed.
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
        self._lock = threading.RLock()

    def register(
        self,
        name: str,
        loader: Callable[[], T],
        *,
        extra: str | None = None,
    ) -> None:
        """Register an adapter under ``name``.

        ``loader`` is a zero-arg callable returning the adapter
        instance; it should perform any heavy imports inside its body
        so installation cost is paid only when the adapter is used.

        ``extra`` is the pip extras group that provides the required
        third-party dependency. If the loader raises ``ImportError``,
        the registry re-raises as :class:`MissingExtraError` pointing
        at ``extra``.

        Thread-safe: the loader swap, the stale-cache eviction and the
        ``extra`` update land together under the lock, so a concurrent
        :meth:`get` sees either the whole old registration or the whole new
        one — never a new loader still paired with the previous cached
        instance.
        """
        with self._lock:
            self._loaders[name] = loader
            self._cache.pop(name, None)
            if extra is not None:
                self._extras[name] = extra
            else:
                self._extras.pop(name, None)
            self._generation += 1

    def unregister(self, name: str) -> None:
        with self._lock:
            self._loaders.pop(name, None)
            self._cache.pop(name, None)
            self._extras.pop(name, None)
            self._generation += 1

    def get(self, name: str) -> T:
        """Load (or fetch cached) adapter by name.

        Raises
        ------
        KeyError
            If ``name`` is not registered.
        MissingExtraError
            If the loader fails with ``ImportError`` and an ``extra``
            was declared.
        TypeError
            If a protocol was specified and the loaded instance does
            not conform.
        """
        # Fast path: a cache hit needs no lock. ``dict.get`` is a SINGLE
        # atomic operation under the GIL, so it can't tear against a
        # concurrent lock-holding ``register`` / ``unregister`` /
        # ``clear_cache`` — it returns either a fully-constructed adapter or
        # ``None``. (A separate ``name in _cache`` test followed by
        # ``_cache[name]`` could race: the key can be popped between the two,
        # raising KeyError.) Adapters are never ``None``, so ``None``
        # unambiguously means "not cached — take the slow path".
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
            except ImportError as e:
                extra = self._extras.get(name)
                # An ``extra`` was declared, but not every ImportError from the
                # loader means the optional dependency is missing: the adapter
                # module itself may have a broken import — a renamed / mistyped
                # internal module, or a circular import — even when the extra IS
                # installed. Blindly wrapping those as "pip install
                # brailix[<extra>]" sends the user chasing a dependency that is
                # already there. Only wrap when the failure is NOT an adapter-
                # internal one; otherwise re-raise the original error with its
                # traceback intact so the real bug is visible.
                if extra is not None and not _is_internal_import_error(e):
                    raise MissingExtraError(
                        adapter=name,
                        extra=extra,
                        missing_module=e.name,
                    ) from e
                raise
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

    @property
    def generation(self) -> int:
        """Monotonic registration-surface version (see ``__init__``).

        Advances on every :meth:`register` / :meth:`unregister` and on an
        :meth:`overriding` exit; :meth:`get` and :meth:`clear_cache` never
        move it. Read lock-free — a single ``int`` attribute read is atomic,
        and a reader that races a bump simply sees the value from one side
        of it, which is exactly the point of a version counter.
        """
        return self._generation

    def clear_cache(self) -> None:
        """Drop cached instances; loaders remain registered."""
        with self._lock:
            self._cache.clear()

    @contextmanager
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
            saved = (
                dict(self._loaders),
                dict(self._cache),
                dict(self._extras),
            )
        try:
            if name is not None:
                if loader is None:
                    raise ValueError("overriding(name=...) requires a loader")
                self.register(name, loader, extra=extra)
            yield self
        finally:
            loaders, cache, extras = saved
            with self._lock:
                self._loaders = loaders
                self._cache = cache
                self._extras = extras
                # The restore is a registration-surface change like any
                # other (what a name resolves to may just have flipped
                # back), so it advances the generation too — conservative
                # for a no-op body, but a stale-cache risk never survives.
                self._generation += 1
