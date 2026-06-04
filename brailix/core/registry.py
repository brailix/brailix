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

from collections.abc import Callable

from brailix.core.errors import MissingExtraError


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

    __slots__ = ("subsystem", "protocol", "_loaders", "_cache", "_extras")

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
        """
        self._loaders[name] = loader
        self._cache.pop(name, None)
        if extra is not None:
            self._extras[name] = extra
        else:
            self._extras.pop(name, None)

    def unregister(self, name: str) -> None:
        self._loaders.pop(name, None)
        self._cache.pop(name, None)
        self._extras.pop(name, None)

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
        if name in self._cache:
            return self._cache[name]
        if name not in self._loaders:
            raise KeyError(
                f"no adapter named {name!r} registered for subsystem "
                f"{self.subsystem!r}; available: {sorted(self._loaders)}"
            )
        try:
            instance = self._loaders[name]()
        except ImportError as e:
            extra = self._extras.get(name)
            if extra:
                raise MissingExtraError(adapter=name, extra=extra) from e
            raise
        if self.protocol is not None and not isinstance(instance, self.protocol):
            raise TypeError(
                f"adapter {name!r} in subsystem {self.subsystem!r} does "
                f"not conform to protocol {self.protocol.__name__}"
            )
        self._cache[name] = instance
        return instance

    def has(self, name: str) -> bool:
        return name in self._loaders

    def names(self) -> list[str]:
        return sorted(self._loaders)

    def clear_cache(self) -> None:
        """Drop cached instances; loaders remain registered."""
        self._cache.clear()
