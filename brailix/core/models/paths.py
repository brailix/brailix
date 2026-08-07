"""Filesystem paths for downloadable model assets.

Uses the same frozen-vs-dev dispatch a packaged front-end applies,
but lives in the ``brailix`` package so adapter code can resolve
model directories on its own without importing any front-end layer.

Resolution rules:

* Frozen build (Nuitka standalone): ``<exe parent>/models/``.
  Sits next to the application executable so a copied portable bundle
  carries its downloaded weights along.
* Dev / source mode: ``<cwd>/models/``.  Predictable for
  developers running the application from the repo root;
  ``.gitignore`` already excludes ``models/`` so test weights don't
  get committed.
* Fallback when the chosen root is **not writable**: a per-user data
  directory (``%LOCALAPPDATA%/brailix/models`` on Windows, an
  XDG / home path elsewhere).  This is the case when brailix is
  imported into *another* application's frozen interpreter — e.g. the
  NVDA add-on, where ``sys.executable`` is ``nvda.exe`` under
  ``C:/Program Files`` — or installed read-only.  Without it,
  resolving a model dir would raise ``PermissionError`` mid-compile.

Both :func:`get_models_root` and :func:`get_model_dir` create the
directory on first call — adapters should be able to assume the
path exists, and a missing-but-creatable directory is never the
right error condition (the failure modes that matter are missing
*files inside it*, surfaced by the adapter's own
``_ensure_model_installed`` check raising
:class:`~brailix.core.errors.ModelNotInstalledError`).

Both candidates are checked the same way — by writing a file into
them — and if neither can hold one, that *is* an error condition:
:class:`~brailix.core.errors.ConfigurationError` naming both paths,
raised here where the choice was made rather than later inside a
download.

:func:`get_model_dir` runs that same check on the model's **own**
directory rather than inheriting the root's answer: a writable
``models/`` says nothing about a ``models/<name>/`` that is already
there and read-only.
"""

from __future__ import annotations

import os as _os
import sys as _sys
import tempfile as _tempfile
from pathlib import Path as _Path

from brailix.core.errors import ConfigurationError
from brailix.core.paths import validate_resource_component

_MODELS_DIRNAME = "models"


def _is_frozen() -> bool:
    """``True`` when running from a PyInstaller / Nuitka standalone build.

    Nuitka doesn't set ``sys.frozen`` (only PyInstaller does); it sets a
    module-level ``__compiled__``.  Check both.
    """
    return bool(getattr(_sys, "frozen", False)) or "__compiled__" in globals()


def _portable_root() -> _Path:
    if _is_frozen():
        return _Path(_sys.executable).resolve().parent
    return _Path.cwd()


def _user_data_root() -> _Path:
    """Per-user, writable base directory for brailix assets.

    Used as the fallback when the portable root isn't writable. Honors
    ``LOCALAPPDATA`` / ``APPDATA`` (Windows) then ``XDG_DATA_HOME``,
    finally ``~/.local/share``.
    """
    win = _os.environ.get("LOCALAPPDATA") or _os.environ.get("APPDATA")
    if win:
        return _Path(win) / "brailix"
    xdg = _os.environ.get("XDG_DATA_HOME")
    if xdg:
        return _Path(xdg) / "brailix"
    return _Path.home() / ".local" / "share" / "brailix"


def _make_usable_dir(path: _Path) -> bool:
    """Create ``path`` (with parents) and report whether a model can be
    written into it.

    Returns ``False`` instead of raising when the directory can't be
    created (read-only parent, a file in the way) so the caller can fall
    back to another location.

    The question is "can a downloader put a file here", and the only answer
    that is true on every platform this ships to is **writing one**. Asking
    :func:`os.access` for ``W_OK`` instead answers a narrower question on
    POSIX — creating an entry in a directory needs search permission too, so
    a ``0o600`` directory passes and every later ``open()`` in it fails — and
    barely answers it at all on Windows, the product's own target, where the
    check reads a read-only *attribute* and is blind to the ACL that actually
    denies the write. A corporate install under ``C:/Program Files`` is
    exactly that case: ``W_OK`` says yes, the first model download says
    ``PermissionError``, and the fallback that exists for precisely this
    situation never runs.

    The probe file is deleted on close (that is
    :class:`~tempfile.NamedTemporaryFile`'s own contract, and its name is
    unique per call, so concurrent callers — threads, or a second process
    started while the first is probing — cannot collide or delete each
    other's).
    """
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return False
    try:
        with _tempfile.NamedTemporaryFile(dir=path, prefix=".brailix-probe-"):
            pass
    except OSError:
        return False
    return True


def _candidate_roots() -> tuple[_Path, _Path]:
    """The two ``models/`` locations, in preference order.

    One list, read by both entry points below, so "where could a model go"
    is stated once. :func:`get_models_root` probes the roots themselves;
    :func:`get_model_dir` probes ``<root>/<name>`` under each — a distinction
    that matters, since a writable root does not make every directory already
    inside it writable.
    """
    return (
        _portable_root() / _MODELS_DIRNAME,
        _user_data_root() / _MODELS_DIRNAME,
    )


def get_models_root() -> _Path:
    """Return a writable ``models/`` directory, creating it on first call.

    Prefers the portable bundle root (next to the executable when frozen,
    else the cwd) so a copied portable bundle carries its weights. Falls
    back to a per-user data directory when that root is read-only.

    Both candidates go through the *same* check, which is what makes the
    return value mean what this line says. The fallback used to be a bare
    ``mkdir`` whose result was returned unexamined: when it was the one that
    could not be written to — a read-only home, ``LOCALAPPDATA`` pointed at a
    stale drive — the caller got a path back, believed the promise above, and
    failed later inside a model download, with an error naming a weights file
    rather than the directory choice that produced it.

    Raises :class:`~brailix.core.errors.ConfigurationError` naming both
    candidates when neither can hold a file. Nothing downstream can proceed
    without somewhere to put a model, and stating which two places were tried
    is the difference between a fixable report and a bare ``PermissionError``.

    Safe to call from any thread / process — :meth:`Path.mkdir` with
    ``exist_ok=True`` is idempotent, and the write probe is per-call.
    """
    portable, fallback = _candidate_roots()
    if _make_usable_dir(portable):
        return portable
    if _make_usable_dir(fallback):
        return fallback
    raise ConfigurationError(
        f"no writable models directory: neither {portable} (beside the "
        f"application / working directory) nor {fallback} (the per-user data "
        f"directory) can hold a file. Point LOCALAPPDATA / XDG_DATA_HOME at a "
        f"writable location, or run from a directory this process may write to."
    )


def get_model_dir(name: str) -> _Path:
    """Return ``models/<name>/`` for a registered model, creating it.

    ``name`` is the registry key (e.g. ``"hanlp"``, ``"g2pw"``); the
    caller is responsible for picking a stable, filesystem-safe
    identifier.  A name that is not a single filename component raises
    ``ValueError`` rather than silently creating a directory outside
    ``models/``.

    The check is :func:`brailix.core.paths.validate_resource_component`, the
    same one the braille and tactile profile loaders use. It used to be four
    conditions written out here, and they let ``C:foo`` through — not a
    filename but the drive-relative path "``foo`` under the current directory
    of drive C". Joining that onto a ``models`` root on any other drive
    *discards the root*, so the ``mkdir`` below created the directory
    somewhere else entirely, which is exactly what the guard existed to
    prevent. Sharing the check is what stops the two copies drifting again;
    :mod:`brailix.core.paths` explains why the rule lives in ``core``.

    ``ConfigurationError`` is what propagates, and it subclasses
    :class:`ValueError`, so the documented contract above is unchanged.

    The write probe is on ``models/<name>/`` **itself**, not on the root it
    sits in, because "the root can hold a file" does not answer for a
    directory that is already there: an aborted download's leftovers, a stray
    ACL, an admin who locked one engine's folder. Deriving the answer from
    :func:`get_models_root` meant this returned that directory anyway and the
    failure surfaced later, inside the download, as a ``PermissionError``
    naming a weights file — the exact shape the root probe was added to
    remove, one level down.

    Same two candidates as the root, in the same order, so one unusable
    model directory falls back to the per-user data location instead of
    failing outright; the error naming both is reserved for when neither can
    hold a file.
    """
    validate_resource_component(name, "model")
    candidates = tuple(root / name for root in _candidate_roots())
    for target in candidates:
        if _make_usable_dir(target):
            return target
    portable, fallback = candidates
    raise ConfigurationError(
        f"no writable directory for model {name!r}: neither {portable} "
        f"(beside the application / working directory) nor {fallback} (the "
        f"per-user data directory) can hold a file. A directory that already "
        f"exists may be read-only; remove it or fix its permissions, or point "
        f"LOCALAPPDATA / XDG_DATA_HOME at a writable location."
    )


__all__ = ("get_models_root", "get_model_dir")
