"""Filesystem paths for downloadable model assets.

Uses the same frozen-vs-dev dispatch a packaged front-end applies,
but lives in the ``brailix`` package so adapter code can resolve
model directories on its own without importing any front-end layer.
The two helpers below are intentionally tiny — if a third shared
portable-aware utility shows up later, consolidate then.

Resolution rules:

* Frozen build (Nuitka standalone): ``<exe parent>/models/``.
  Sits next to the application executable so a copied portable bundle
  carries its downloaded weights along.
* Dev / source mode: ``<cwd>/models/``.  Predictable for
  developers running the application from the repo root;
  ``.gitignore`` already excludes ``models/`` so test weights don't
  get committed.

Both :func:`get_models_root` and :func:`get_model_dir` create the
directory on first call — adapters should be able to assume the
path exists, and a missing-but-creatable directory is never the
right error condition (the failure modes that matter are missing
*files inside it*, surfaced by the adapter's own
``_ensure_model_installed`` check raising
:class:`~brailix.core.errors.ModelNotInstalledError`).
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODELS_DIRNAME = "models"


def _is_frozen() -> bool:
    """``True`` when running from a PyInstaller / Nuitka standalone build.

    Nuitka doesn't set ``sys.frozen`` (only PyInstaller does); it sets a
    module-level ``__compiled__``.  Check both.
    """
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def _portable_root() -> Path:
    if _is_frozen():
        return Path(sys.executable).resolve().parent
    return Path.cwd()


def get_models_root() -> Path:
    """Return the ``models/`` directory at the portable bundle root.

    Created on first access.  Safe to call from any thread / process —
    :meth:`Path.mkdir` with ``exist_ok=True`` is idempotent.
    """
    root = _portable_root() / _MODELS_DIRNAME
    root.mkdir(parents=True, exist_ok=True)
    return root


def get_model_dir(name: str) -> Path:
    """Return ``models/<name>/`` for a registered model, creating it.

    ``name`` is the registry key (e.g. ``"hanlp"``, ``"g2pw"``); the
    caller is responsible for picking a stable, filesystem-safe
    identifier.  Empty or path-component names raise ``ValueError``
    rather than silently writing outside ``models/``.
    """
    if not name or "/" in name or "\\" in name or name in (".", ".."):
        raise ValueError(f"invalid model name: {name!r}")
    target = get_models_root() / name
    target.mkdir(parents=True, exist_ok=True)
    return target


__all__ = ("get_models_root", "get_model_dir")
