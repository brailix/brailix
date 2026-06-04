"""Tests for :mod:`brailix.core.models.asset_registry`."""

from __future__ import annotations

from pathlib import Path

import pytest

from brailix.core.models.asset_registry import (
    ModelAsset,
    all_assets,
    clear,
    get_asset,
    is_managed_download,
    register_asset,
    set_managed_download,
)


@pytest.fixture(autouse=True)
def _isolated_registry():
    """Snapshot + restore so test ordering can't cross-contaminate."""
    # We re-import the module's dict to snapshot it, then reset after.
    from brailix.core.models import asset_registry as ar

    snap = dict(ar._assets)
    snap_managed = ar._managed_download
    try:
        ar._assets.clear()
        ar._managed_download = False
        yield
    finally:
        ar._assets.clear()
        ar._assets.update(snap)
        ar._managed_download = snap_managed


def _asset(name: str, install_dir: Path, key: str = "model.x.display_name") -> ModelAsset:
    return ModelAsset(
        name=name,
        display_name_key=key,
        install_dir_factory=lambda: install_dir,
    )


class TestRegisterAndLookup:
    def test_register_then_get(self, tmp_path: Path) -> None:
        a = _asset("m1", tmp_path / "m1")
        register_asset(a)
        assert get_asset("m1") is a

    def test_get_missing_returns_none(self) -> None:
        assert get_asset("nope") is None

    def test_reregister_replaces(self, tmp_path: Path) -> None:
        register_asset(_asset("m1", tmp_path / "old", key="k.old"))
        register_asset(_asset("m1", tmp_path / "new", key="k.new"))
        result = get_asset("m1")
        assert result is not None
        assert result.display_name_key == "k.new"

    def test_all_assets_returns_name_sorted(self, tmp_path: Path) -> None:
        register_asset(_asset("z", tmp_path / "z"))
        register_asset(_asset("a", tmp_path / "a"))
        register_asset(_asset("m", tmp_path / "m"))
        names = [a.name for a in all_assets()]
        assert names == ["a", "m", "z"]

    def test_clear_drops_all(self, tmp_path: Path) -> None:
        register_asset(_asset("m", tmp_path / "m"))
        clear()
        assert all_assets() == []


class TestInstallDir:
    def test_factory_called_lazily(self, tmp_path: Path) -> None:
        """Factory must not run at registration time — adapters
        register at import, when get_model_dir() in cwd would create
        a directory in the wrong place."""
        calls: list[int] = []

        def factory() -> Path:
            calls.append(1)
            return tmp_path / "lazy"

        asset = ModelAsset(name="m", display_name_key="k", install_dir_factory=factory)
        register_asset(asset)
        assert calls == []  # factory NOT called yet
        asset.install_dir()
        assert calls == [1]


class TestIsInstalled:
    def test_false_when_missing(self, tmp_path: Path) -> None:
        asset = _asset("m", tmp_path / "absent")
        register_asset(asset)
        assert asset.is_installed() is False

    def test_false_when_empty(self, tmp_path: Path) -> None:
        d = tmp_path / "empty"
        d.mkdir()
        asset = _asset("m", d)
        register_asset(asset)
        assert asset.is_installed() is False

    def test_true_when_has_files(self, tmp_path: Path) -> None:
        d = tmp_path / "full"
        d.mkdir()
        (d / "weights.bin").write_bytes(b"x")
        asset = _asset("m", d)
        register_asset(asset)
        assert asset.is_installed() is True


class TestManagedDownload:
    """The download-policy seam: default auto-download vs. front-end-managed."""

    def test_default_is_unmanaged(self) -> None:
        # Library default: adapters auto-download a missing model on first use.
        assert is_managed_download() is False

    def test_opt_in_then_out(self) -> None:
        set_managed_download(True)
        assert is_managed_download() is True
        set_managed_download(False)
        assert is_managed_download() is False

    def test_default_arg_enables(self) -> None:
        set_managed_download()
        assert is_managed_download() is True

    def test_clear_resets_to_unmanaged(self) -> None:
        set_managed_download(True)
        clear()
        assert is_managed_download() is False
