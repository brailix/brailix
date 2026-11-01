"""Tests for the g2pW adapter (lazy import + token mapping + warnings)."""

from __future__ import annotations

import sys
import types

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError, ModelNotInstalledError, RunMode
from brailix.core.span import Span
from brailix.frontend.zh.pinyin.adapters.g2pw import (
    G2pwPinyinResolver,
    _normalize_predictor_output,
)
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.frontend.zh.tokens import ChineseToken

# ---------------------------------------------------------------------------
# Lazy-import / missing-extra contract
# ---------------------------------------------------------------------------


def test_missing_g2pw_surfaces_missing_extra_error(monkeypatch):
    resolver_registry.clear_cache()
    monkeypatch.setitem(sys.modules, "g2pw", None)
    with pytest.raises(MissingExtraError) as ei:
        resolver_registry.get("g2pw")
    assert ei.value.extra == "g2pw"
    assert "pip install brailix[g2pw]" in str(ei.value)


# ---------------------------------------------------------------------------
# Predictor output normalization
# ---------------------------------------------------------------------------


class TestNormalizePredictorOutput:
    def test_the_shape_g2pw_actually_returns(self):
        """``G2PWConverter`` batches: a list with one result per sentence.

        Verified against the real 589 MB model —
        ``conv(["我在重庆"])`` returns
        ``[['wo3', 'zai4', 'chong2', 'qing4']]``. Reading that outer list as
        the syllables made ``len(syllables)`` 1 for every input, so the
        per-character alignment saw a divergence every time and cleared every
        reading: the engine rendered braille byte-for-byte identical to
        ``resolver="null"`` and the scheduled smoke job called it a pass.
        """
        py, conf = _normalize_predictor_output([["wo3", "zai4", "chong2"]])
        assert py == ["wo3", "zai4", "chong2"]
        assert conf is None

    def test_a_character_with_no_reading_keeps_its_slot(self):
        # g2pW seeds each sentence with ``[None] * len(sentence)`` and fills
        # in what it knows, so a None is a real answer — and dropping it would
        # shorten the list and misalign every character after it.
        py, _ = _normalize_predictor_output([["wo3", None, "chong2"]])
        assert py == ["wo3", None, "chong2"]

    def test_tuple_form(self):
        # An injected predictor may also report confidences; the shipped
        # converter never does (its ``__call__`` returns predictions alone).
        py, conf = _normalize_predictor_output(
            ([["wo3", "zai4"]], [[0.9, 0.95]])
        )
        assert py == ["wo3", "zai4"]
        assert conf == [0.9, 0.95]

    def test_flat_form_from_an_injected_predictor(self):
        # Not what g2pW returns, but unambiguous — the entries are strings, so
        # this is one sentence's syllables rather than a batch of sentences.
        py, conf = _normalize_predictor_output(["wo3", "zai4"])
        assert py == ["wo3", "zai4"]
        assert conf is None

    def test_tuple_with_none_confidence(self):
        py, conf = _normalize_predictor_output(([["a"]], None))
        assert conf is None

    def test_empty_output(self):
        py, conf = _normalize_predictor_output([])
        assert py == []
        assert conf is None


# ---------------------------------------------------------------------------
# Token mapping
# ---------------------------------------------------------------------------


def _predictor(syllables, confidences=None):
    """A stand-in shaped like the real ``G2PWConverter``.

    It used to take a bare sentence string and answer with a flat list of
    syllables — an interface g2pW has never had. Every test below passed
    against it while the shipped engine, called the way it really works,
    produced no readings at all. So the stand-in now insists on the batch
    call and answers in the batch shape.
    """

    def call(sentences):
        assert isinstance(sentences, list), (
            f"G2PWConverter takes a list of sentences; the adapter called it "
            f"with {type(sentences).__name__}"
        )
        if confidences is None:
            return [syllables]
        return ([syllables], [confidences])

    return call


class TestResolve:
    def test_single_char_tokens(self):
        tokens = [
            ChineseToken(surface="我", span=Span(0, 1)),
            ChineseToken(surface="在", span=Span(1, 2)),
        ]
        adapter = G2pwPinyinResolver(predictor=_predictor(["wo3", "zai4"]))
        out = adapter.resolve(tokens)
        assert [t.pinyin for t in out] == ["wo3", "zai4"]
        # original tokens are not mutated
        assert tokens[0].pinyin is None

    def test_multi_char_token_joins_syllables(self):
        tokens = [
            ChineseToken(surface="重庆", span=Span(0, 2)),
        ]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["chong2", "qing4"]),
        )
        out = adapter.resolve(tokens)
        assert out[0].pinyin == "chong2 qing4"

    def test_empty(self):
        assert G2pwPinyinResolver(predictor=_predictor([])).resolve([]) == []

    def test_confidence_propagates_minimum(self):
        tokens = [ChineseToken(surface="重庆", span=Span(0, 2))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["chong2", "qing4"], [0.99, 0.8]),
        )
        out = adapter.resolve(tokens)
        assert out[0].confidence == 0.8


class TestLowConfidenceWarning:
    def test_low_confidence_emits_warning(self):
        ctx = FrontendContext(profile="cn_current", mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="单于", span=Span(0, 2))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["chan2", "yu2"], [0.5, 0.55]),
        )
        adapter.resolve(tokens, ctx)
        warnings = list(ctx.warnings)
        assert len(warnings) == 1
        assert warnings[0].code == "LOW_CONFIDENCE_PINYIN"
        assert warnings[0].surface == "单于"
        assert warnings[0].source == "pinyin.g2pw"

    def test_high_confidence_emits_nothing(self):
        ctx = FrontendContext(profile="cn_current", mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["wo3"], [0.99]),
        )
        adapter.resolve(tokens, ctx)
        assert len(ctx.warnings) == 0

    def test_threshold_is_configurable(self):
        ctx = FrontendContext(profile="cn_current", mode=RunMode.NORMAL)
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = G2pwPinyinResolver(
            predictor=_predictor(["wo3"], [0.6]),
            low_confidence_threshold=0.5,
        )
        adapter.resolve(tokens, ctx)
        assert len(ctx.warnings) == 0

    def test_no_ctx_no_warning(self):
        # When ctx is None we still process tokens, just silently.
        tokens = [ChineseToken(surface="我", span=Span(0, 1))]
        adapter = G2pwPinyinResolver(predictor=_predictor(["wo3"], [0.1]))
        out = adapter.resolve(tokens)
        assert out[0].pinyin == "wo3"


class TestProtocolConformance:
    def test_satisfies_protocol(self):
        from brailix.frontend.zh.pinyin import PinyinResolver

        adapter = G2pwPinyinResolver(predictor=_predictor([]))
        assert isinstance(adapter, PinyinResolver)


def _seed_model_dir(
    tmp_path, monkeypatch: pytest.MonkeyPatch, *, marker: bool = True
):
    """Point the adapter's ``get_model_dir`` at a tmp dir (seeded on request).

    Patched, never chdir'd: under the weekly mutation audit, mutmut's
    trampoline resolves its relative ``source_paths`` against the cwd on
    every hit, so a test that chdir's into tmp_path dies with
    FileNotFoundError the moment a trampolined loader runs. Pointing the
    adapter module's ``get_model_dir`` reference at a tmp directory keeps
    the cwd — and the audit — intact.
    """
    monkeypatch.setattr(
        "brailix.frontend.zh.pinyin.adapters.g2pw.get_model_dir",
        lambda name: tmp_path / "models" / name,
    )
    model_dir = tmp_path / "models" / "g2pw"
    if marker:
        model_dir.mkdir(parents=True)
        (model_dir / "version").write_text("v3.0\n")
    return model_dir


class TestLoaderWithFakeModule:
    def test_load_wraps_g2pwconverter(self, tmp_path, monkeypatch):
        """When ``g2pw`` is importable and the model is present, ``_load``
        builds the resolver around a fresh :class:`G2PWConverter` instance."""
        _seed_model_dir(tmp_path, monkeypatch)
        fake_module = types.ModuleType("g2pw")
        asked = {}

        class _FakeConverter:
            def __init__(
                self, *, model_dir: str = "", style: str = "bopomofo", **_: object
            ) -> None:
                asked["model_dir"] = model_dir
                asked["style"] = style

            def __call__(self, sentences):
                return [["wo3"] * len(s) for s in sentences]

        fake_module.G2PWConverter = _FakeConverter
        monkeypatch.setitem(sys.modules, "g2pw", fake_module)
        resolver_registry.clear_cache()

        # Trigger lazy load via the registry — the same path users hit.
        adapter = resolver_registry.get("g2pw")
        assert isinstance(adapter, G2pwPinyinResolver)
        assert callable(adapter.predictor)
        # The converter's own default is bopomofo (``ㄨㄛ3``), which no reader
        # of ``ChineseToken.pinyin`` understands, so the adapter has to ask.
        assert asked["style"] == "pinyin"
        # The model dir is passed explicitly: the constructor's own
        # missing-model branch downloads from a URL that no longer exists,
        # so the adapter must always hand it the verified directory.
        assert asked["model_dir"] == str(tmp_path / "models" / "g2pw")
        py, conf = _normalize_predictor_output(adapter.predictor(["我"]))
        assert py == ["wo3"]
        assert conf is None

    def test_load_checks_the_model_before_the_converter_runs(
        self, tmp_path, monkeypatch
    ):
        # A missing model must raise ModelNotInstalledError from the
        # availability pre-check — BEFORE the converter is constructed, so
        # its dead-URL download branch is never reached (the old failure
        # mode was zipfile.BadZipFile off a fetched error page, wrapped as
        # MissingExtraError with a "pip install" hint that solved nothing).
        _seed_model_dir(tmp_path, monkeypatch, marker=False)
        fake_module = types.ModuleType("g2pw")

        class _MustNotRun:
            def __init__(self, **_: object) -> None:
                raise AssertionError("converter constructed without a model")

        fake_module.G2PWConverter = _MustNotRun
        monkeypatch.setitem(sys.modules, "g2pw", fake_module)
        resolver_registry.clear_cache()

        with pytest.raises(ModelNotInstalledError) as ei:
            resolver_registry.get("g2pw")
        assert ei.value.model_id == "g2pw_polyphone_v2"
        assert str(tmp_path / "models" / "g2pw") in str(ei.value)

    def test_load_wraps_model_failure_as_not_installed(
        self, tmp_path, monkeypatch
    ):
        # g2pw IS importable and the model dir passes the pre-check, but the
        # converter still fails on construction (corrupt weights, offline
        # tokenizer fetch). _load must translate that into
        # ModelNotInstalledError so the ``auto`` chain degrades instead of
        # crashing — not let the raw RuntimeError escape.
        _seed_model_dir(tmp_path, monkeypatch)
        fake_module = types.ModuleType("g2pw")

        class _BoomConverter:
            def __init__(self, **_: object) -> None:
                raise RuntimeError("corrupt onnx weights")

        fake_module.G2PWConverter = _BoomConverter
        monkeypatch.setitem(sys.modules, "g2pw", fake_module)
        resolver_registry.clear_cache()

        with pytest.raises(ModelNotInstalledError):
            resolver_registry.get("g2pw")
