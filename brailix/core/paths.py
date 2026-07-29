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

from pathlib import Path, PureWindowsPath

from brailix.core.errors import ConfigurationError


def resolve_named_resource(
    directory: Path, name: str, kind: str, suffix: str = ".json"
) -> Path:
    """``directory/<name><suffix>`` for a ``name`` that is one filename.

    ``kind`` names the thing being loaded (``"profile"``, ``"tactile
    profile"``) and appears in the error, so a front-end can show which
    setting was rejected. Raises :class:`~brailix.core.errors.ConfigurationError`
    for anything that is not a single filename component — the error type both
    loaders already promise for a configuration value they cannot use.

    Parsed as a **Windows** path deliberately, on every platform: it treats
    ``\\`` as a separator as well as ``/``, so ``..\\secret`` is refused on
    Linux too. Refusing a name a given OS would have read as harmless costs
    nothing (no profile is called ``a/b``), while accepting one it reads as a
    path is the whole bug.

    Symlinks are still followed. The name is the untrusted part — an operator
    who can plant a symlink inside the profile directory already owns the
    process, and refusing them would break the ordinary deployment that links
    a profile in from a config-management directory.
    """
    if not name or PureWindowsPath(name).name != name:
        raise ConfigurationError(
            f"{kind} name must be a single file name, not a path: {name!r}"
        )
    return directory / f"{name}{suffix}"
