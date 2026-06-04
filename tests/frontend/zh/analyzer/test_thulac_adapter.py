"""Tests for the THULAC analyzer adapter.

Covers the lazy-import / missing-extra contract, the span-recovery
logic (THULAC gives no offsets, so the adapter recovers them by linear
search from a moving cursor), the line-marker / blank-token skipping,
and the ``_load`` wiring (``seg_only=True`` + ``cut(text, text=False)``).
The span logic is exercised with an injected ``cut_fn`` so we don't load
the ~100 MB model just to test the bookkeeping.
"""

from __future__ import annotations

import sys
import types

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError, RunMode
from brailix.frontend.zh.analyzer.adapters.thulac import (
    ThulacChineseAnalyzer,
    _ensure_cws_models_present,
)
from brailix.frontend.zh.analyzer.registry import analyzer_registry


def test_missing_thulac_surfaces_missing_extra_error(monkeypatch):
    analyzer_registry.clear_cache()
    monkeypatch.setitem(sys.modules, "thulac", None)
    with pytest.raises(MissingExtraError) as ei:
        analyzer_registry.get("thulac")
    assert ei.value.extra == "thulac"
    assert "pip install brailix[thulac]" in str(ei.value)


def _cut(pairs):
    """Build a cut_fn that ignores its input and returns fixed pairs."""
    return lambda _text: pairs


class TestAnalyze:
    def test_empty(self):
        assert ThulacChineseAnalyzer(cut_fn=_cut([])).analyze("") == []

    def test_single_char_words(self):
        a = ThulacChineseAnalyzer(cut_fn=_cut([["我", ""], ["在", ""]]))
        out = a.analyze("我在")
        assert [t.surface for t in out] == ["我", "在"]
        assert [(t.span.start, t.span.end) for t in out] == [(0, 1), (1, 2)]
        # seg-only mode carries no POS.
        assert all(t.pos is None for t in out)

    def test_multi_char_word_spans(self):
        a = ThulacChineseAnalyzer(cut_fn=_cut([["重庆", ""], ["银行", ""]]))
        out = a.analyze("重庆银行")
        assert [t.surface for t in out] == ["重庆", "银行"]
        assert [(t.span.start, t.span.end) for t in out] == [(0, 2), (2, 4)]

    def test_repeated_word_spans_advance(self):
        # The cursor advances past each match, so the second 很好 resolves
        # to the later occurrence rather than snapping back to the first.
        a = ThulacChineseAnalyzer(
            cut_fn=_cut([["很好", ""], ["，", ""], ["很好", ""]])
        )
        out = a.analyze("很好，很好")
        assert [(t.span.start, t.span.end) for t in out] == [
            (0, 2),
            (2, 3),
            (3, 5),
        ]

    def test_skips_newline_and_blank_markers(self):
        # THULAC inserts ['\n', ''] line markers and may emit blank /
        # whitespace tokens; none are real content.
        a = ThulacChineseAnalyzer(
            cut_fn=_cut([["我", ""], ["\n", ""], ["", ""], [" ", ""], ["在", ""]])
        )
        out = a.analyze("我在")
        assert [t.surface for t in out] == ["我", "在"]


class TestWarnings:
    def test_word_not_in_text_warns(self):
        ctx = FrontendContext(mode=RunMode.NORMAL)
        # THULAC returned a (normalized) word that isn't in the source.
        ThulacChineseAnalyzer(cut_fn=_cut([["X", ""]])).analyze("我", ctx)
        codes = {w.code for w in ctx.warnings}
        assert "THULAC_WORD_NOT_IN_TEXT" in codes

    def test_skipped_chars_warns(self):
        ctx = FrontendContext(mode=RunMode.NORMAL)
        # "很" sits between the cursor and the next word "好" → gap warning.
        ThulacChineseAnalyzer(cut_fn=_cut([["好", ""]])).analyze("很好", ctx)
        codes = {w.code for w in ctx.warnings}
        assert "THULAC_SKIPPED_CHARS" in codes

    def test_no_ctx_no_crash(self):
        # ctx=None → still produces tokens (synthetic span), just silent.
        out = ThulacChineseAnalyzer(cut_fn=_cut([["X", ""]])).analyze("我")
        assert out[0].surface == "X"


class TestProtocolConformance:
    def test_satisfies_protocol(self):
        from brailix.core.protocols import ChineseAnalyzer

        assert isinstance(ThulacChineseAnalyzer(cut_fn=_cut([])), ChineseAnalyzer)


class TestLoaderWithFakeModule:
    def test_load_builds_seg_only_segmenter(self, monkeypatch, tmp_path):
        """``_load`` must construct ``thulac.thulac(seg_only=True)`` and
        the resulting cut_fn must call ``cut(text, text=False)``."""
        fake_module = types.ModuleType("thulac")
        # _load() pre-checks the CWS models relative to ``thulac.__file__``,
        # so the fake package needs a __file__ and a models/ dir holding
        # non-empty cws_model.bin / cws_dat.bin, or the precheck trips.
        models_dir = tmp_path / "thulac" / "models"
        models_dir.mkdir(parents=True)
        (models_dir / "cws_model.bin").write_bytes(b"\x00")
        (models_dir / "cws_dat.bin").write_bytes(b"\x00")
        fake_module.__file__ = str(tmp_path / "thulac" / "__init__.py")
        captured: dict = {}

        class _FakeThulac:
            def __init__(self, **kwargs):
                captured["kwargs"] = kwargs

            def cut(self, oiraw, text=False):
                captured["oiraw"] = oiraw
                captured["text_flag"] = text
                return [[oiraw, ""]]

        fake_module.thulac = _FakeThulac
        monkeypatch.setitem(sys.modules, "thulac", fake_module)
        analyzer_registry.clear_cache()

        adapter = analyzer_registry.get("thulac")
        assert isinstance(adapter, ThulacChineseAnalyzer)
        assert captured["kwargs"] == {"seg_only": True}

        result = list(adapter.cut_fn("我"))
        assert captured["oiraw"] == "我"
        assert captured["text_flag"] is False
        assert result == [["我", ""]]
        analyzer_registry.clear_cache()


class TestModelPrecheck:
    """``_ensure_cws_models_present`` guards the seg_only ``.bin`` models so
    a missing / quarantined one degrades via ``auto`` instead of crashing
    mid-tokenize — the portable-build .bin-skip bug it was added for."""

    def test_passes_when_both_models_present(self, tmp_path):
        (tmp_path / "cws_model.bin").write_bytes(b"\x00\x01")
        (tmp_path / "cws_dat.bin").write_bytes(b"\x00\x01")
        # No raise.
        _ensure_cws_models_present(tmp_path)

    def test_raises_missing_extra_when_a_model_is_absent(self, tmp_path):
        # cws_dat.bin never written.
        (tmp_path / "cws_model.bin").write_bytes(b"\x00")
        with pytest.raises(MissingExtraError) as ei:
            _ensure_cws_models_present(tmp_path)
        assert ei.value.extra == "thulac"
        assert "cws_dat.bin" in str(ei.value)

    def test_raises_when_a_model_is_empty(self, tmp_path):
        # 0-byte file (half-written download / truncated AV restore) counts
        # as missing.
        (tmp_path / "cws_model.bin").write_bytes(b"")
        (tmp_path / "cws_dat.bin").write_bytes(b"\x00")
        with pytest.raises(MissingExtraError):
            _ensure_cws_models_present(tmp_path)


class TestAutoFallbackOnMissingModel:
    """thulac leads the ``auto`` chain; a missing model must fall back, not
    crash. Mirrors the runtime-failure mode the precheck exists to tame."""

    def test_auto_skips_thulac_when_model_missing(self, monkeypatch):
        import brailix.frontend.zh.analyzer.adapters.thulac as thulac_adapter

        def _raise_missing(_models_dir):
            raise MissingExtraError(adapter="thulac", extra="thulac")

        # Simulate the model being gone without touching the real wheel.
        monkeypatch.setattr(
            thulac_adapter, "_ensure_cws_models_present", _raise_missing
        )
        analyzer_registry.clear_cache()
        try:
            analyzer = analyzer_registry.get("auto")
            # Must not raise — auto catches MissingExtraError and falls
            # through to the next available tokenizer (jieba → char).
            tokens = analyzer.analyze("我")
            assert [t.surface for t in tokens] == ["我"]
        finally:
            analyzer_registry.clear_cache()
