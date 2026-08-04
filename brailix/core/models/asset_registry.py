"""Adapter-owned model asset registry.

Each adapter that needs a downloadable model (today: HanLP; later:
g2pw, paddle, ...) registers a :class:`ModelAsset` at module import
time.  A model-manager front-end walks :func:`all_assets` to populate
its table — it never imports adapters directly, so adding a new
downloadable model is a single ``register_asset`` call in the
adapter module + a registry JSON entry, no front-end edits required.

The :attr:`ModelAsset.name` field links the asset to the
download-catalogue entry of the same key, so a model-manager front-end can
pair "where it goes" (asset) with "where to fetch it from" (entry).

``install_dir_factory`` is a zero-arg callable rather than a baked
:class:`Path` so the path is resolved lazily — adapters can register
at module-import time without triggering :func:`get_model_dir`'s
side effect of creating directories in the wrong cwd.
"""

from __future__ import annotations

import threading as _threading
from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


@_dataclass(frozen=True)
class ModelAsset:
    """Description of a downloadable model owned by an adapter.

    ``display_name_key`` is an i18n key for the display label — kept as a
    key rather than the localized string so registration can happen at
    Python import time, before any translator is initialised.
    """

    name: str
    display_name_key: str
    install_dir_factory: Callable[[], Path]

    def install_dir(self) -> Path:
        """Resolve the install directory.  May create the parent ``models/<...>/``."""
        return self.install_dir_factory()

    def is_installed(self) -> bool:
        """``True`` when the install dir exists and is non-empty.

        Mirrors the check in adapters' ``_ensure_model_installed`` —
        a present-but-empty dir from an interrupted download counts
        as "not installed" so a front-end lets the user re-download.
        """
        d = self.install_dir()
        return d.is_dir() and any(d.iterdir())


_assets: dict[str, ModelAsset] = {}

# Download policy. Default: adapters auto-download a missing model on first
# use (standalone library behaviour). A front-end that ships its own
# download manager calls :func:`set_managed_download` so adapters instead
# raise :class:`~brailix.core.errors.ModelNotInstalledError` and defer the
# fetch to that manager (progress feedback, user consent, etc.).
_managed_download = False

# Guards both of the above, for the same reason
# :class:`brailix.core.registry.Registry` guards its dicts — and because
# ARCHITECTURE#arch-boundaries names this table among the process-level
# assembly surfaces whose reads and writes are safe against a concurrent
# compile. It was the one that wasn't.
#
# The race is reachable, not theoretical. Registration is not the import-time
# event it reads as: adapters ``register_asset`` at *module* import, and
# adapter modules are imported lazily by the registry's ``get`` — on whichever
# thread is compiling. A model-manager front-end refreshing its table calls
# ``all_assets`` from its own thread, so it runs concurrently with the first
# document that selects HanLP.
#
# What that costs without the lock, stated as measured rather than as feared:
#
# * ``all_assets`` was two steps — ``sorted(_assets)``, then an index per key.
#   A key disappearing between them raises ``KeyError`` (reproduced in a few
#   hundred thousand iterations). Nothing here removes entries, but the
#   snapshot-and-restore fixture this module recommends does, so the window is
#   open in-process.
# * Insert-only, the two-step read did NOT fail in a 10s hammer: CPython
#   materialises the key list in one bytecode under the GIL. That is an
#   implementation detail of one build, not a property to document a guarantee
#   on — a free-threaded interpreter (PEP 703, available from the 3.13 brailix
#   targets) removes exactly it.
#
# Reentrant to match ``Registry``: an ``install_dir_factory`` is caller-supplied
# and could reach back into this module.
_lock = _threading.RLock()


def register_asset(asset: ModelAsset) -> None:
    """Register an asset; later registrations replace earlier entries."""
    with _lock:
        _assets[asset.name] = asset


def get_asset(name: str) -> ModelAsset | None:
    # ``dict.get`` is a single atomic operation under the GIL, so it cannot
    # tear against a concurrent ``register_asset``; it returns either a fully
    # constructed asset or ``None``. Same lock-free read the registry's cache
    # hit takes, for the same reason.
    return _assets.get(name)


def all_assets() -> list[ModelAsset]:
    """Snapshot of all registered assets (stable name order).

    A real snapshot: taken under the lock, so the list is consistent with a
    registration landing mid-call rather than a view of a dict being mutated.
    :class:`ModelAsset` is frozen, so what the caller holds afterwards cannot
    be changed underneath it either.
    """
    with _lock:
        return [_assets[k] for k in sorted(_assets)]


# (There is deliberately no ``clear()`` reset helper. A test that needs a
# clean registry snapshots ``_assets`` / ``_managed_download`` and restores
# them in a fixture — restoring beats wiping, since the real registrations
# come from adapter imports that only happen once per process. A global
# "drop everything" function on a stable facade would be an app-reachable
# way to un-register those.)


def set_managed_download(enabled: bool = True) -> None:
    """Opt into front-end-managed model downloading.

    When enabled, adapters that need a downloadable model raise
    :class:`~brailix.core.errors.ModelNotInstalledError` instead of
    triggering their backend's own auto-download, so a front-end's
    download manager can fetch the model under its own control (progress
    feedback, user consent). The default (disabled) lets each adapter
    auto-download on first use — what a standalone library user expects.

    Process-level policy, deliberately: it changes the behaviour of every
    adapter in the interpreter, not of one run. A front-end sets it once at
    startup — it is not a per-compile option, and a multi-tenant host cannot
    use it to give two tenants different policies
    (ARCHITECTURE#arch-boundaries).
    """
    global _managed_download
    with _lock:
        _managed_download = bool(enabled)


def is_managed_download() -> bool:
    """``True`` when a front-end has taken over model downloading."""
    # Lock-free: reading a module-level ``bool`` is a single atomic operation
    # under the GIL, and adapters call this on the compile hot path. The write
    # takes the lock so a reader sees one side of the flip or the other.
    return _managed_download


__all__ = (
    "ModelAsset",
    "all_assets",
    "get_asset",
    "is_managed_download",
    "register_asset",
    "set_managed_download",
)
