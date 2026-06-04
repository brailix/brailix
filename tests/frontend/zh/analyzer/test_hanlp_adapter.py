"""Tests for the HanLP adapter.

We do not require ``hanlp`` to be installed. Instead we:

1. Confirm the registry raises :class:`MissingExtraError` with the
   right hint when the loader fails to import the module.
2. Exercise the adapter's token-conversion logic by injecting a fake
   pipeline object, so the structural contract (what we do with HanLP
   output) is fully covered.
3. Cover the model-download paths: ``_load`` sets ``HANLP_HOME``
   before importing hanlp; by default it lets hanlp auto-download a
   missing model, and under managed download (a front-end opted in via
   ``set_managed_download``) it raises :class:`ModelNotInstalledError`
   instead.
"""

from __future__ import annotations

import os
import sys
import types
from pathlib import Path
from typing import Any

import pytest

from brailix.core.errors import MissingExtraError, ModelNotInstalledError
from brailix.core.models.asset_registry import set_managed_download
from brailix.core.span import Span
from brailix.frontend.zh.analyzer.adapters.hanlp import (
    _MTL_DIR,
    HanLPChineseAnalyzer,
    _ensure_model_installed,
    _extract_pos,
    _extract_words,
    _tokens_from,
)
from brailix.frontend.zh.analyzer.registry import analyzer_registry


def _seed_mtl_dir(hanlp_home: Path) -> Path:
    """Create a fake-but-present model directory under ``hanlp_home``.

    The adapter only checks the directory exists and is non-empty; we
    don't need a real HanLP model payload to exercise the load path.
    """
    install_dir = hanlp_home / _MTL_DIR
    install_dir.mkdir(parents=True, exist_ok=True)
    (install_dir / "config.json").write_text("{}", encoding="utf-8")
    return install_dir


@pytest.fixture(autouse=True)
def _reset_download_policy():
    """Some tests opt into managed download; reset around each so test
    ordering can't leak the policy."""
    set_managed_download(False)
    yield
    set_managed_download(False)


# ---------------------------------------------------------------------------
# Lazy-import / missing-extra contract
# ---------------------------------------------------------------------------


def test_missing_hanlp_surfaces_missing_extra_error(monkeypatch, tmp_path: Path):
    """If ``hanlp`` cannot be imported, the registry must report
    MissingExtraError pointing to the hanlp extra — not a raw ImportError.
    """
    # _load now sets HANLP_HOME via get_model_dir(), which creates
    # models/hanlp/ under cwd as a side effect. Redirect cwd to a
    # tmp dir so the assertion test doesn't pollute the repo root.
    monkeypatch.delattr(sys, "frozen", raising=False)
    monkeypatch.chdir(tmp_path)
    # Force a clean cache slot for "hanlp".
    analyzer_registry.clear_cache()

    # Hide any installed hanlp module so import inside _load fails.
    monkeypatch.setitem(sys.modules, "hanlp", None)

    with pytest.raises(MissingExtraError) as ei:
        analyzer_registry.get("hanlp")
    assert ei.value.extra == "hanlp"
    assert "pip install brailix[hanlp]" in str(ei.value)


def test_registered_loader_is_lazy(monkeypatch):
    """Registering the hanlp adapter must NOT trigger an import of
    the hanlp library."""
    # The fact that this test file imports analyzer_registry at the top
    # without hanlp being installed (or even after monkey-patching it
    # to None) is itself the test.
    monkeypatch.setitem(sys.modules, "hanlp", None)
    assert analyzer_registry.has("hanlp")


# ---------------------------------------------------------------------------
# Token-conversion logic (with injected fake pipeline)
# ---------------------------------------------------------------------------


class _FakeHanLPDoc:
    """Dict-like object mimicking HanLP's MTL pipeline output."""

    def __init__(self, payload: dict[str, Any]):
        self._payload = payload

    def __getitem__(self, key: str) -> Any:
        return self._payload[key]


def _make_pipeline(words: list[str], tags: list[str] | None = None, key: str = "tok/fine"):
    def pipeline(text: str) -> _FakeHanLPDoc:
        payload: dict[str, Any] = {key: words}
        if tags is not None:
            payload["pos/ctb"] = tags
        return _FakeHanLPDoc(payload)

    return pipeline


class TestTokenConversion:
    def test_basic_words_and_spans(self):
        pipeline = _make_pipeline(["我", "在", "重庆"], tags=["PN", "P", "NR"])
        adapter = HanLPChineseAnalyzer(pipeline=pipeline)
        tokens = adapter.analyze("我在重庆")
        assert [t.surface for t in tokens] == ["我", "在", "重庆"]
        assert [t.span for t in tokens] == [Span(0, 1), Span(1, 2), Span(2, 4)]
        assert [t.pos for t in tokens] == ["PN", "P", "NR"]

    def test_empty_text(self):
        adapter = HanLPChineseAnalyzer(pipeline=_make_pipeline([]))
        assert adapter.analyze("") == []

    def test_missing_pos_tags(self):
        pipeline = _make_pipeline(["你", "好"], tags=None)
        adapter = HanLPChineseAnalyzer(pipeline=pipeline)
        tokens = adapter.analyze("你好")
        assert [t.pos for t in tokens] == [None, None]

    def test_alternative_token_key(self):
        # Older HanLP versions surface tokens under "tok" rather than
        # "tok/fine"; the extractor must tolerate both.
        pipeline = _make_pipeline(["你", "好"], key="tok")
        adapter = HanLPChineseAnalyzer(pipeline=pipeline)
        tokens = adapter.analyze("你好")
        assert [t.surface for t in tokens] == ["你", "好"]


class TestHelperExtraction:
    def test_extract_words_unknown_shape_raises(self):
        with pytest.raises(ValueError):
            _extract_words({"foo": ["bar"]})

    def test_extract_words_skips_none_valued_keys(self):
        # If tok/fine is present but None, fall through to tok/coarse / tok.
        doc = {"tok/fine": None, "tok": ["你", "好"]}
        assert _extract_words(doc) == ["你", "好"]

    def test_extract_pos_returns_none_if_missing(self):
        assert _extract_pos({}) is None

    def test_extract_pos_skips_none_valued_keys(self):
        doc = {"pos/ctb": None, "pos": ["N", "V"]}
        assert _extract_pos(doc) == ["N", "V"]

    def test_tokens_from_recovers_span_by_search(self):
        # Words that don't start at cursor (simulated word reordering)
        # still get a reasonable span via text.find().
        tokens = _tokens_from(["重庆"], None, "我在重庆")
        assert tokens[0].span == Span(2, 4)

    def test_tokens_from_uses_cursor_when_word_not_present(self):
        # If a word doesn't appear in the input at all, fall back to
        # a synthetic span starting at the current cursor.
        tokens = _tokens_from(["不存在"], None, "我在重庆")
        assert tokens[0].span == Span(0, 3)


class TestLoaderWithFakeModule:
    def test_load_invokes_hanlp_load(self, monkeypatch, tmp_path: Path):
        """When ``hanlp`` is importable and the model is on disk,
        ``_load`` calls ``hanlp.load`` with the published MTL constant
        and wraps the result."""
        # Isolate the filesystem side-effect of get_model_dir() —
        # otherwise it would create models/hanlp/ at the repo root.
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        _seed_mtl_dir(tmp_path / "models" / "hanlp")

        fake_module = types.ModuleType("hanlp")
        loaded: dict[str, Any] = {}

        def fake_load(model_name):
            loaded["model"] = model_name
            return lambda text: _FakeHanLPDoc({"tok/fine": list(text), "pos/ctb": ["N"] * len(text)})

        class _Pretrained:
            class mtl:
                CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH = "<close-tok-pos-mtl>"

        fake_module.load = fake_load
        fake_module.pretrained = _Pretrained
        monkeypatch.setitem(sys.modules, "hanlp", fake_module)
        analyzer_registry.clear_cache()

        adapter = analyzer_registry.get("hanlp")
        assert isinstance(adapter, HanLPChineseAnalyzer)
        assert loaded["model"] == "<close-tok-pos-mtl>"
        tokens = adapter.analyze("你好")
        assert [t.surface for t in tokens] == ["你", "好"]

    def test_load_sets_hanlp_home_before_importing(
        self, monkeypatch, tmp_path: Path
    ):
        """The takeover only works if HANLP_HOME is set *before* the
        hanlp module is first imported. Verify by checking the env var
        is in place at the moment hanlp.load is called.
        """
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)
        _seed_mtl_dir(tmp_path / "models" / "hanlp")
        monkeypatch.delenv("HANLP_HOME", raising=False)

        fake_module = types.ModuleType("hanlp")
        seen: dict[str, str | None] = {}

        def fake_load(model_name):
            # By the time hanlp.load runs, HANLP_HOME must already be set.
            seen["env"] = os.environ.get("HANLP_HOME")
            return lambda text: _FakeHanLPDoc({"tok/fine": [], "pos/ctb": []})

        class _Pretrained:
            class mtl:
                CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH = "<x>"

        fake_module.load = fake_load
        fake_module.pretrained = _Pretrained
        monkeypatch.setitem(sys.modules, "hanlp", fake_module)
        analyzer_registry.clear_cache()

        analyzer_registry.get("hanlp")
        # Path the helper writes is models/hanlp/ under cwd in dev mode.
        assert seen["env"] is not None
        assert Path(seen["env"]) == tmp_path / "models" / "hanlp"


class TestEnsureModelInstalled:
    def test_raises_when_dir_missing(self, tmp_path: Path):
        with pytest.raises(ModelNotInstalledError) as ei:
            _ensure_model_installed(tmp_path)
        assert ei.value.model_id == "hanlp_mtl_electra_small_zh"
        assert ei.value.install_dir == tmp_path / _MTL_DIR

    def test_raises_when_dir_empty(self, tmp_path: Path):
        # An interrupted download leaves the dir but no files inside —
        # treat that as "not installed" rather than fooling the check.
        (tmp_path / _MTL_DIR).mkdir(parents=True)
        with pytest.raises(ModelNotInstalledError):
            _ensure_model_installed(tmp_path)

    def test_passes_when_dir_has_files(self, tmp_path: Path):
        _seed_mtl_dir(tmp_path)
        _ensure_model_installed(tmp_path)  # no raise

    def test_load_under_managed_download_raises_when_missing(
        self, monkeypatch, tmp_path: Path
    ):
        """Under managed download, registry.get('hanlp') surfaces
        ModelNotInstalledError when hanlp is importable but the model
        directory is absent — and must NOT call hanlp.load (a front-end's
        downloader handles the fetch)."""
        set_managed_download(True)
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)

        fake_module = types.ModuleType("hanlp")
        fake_module.load = lambda *a, **kw: pytest.fail(
            "hanlp.load must not be called under managed download"
        )

        class _Pretrained:
            class mtl:
                CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH = "<x>"

        fake_module.pretrained = _Pretrained
        monkeypatch.setitem(sys.modules, "hanlp", fake_module)
        analyzer_registry.clear_cache()

        with pytest.raises(ModelNotInstalledError):
            analyzer_registry.get("hanlp")

    def test_load_default_auto_downloads_when_missing(
        self, monkeypatch, tmp_path: Path
    ):
        """By default (no managed download) an absent model does NOT
        raise: the adapter lets hanlp.load auto-download on first use."""
        monkeypatch.delattr(sys, "frozen", raising=False)
        monkeypatch.chdir(tmp_path)

        loaded: dict[str, Any] = {}

        def fake_load(model_name):
            loaded["called"] = True
            return lambda text: None

        fake_module = types.ModuleType("hanlp")
        fake_module.load = fake_load

        class _Pretrained:
            class mtl:
                CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH = "<x>"

        fake_module.pretrained = _Pretrained
        monkeypatch.setitem(sys.modules, "hanlp", fake_module)
        analyzer_registry.clear_cache()

        analyzer = analyzer_registry.get("hanlp")  # no raise
        assert loaded.get("called") is True
        assert isinstance(analyzer, HanLPChineseAnalyzer)


class TestProtocolConformance:
    def test_instance_satisfies_protocol(self):
        from brailix.core.protocols import ChineseAnalyzer

        adapter = HanLPChineseAnalyzer(pipeline=_make_pipeline([]))
        assert isinstance(adapter, ChineseAnalyzer)


# The analyzer registry lives outside this package now; these adapter
# tests must not import any front-end / application layer.
