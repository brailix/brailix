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

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
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


def register_asset(asset: ModelAsset) -> None:
    """Register an asset; later registrations replace earlier entries."""
    _assets[asset.name] = asset


def get_asset(name: str) -> ModelAsset | None:
    return _assets.get(name)


def all_assets() -> list[ModelAsset]:
    """Snapshot of all registered assets (stable name order)."""
    return [_assets[k] for k in sorted(_assets)]


def clear() -> None:
    """Drop all registrations and reset the download policy.

    Test-only — never call from app code.
    """
    _assets.clear()
    set_managed_download(False)


def set_managed_download(enabled: bool = True) -> None:
    """Opt into front-end-managed model downloading.

    When enabled, adapters that need a downloadable model raise
    :class:`~brailix.core.errors.ModelNotInstalledError` instead of
    triggering their backend's own auto-download, so a front-end's
    download manager can fetch the model under its own control (progress
    feedback, user consent). The default (disabled) lets each adapter
    auto-download on first use — what a standalone library user expects.
    """
    global _managed_download
    _managed_download = bool(enabled)


def is_managed_download() -> bool:
    """``True`` when a front-end has taken over model downloading."""
    return _managed_download


__all__ = (
    "ModelAsset",
    "all_assets",
    "clear",
    "get_asset",
    "is_managed_download",
    "register_asset",
    "set_managed_download",
)
