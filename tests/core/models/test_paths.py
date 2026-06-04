"""Tests for :mod:`brailix.core.models.paths`.

Mirrors the frozen/dev dispatch shape a packaged front-end uses,
but covers the ``models/`` resolution that adapter code (not just a
front-end) calls into.  The two failure surfaces worth pinning down:

* path goes to the right place in each mode (frozen → exe parent,
  dev → cwd),
* auto-mkdir is idempotent and rejects names that would escape the
  ``models/`` root.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

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

    @pytest.mark.parametrize("bad", ["", "..", ".", "a/b", "a\\b"])
    def test_rejects_path_escapes(
        self, bad: str, tmp_path: Path, monkeypatch
    ) -> None:
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        with pytest.raises(ValueError):
            get_model_dir(bad)
