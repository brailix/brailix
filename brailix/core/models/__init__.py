"""Adapter-facing model-asset infrastructure (paths + asset registry).

Pinyin/jieba bundled dictionaries ride inside the wheel, but heavier
adapters (HanLP transformer weights, future g2pw, paddle, ...) are
too big to ship and must live in a portable-side ``models/``
directory the user populates separately.

This package owns the **adapter-side** pieces, which stay zero-deps
so headless / CLI use of ``brailix`` pulls in no heavy UI dependencies:

* :mod:`brailix.core.models.paths` — where ``models/`` lives in
  frozen vs. dev mode, and per-model subdirectory resolution.
* :mod:`brailix.core.models.asset_registry` — adapters' "I need this
  weight on disk under <path>" declaration.

The downloader-side counterparts (a JSON catalogue + an HTTP
downloader) pull in network I/O and only a model-manager front-end needs
them, so they live in a separate front-end layer rather than in
this core translation library.

Adapter code calls :func:`paths.get_model_dir` to find its weights
and :func:`asset_registry.register_asset` to announce itself; the
model-manager front-end walks the asset registry to populate its list.
Both sides stay decoupled — adapters don't import any front-end, and
a front-end doesn't import adapters.  By default adapters auto-download a
missing model on first use; a front-end that manages downloads itself
calls :func:`set_managed_download` so adapters defer to it instead.

This ``__init__`` re-exports the adapter-side public surface so callers
import from ``brailix.core.models`` rather than the concrete
``asset_registry`` / ``paths`` modules.
"""

from __future__ import annotations

from brailix.core.models.asset_registry import (
    ModelAsset,
    all_assets,
    clear,
    get_asset,
    is_managed_download,
    register_asset,
    set_managed_download,
)
from brailix.core.models.paths import get_model_dir, get_models_root

__all__ = (
    "ModelAsset",
    "all_assets",
    "clear",
    "get_asset",
    "is_managed_download",
    "register_asset",
    "set_managed_download",
    "get_model_dir",
    "get_models_root",
)
