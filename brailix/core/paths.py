"""Turning a configuration *name* into a file path, safely.

Two resource loaders in the library take a bare ``name`` and open
``<directory>/<name>.json``: the braille profile loader
(:mod:`brailix.core.config.loader`) and the tactile one
(:mod:`brailix.backend.tactile.profile`). A name is not a path — but
:class:`~pathlib.Path` will treat one as such without complaint.
``directory / "../secret"`` walks straight up out of ``directory``, and
``directory / "/srv/app/private/settings"`` discards ``directory``
altogether, because joining a ``Path`` with an absolute right-hand side
keeps only the right-hand side. Both loaders then read and parse
whatever file they landed on.

For a local CLI that is only the caller opening files they could have
opened anyway. ``SECURITY.md`` names embedding brailix in a service that
accepts untrusted input as a supported deployment, though, and there a
profile name is exactly the sort of value that arrives as a request
parameter — at which point the two spellings above are an out-of-tree
JSON read and a path prober.

So a name is checked to *be* a name before it is joined. The rule lives
in ``core`` rather than in either loader because both need it and
neither may import the other: the braille loader is core, and the
tactile one is a backend subsystem that deliberately keeps its own small
loader so the graphics vertical stays independently replaceable
(ARCHITECTURE#arch-layers). That is the same reasoning that put
``PERCENT_CHARS`` in :mod:`brailix.core.chars` — one fact, one owner,
consumed by layers that must not depend on each other — and the duplicate
here had already cost more than drift: the two loaders grew the *same
defect*, independently, because each wrote the join itself.
"""

from __future__ import annotations

from pathlib import PureWindowsPath as _PureWindowsPath
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.errors import ConfigurationError

if _TYPE_CHECKING:
    from pathlib import Path

# Names Windows resolves to a *device* rather than a file, with or without an
# extension: opening ``NUL.json`` opens the null device and reads empty, and
# ``COM1.json`` opens a serial port. None of them can be a resource, and the
# check runs on every platform so a profile name that works on Linux doesn't
# turn into a device read after deployment to Windows.
_WINDOWS_DEVICE_NAMES = frozenset(
    {"CON", "PRN", "AUX", "NUL"}
    | {f"COM{d}" for d in "123456789"}
    | {f"LPT{d}" for d in "123456789"}
)

# Characters Windows forbids in a filename outright. ``:`` is the interesting
# one: ``foo:bar`` passes the single-component test above (it *is* one path
# component) while naming the alternate data stream ``bar`` of a file ``foo``,
# so a name like that reads and writes somewhere the caller never asked for.
# The rest can't appear in a Windows filename at all; refusing them everywhere
# keeps one name meaning one thing on every platform.
_FORBIDDEN_CHARS = frozenset('<>:"|?*')


def validate_resource_component(name: str, kind: str) -> str:
    """Return ``name`` if it is usable as one filename component; else raise.

    The single owner of "a name is not a path" for every loader that turns a
    configured *name* into a file under a directory it chose. Three loaders
    wrote their own version of this and two of them had different holes, which
    is the argument for one function rather than three near-identical guards:
    :func:`resolve_named_resource` (braille and tactile profiles) and
    :func:`brailix.core.models.paths.get_model_dir` (an adapter's model
    directory) both call it.

    ``kind`` names the thing being loaded (``"profile"``, ``"model"``) and
    appears in the error so a front-end can say which setting was rejected.
    Raises :class:`~brailix.core.errors.ConfigurationError`, which is also a
    :class:`ValueError` — the type both call sites already documented.

    The rules, and what each is for:

    * **non-empty**, and a **single component when parsed as a Windows path**.
      That flavour is used on every platform because it is the stricter
      reading: it treats ``\\`` as a separator as well as ``/``, and it
      understands drives and UNC shares. ``..\\secret`` is refused on Linux
      too, and so is ``C:foo`` — which is not a filename at all but the
      *drive-relative* path "``foo`` under the current directory of drive C",
      and joining it onto a directory on another drive discards that directory
      entirely rather than nesting under it.
    * **no ``<>:"|?*``**, so a name cannot address an NTFS alternate data
      stream (see :data:`_FORBIDDEN_CHARS`).
    * **no Windows device name** as the stem (see
      :data:`_WINDOWS_DEVICE_NAMES`).
    * **no control characters**, which no resource has and several
      filesystems mangle.
    * **no trailing space or dot**: Windows strips both when resolving, so
      ``cn_current.`` and ``cn_current`` name the same file — one resource
      reachable under names that don't compare equal, which is a hole in any
      caller that decides access by comparing the name.

    Containment needs no separate check: a name that is one component, with no
    drive and no root, joins *under* the directory by construction. A
    :meth:`~pathlib.Path.resolve`-based check would additionally refuse a
    symlink out of the directory, which the loaders deliberately allow — an
    operator who can plant one inside the profile directory already owns the
    process, and refusing them would break the ordinary deployment that links
    a profile in from a config-management directory.
    """
    if not name or _PureWindowsPath(name).name != name:
        raise ConfigurationError(
            f"{kind} name must be a single file name, not a path: {name!r}"
        )
    bad = _FORBIDDEN_CHARS.intersection(name)
    if bad:
        raise ConfigurationError(
            f"{kind} name must not contain {''.join(sorted(bad))!r}: {name!r}"
        )
    if any(ch < " " or ch == "\x7f" for ch in name):
        raise ConfigurationError(
            f"{kind} name must not contain control characters: {name!r}"
        )
    if name[-1] in " .":
        raise ConfigurationError(
            f"{kind} name must not end with a space or a dot: {name!r}"
        )
    if name.partition(".")[0].upper() in _WINDOWS_DEVICE_NAMES:
        raise ConfigurationError(
            f"{kind} name must not be a reserved device name: {name!r}"
        )
    return name


def resolve_named_resource(
    directory: Path, name: str, kind: str, suffix: str = ".json"
) -> Path:
    """``directory/<name><suffix>`` for a ``name`` that is one filename.

    ``kind`` names the thing being loaded (``"profile"``, ``"tactile
    profile"``) and appears in the error, so a front-end can show which
    setting was rejected. The name is checked by
    :func:`validate_resource_component` — see there for the rules and the
    reasoning; it raises :class:`~brailix.core.errors.ConfigurationError`, the
    error type both loaders already promise for a configuration value they
    cannot use.
    """
    validate_resource_component(name, kind)
    return directory / f"{name}{suffix}"
