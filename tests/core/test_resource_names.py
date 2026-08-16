"""One rule for "a name is not a path", checked at all three call sites.

Three loaders turn a configured *name* into a file under a directory they
chose: the braille profile loader, the tactile profile loader, and
:func:`~brailix.core.models.paths.get_model_dir`. Each used to carry its own
guard, and the guards disagreed — the model one accepted ``C:foo``, which is
not a filename but the drive-relative path "``foo`` under the current
directory of drive C". Joined onto a ``models`` root on another drive it
*discards the root*, and ``get_model_dir`` then created the directory there.

They now share :func:`brailix.core.paths.validate_resource_component`, and
this is the test that keeps them sharing it: every case runs through all three
entry points, so a loader that grows its own copy again fails here rather than
drifting quietly until someone re-reviews it.

The Windows-specific inputs run **on every platform**. The rule is defined by
parsing as a Windows path deliberately (see the validator's docstring), so
Linux CI can pin the whole rule with :class:`~pathlib.PureWindowsPath`
semantics and none of it waits on a Windows runner.
"""

from __future__ import annotations

import os as _os
from collections.abc import Callable
from pathlib import Path, PureWindowsPath

import pytest

from brailix.backend.tactile.profile import load_tactile_profile
from brailix.core.config import load_profile
from brailix.core.errors import ConfigurationError
from brailix.core.models.paths import get_model_dir
from brailix.core.paths import validate_resource_component

# The three entry points, each "name in → resource out (or refusal)".
_LOADERS: dict[str, Callable[[str], object]] = {
    "load_profile": load_profile,
    "load_tactile_profile": load_tactile_profile,
    "get_model_dir": get_model_dir,
}
_LOADER_IDS = sorted(_LOADERS)


# Every case is (name, why it must be refused). The "why" is the point of the
# entry — a bare list of strings would not say what any of them is for.
_REFUSED: list[tuple[str, str]] = [
    ("", "empty"),
    ("..", "the parent directory"),
    (".", "the directory itself"),
    ("../secret", "traversal, posix separator"),
    ("..\\secret", "traversal, windows separator"),
    ("sub/profile", "nested, posix separator"),
    ("sub\\profile", "nested, windows separator"),
    ("/srv/app/private/settings", "absolute: the join keeps only this"),
    ("C:/foo", "absolute with a drive"),
    ("C:foo", "drive-RELATIVE: the join silently drops the root directory"),
    ("\\\\server\\share", "UNC share"),
    ("foo:bar", "NTFS alternate data stream of a file named foo"),
    ("CON", "windows device"),
    ("NUL", "windows device: opening it reads empty"),
    ("COM1", "windows device: a serial port"),
    ("nul.json", "windows device, extension does not disarm it"),
    ("LPT9", "windows device"),
    ("name.", "windows strips the dot: two names, one file"),
    ("name ", "windows strips the space: two names, one file"),
    ("na\x00me", "control character"),
    ("na\nme", "control character"),
    ("what?", "not a legal windows filename"),
    ("star*", "not a legal windows filename"),
]
_REFUSED_IDS = [f"{name!r}: {why}" for name, why in _REFUSED]

_ACCEPTED = ["cn_current", "cn_ncb", "ja_current", "generic", "hanlp", "g2pw"]


@pytest.fixture(autouse=True)
def _no_stray_directories(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """``get_model_dir`` creates ``models/<name>/`` under the cwd on the way
    through. A refusal must happen *before* that, and running in ``tmp_path``
    is what lets the test say so rather than assume it."""
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    monkeypatch.delenv("APPDATA", raising=False)
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdg"))
    monkeypatch.chdir(tmp_path)


@pytest.mark.parametrize("loader", _LOADER_IDS)
@pytest.mark.parametrize("name,why", _REFUSED, ids=_REFUSED_IDS)
def test_every_loader_refuses_the_same_names(
    loader: str, name: str, why: str
) -> None:
    with pytest.raises(ConfigurationError):
        _LOADERS[loader](name)


@pytest.mark.parametrize("name,why", _REFUSED, ids=_REFUSED_IDS)
def test_a_refusal_is_also_a_valueerror(name: str, why: str) -> None:
    """Both call sites documented ``ValueError`` before the rule was shared.
    ``ConfigurationError`` subclasses it, so callers catching the documented
    type still catch this."""
    with pytest.raises(ValueError):
        validate_resource_component(name, "profile")


def test_nothing_was_created_while_refusing(tmp_path: Path) -> None:
    """The proof rather than the message: a name that escapes ``models/``
    must be refused before ``mkdir`` runs, or the guard is decoration."""
    for name, _why in _REFUSED:
        with pytest.raises(ConfigurationError):
            get_model_dir(name)
    assert not (tmp_path / "models").exists()


@pytest.mark.parametrize("name", _ACCEPTED)
def test_ordinary_names_still_pass(name: str) -> None:
    assert validate_resource_component(name, "profile") == name


def test_a_drive_relative_name_really_would_have_escaped() -> None:
    """Why ``C:foo`` is on the list at all, stated as the path arithmetic
    rather than as an assertion about Windows.

    ``C:foo`` has a drive and no root, so joining it onto a directory on
    another drive keeps only the right-hand side — the ``models`` root is gone
    and the result names a directory under whatever the process's current
    directory on C: happens to be. Neither ``in``-style containment checks nor
    a ``..`` scan would have caught it; only refusing the name does.
    """
    root = PureWindowsPath("D:/app/models")
    assert str(root / "C:foo") == "C:foo"
    assert not str(root / "C:foo").startswith(str(root))


def test_the_accepted_names_are_the_ones_actually_shipped() -> None:
    """The rule has to keep the real resources loadable — a validator that
    refuses ``cn_current`` passes every test above and breaks the product."""
    # The autouse _no_stray_directories fixture chdirs to tmp_path; restoring
    # cwd here is required because mutmut's trampoline resolves source_paths
    # (relative to cwd) against a directory that no longer exists there.
    _saved_cwd = _os.getcwd()
    try:
        assert load_profile("cn_current").name == "cn_current"
        assert load_tactile_profile("generic") is not None
    finally:
        _os.chdir(_saved_cwd)
