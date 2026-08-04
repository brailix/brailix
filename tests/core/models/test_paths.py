"""Tests for :mod:`brailix.core.models.paths`.

Mirrors the frozen/dev dispatch shape a packaged front-end uses,
but covers the ``models/`` resolution that adapter code (not just a
front-end) calls into.  The two failure surfaces worth pinning down:

* path goes to the right place in each mode (frozen → exe parent,
  dev → cwd),
* auto-mkdir is idempotent and rejects names that would escape the
  ``models/`` root,
* whichever candidate comes back can actually hold a file — and when
  neither can, the caller is told so here instead of finding out inside
  a model download.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

from brailix.core.errors import ConfigurationError
from brailix.core.models.paths import get_model_dir, get_models_root


class TestGetModelsRoot:
    def test_dev_mode_under_cwd(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        root = get_models_root()
        assert root == tmp_path / "models"
        assert root.is_dir()

    def test_frozen_mode_next_to_exe(self, tmp_path: Path) -> None:
        fake_exe = tmp_path / "App.exe"
        fake_exe.write_bytes(b"")
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.object(sys, "executable", str(fake_exe)):
            root = get_models_root()
        assert root == tmp_path / "models"
        assert root.is_dir()

    def test_nuitka_compiled_next_to_exe(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # Nuitka sets __compiled__, not sys.frozen — model dir must still
        # resolve next to the exe (mirrors the application's frozen-build detection).
        import brailix.core.models.paths as paths_mod

        fake_exe = tmp_path / "App.exe"
        fake_exe.write_bytes(b"")
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.setattr(paths_mod, "__compiled__", object(), raising=False)
        monkeypatch.setattr(sys, "executable", str(fake_exe))
        root = get_models_root()
        assert root == tmp_path / "models"
        assert root.is_dir()

    def test_idempotent_when_already_exists(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        (tmp_path / "models").mkdir()
        # Second call must not raise (mkdir(exist_ok=True) covers this).
        root = get_models_root()
        assert root.is_dir()

    def test_the_returned_directory_can_actually_hold_a_file(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The promise the name makes, checked the only way that holds on
        every platform: write into what comes back.

        ``os.access(W_OK)`` was the old check and answers a different question
        — on POSIX it ignores the search permission a directory also needs to
        take new entries, and on Windows it reads a read-only attribute and
        never sees the ACL that denies the write.
        """
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        root = get_models_root()
        (root / "weights.bin").write_bytes(b"x")
        assert (root / "weights.bin").read_bytes() == b"x"

    def test_the_write_probe_leaves_nothing_behind(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        root = get_models_root()
        get_models_root()  # a second call probes again
        assert list(root.iterdir()) == []

    def test_falls_back_to_user_data_when_portable_unwritable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # brailix imported into another app's read-only install (e.g. the
        # NVDA add-on, where sys.executable is nvda.exe under Program Files):
        # the portable root can't be created, so the models dir must fall
        # back to a per-user data directory rather than raise PermissionError
        # mid-compile.
        import brailix.core.models.paths as paths_mod

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"")  # a file in the way blocks mkdir of any child
        monkeypatch.setattr(paths_mod, "_portable_root", lambda: blocker / "sub")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
        root = get_models_root()
        assert root == tmp_path / "appdata" / "brailix" / "models"
        assert root.is_dir()

    def test_a_fallback_that_cannot_hold_a_file_is_not_returned(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The gap the shared check closes.

        A fallback that *exists* and accepts ``mkdir`` but refuses the first
        file written into it — a read-only home, a ``LOCALAPPDATA`` pointing at
        a drive that is no longer mounted the same way — used to be returned
        unexamined, because only the portable candidate was ever verified. The
        caller then failed inside a model download, several layers from the
        directory choice that caused it.
        """
        import tempfile as tempfile_mod

        import brailix.core.models.paths as paths_mod

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"")
        monkeypatch.setattr(paths_mod, "_portable_root", lambda: blocker / "sub")
        monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "appdata"))
        fallback = tmp_path / "appdata" / "brailix" / "models"

        real_probe = tempfile_mod.NamedTemporaryFile

        def refuse_the_fallback(*args, **kwargs):
            if Path(kwargs.get("dir", ".")) == fallback:
                raise PermissionError(13, "Permission denied")
            return real_probe(*args, **kwargs)

        monkeypatch.setattr(
            tempfile_mod, "NamedTemporaryFile", refuse_the_fallback
        )

        with pytest.raises(ConfigurationError) as excinfo:
            get_models_root()
        message = str(excinfo.value)
        assert str(blocker / "sub" / "models") in message, message
        assert str(fallback) in message, message

    def test_raises_when_neither_candidate_can_be_created(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """Both blocked: the error names both places rather than letting a
        bare ``PermissionError`` out of the fallback's ``mkdir``."""
        import brailix.core.models.paths as paths_mod

        blocker = tmp_path / "blocker"
        blocker.write_bytes(b"")  # a file in the way of every child path
        monkeypatch.setattr(paths_mod, "_portable_root", lambda: blocker / "sub")
        monkeypatch.setenv("LOCALAPPDATA", str(blocker / "appdata"))

        with pytest.raises(ConfigurationError) as excinfo:
            get_models_root()
        assert "no writable models directory" in str(excinfo.value)


class TestGetModelDir:
    def test_returns_named_subdir(self, tmp_path: Path, monkeypatch) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        d = get_model_dir("hanlp")
        assert d == tmp_path / "models" / "hanlp"
        assert d.is_dir()

    def test_creates_parent_models_dir(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        # models/ does not exist yet — must be created by the helper.
        assert not (tmp_path / "models").exists()
        get_model_dir("g2pw")
        assert (tmp_path / "models").is_dir()
        assert (tmp_path / "models" / "g2pw").is_dir()

    @pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "a\\b", "C:foo"])
    def test_rejects_path_escapes(
        self, bad: str, tmp_path: Path, monkeypatch
    ) -> None:
        """A spot check that this entry point is guarded at all. The *rule* is
        shared with the two profile loaders and lives in
        ``tests/core/test_resource_names.py``, which runs every case through
        all three — don't grow this list, grow that one."""
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            get_model_dir(bad)
