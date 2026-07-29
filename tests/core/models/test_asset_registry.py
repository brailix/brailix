"""Tests for :mod:`brailix.core.models.asset_registry`."""

from __future__ import annotations

from pathlib import Path

import pytest

from brailix.core.models.asset_registry import (
    ModelAsset,
    all_assets,
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


class TestConcurrencyContract:
    """ARCHITECTURE#arch-boundaries lists this table among the process-level
    assembly surfaces whose reads and writes are safe against a concurrent
    compile. It was the only one with no lock behind that promise.

    The promise is not academic: adapters call ``register_asset`` at *module*
    import, and adapter modules are imported lazily by the adapter registry's
    ``get`` — on the compiling thread. So a model-manager front-end refreshing
    its table calls ``all_assets`` from its own thread, concurrently with the
    first document that selects HanLP. ``all_assets`` was two steps, ``sorted``
    then an index per key, and the fixture pattern at the top of this file —
    clear, then restore — is exactly a removal between them.

    These check that the mutators and the snapshot take the lock, by holding
    it and watching them block, rather than by racing threads and hoping to
    lose. A racing test that passes proves nothing; this one can only fail
    when the lock is genuinely absent.
    """

    # Long enough that a loaded machine isn't mistaken for a bug (the wait is
    # for a thread that should finish immediately once unblocked).
    _UNBLOCKED = 10.0
    # Short, and only ever read as "still blocked". A slow machine makes this
    # *more* likely to hold, never less — it cannot produce a false failure.
    _BLOCKED = 0.2

    @staticmethod
    def _call_in_thread(fn):
        import threading

        done = threading.Event()

        def run() -> None:
            fn()
            done.set()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        return thread, done

    @pytest.mark.parametrize(
        "operation",
        ["register_asset", "all_assets", "set_managed_download"],
    )
    def test_the_guarded_operations_wait_for_the_lock(
        self, operation: str, tmp_path: Path
    ) -> None:
        from brailix.core.models import asset_registry as ar

        calls = {
            "register_asset": lambda: register_asset(_asset("m", tmp_path)),
            "all_assets": all_assets,
            "set_managed_download": lambda: set_managed_download(True),
        }
        with ar._lock:
            thread, done = self._call_in_thread(calls[operation])
            assert not done.wait(self._BLOCKED), (
                f"{operation} completed while another thread held the lock — "
                f"it is not taking it"
            )
        assert done.wait(self._UNBLOCKED), f"{operation} never completed"
        thread.join(timeout=self._UNBLOCKED)

    @pytest.mark.parametrize("operation", ["get_asset", "is_managed_download"])
    def test_the_hot_path_reads_stay_lock_free(self, operation: str) -> None:
        """The deliberate other half. Both are a single atomic ``dict.get`` /
        attribute read that adapters run per compile; putting them behind the
        lock would serialise the hot path to buy nothing. Pinned so that stays
        a decision."""
        from brailix.core.models import asset_registry as ar

        calls = {
            "get_asset": lambda: get_asset("anything"),
            "is_managed_download": is_managed_download,
        }
        with ar._lock:
            thread, done = self._call_in_thread(calls[operation])
            assert done.wait(self._UNBLOCKED), (
                f"{operation} blocked on the lock — it is meant to be a "
                f"lock-free read"
            )
        thread.join(timeout=self._UNBLOCKED)

    def test_all_assets_is_a_snapshot_not_a_view(self, tmp_path: Path) -> None:
        """What the lock is protecting, stated as the caller-visible property:
        a list handed out stays what it was, and :class:`ModelAsset` is frozen,
        so a later registration cannot rewrite a front-end's table underneath
        it mid-render."""
        register_asset(_asset("a", tmp_path / "a"))
        snapshot = all_assets()
        register_asset(_asset("b", tmp_path / "b"))
        assert [a.name for a in snapshot] == ["a"]
        assert [a.name for a in all_assets()] == ["a", "b"]
