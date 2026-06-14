"""Direct unit tests for the shared cursor-based span recovery
(``recover_spans_by_cursor``) used by the THULAC / HanLP analyzer adapters.
It previously had no direct coverage — only indirect exercise through those
adapters."""

from __future__ import annotations

from brailix.frontend.zh.analyzer.adapters._spans import recover_spans_by_cursor


def _spans(words, text, **kw):
    return recover_spans_by_cursor(
        words,
        text,
        None,
        code_prefix="TEST",
        source="test",
        engine="TEST",
        **kw,
    )


def test_found_words_get_exact_spans():
    toks = _spans([("我", None), ("重庆", None)], "我重庆")
    assert [(t.span.start, t.span.end) for t in toks] == [(0, 1), (1, 3)]


def test_repeated_word_resolves_to_next_occurrence():
    toks = _spans([("好", None), ("好", None)], "好好")
    assert [(t.span.start, t.span.end) for t in toks] == [(0, 1), (1, 2)]


def test_skip_blank_drops_whitespace_surfaces():
    toks = _spans([("我", None), (" ", None), ("好", None)], "我 好", skip_blank=True)
    assert [t.surface for t in toks] == ["我", "好"]


def test_not_found_word_span_clamped_to_text_length():
    # An engine-invented / normalised word absent from the source gets a
    # synthetic span at the cursor — it must not run past len(text).
    toks = _spans([("absent", None)], "hi")
    assert len(toks) == 1
    assert toks[0].span.end <= len("hi")
    assert toks[0].span.start <= toks[0].span.end
