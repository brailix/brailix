"""Property-based conformance suite for the PinyinResolver contract.

:class:`brailix.core.protocols.PinyinResolver` states: *the resolver fills
the ``pinyin`` field on tokens; it must not change token boundaries or
types*. Concretely, for ANY input: same token count, same surfaces, same
spans, same POS, in the same order; only ``pinyin`` / ``confidence`` may be
(re)filled; and the caller's input tokens are left unmutated.

This one generated suite runs against every *importable* registered
resolver (adapters whose extra isn't installed skip themselves), so a new
adapter is covered by registration alone — no more per-adapter copies of
the same structural assertions.

The shared char-alignment helper (:func:`resolve_by_char_alignment`) is
additionally property-tested with a synthetic per-character converter,
pinning the slicing arithmetic, the empty-syllable handling, the
length-mismatch bail-out and the low-confidence warning without any engine
installed.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError
from brailix.core.span import Span
from brailix.frontend.zh.pinyin.adapters._align import resolve_by_char_alignment
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken

# Every registered resolver whose dependency is importable in this
# environment. ``get`` raises MissingExtraError for an absent extra (e.g.
# g2pw without its model package) — that adapter is simply not exercised
# here, mirroring how the auto chain skips it at runtime.
_AVAILABLE: list[tuple[str, object]] = []
for _name in resolver_registry.names():
    try:
        _AVAILABLE.append((_name, resolver_registry.get(_name)))
    except MissingExtraError:
        continue

_HANZI = "我在重庆的人民银行长得很好乐了中文数字盲一二三行"
# Mixed surfaces exercise the engines' bail-out paths (an engine may return
# fewer syllables than characters for non-hanzi input); the shape contract
# must hold there exactly as on the happy path.
_MIXED = _HANZI + "ab3,"


@st.composite
def token_lists(draw: st.DrawFn, alphabet: str = _HANZI) -> list[ChineseToken]:
    surfaces = draw(
        st.lists(
            st.text(alphabet=st.sampled_from(list(alphabet)), min_size=1, max_size=3),
            max_size=6,
        )
    )
    tokens: list[ChineseToken] = []
    cursor = 0
    for surface in surfaces:
        pos_tag = draw(st.sampled_from(["n", "v", "d", None]))
        tokens.append(
            ChineseToken(
                surface=surface,
                pos=pos_tag,
                span=Span(cursor, cursor + len(surface)),
            )
        )
        cursor += len(surface)
    return tokens


def _shape(tokens: list[ChineseToken]) -> list[tuple[str, str | None, Span | None]]:
    return [(t.surface, t.pos, t.span) for t in tokens]


@pytest.mark.parametrize(
    ("name", "resolver"), _AVAILABLE, ids=[name for name, _ in _AVAILABLE]
)
class TestResolverConformance:
    @settings(max_examples=20)
    @given(tokens=token_lists())
    def test_shape_preserved_on_hanzi_input(
        self, name: str, resolver, tokens: list[ChineseToken]
    ) -> None:
        self._assert_contract(resolver, tokens)

    @settings(max_examples=20)
    @given(tokens=token_lists(alphabet=_MIXED))
    def test_shape_preserved_on_mixed_input(
        self, name: str, resolver, tokens: list[ChineseToken]
    ) -> None:
        # Non-hanzi characters may make an engine's syllable count diverge;
        # whatever the engine does, the structural contract may not bend.
        self._assert_contract(resolver, tokens)

    @staticmethod
    def _assert_contract(resolver, tokens: list[ChineseToken]) -> None:
        ctx = FrontendContext(profile="cn_current")
        before_shape = _shape(tokens)
        before_pinyin = [t.pinyin for t in tokens]

        resolved = resolver.resolve(tokens, ctx)

        # Same tokens, same order, same boundaries — only pinyin /
        # confidence may differ from the input.
        assert _shape(resolved) == before_shape
        for token in resolved:
            assert token.pinyin is None or (
                isinstance(token.pinyin, str) and token.pinyin.strip()
            )
        # The caller's list and its tokens are not mutated in place.
        assert _shape(tokens) == before_shape
        assert [t.pinyin for t in tokens] == before_pinyin


# --- resolve_by_char_alignment ------------------------------------------------

# Per-character syllables with occasional empty strings (an engine may emit
# an empty reading for a character it passes through).
_syllable = st.one_of(st.just(""), st.text(alphabet="abcdefg12345", min_size=1, max_size=4))


class TestCharAlignment:
    @settings(max_examples=50)
    @given(tokens=token_lists(), data=st.data())
    def test_matched_syllables_slice_per_token(
        self, tokens: list[ChineseToken], data: st.DataObject
    ) -> None:
        sentence = "".join(t.surface for t in tokens)
        syllables = data.draw(
            st.lists(_syllable, min_size=len(sentence), max_size=len(sentence))
        )
        ctx = FrontendContext(profile="cn_current")
        out = resolve_by_char_alignment(
            tokens, syllables, ctx, source="pinyin.test", engine="test"
        )
        assert _shape(out) == _shape(tokens)
        cursor = 0
        for token in out:
            chunk = syllables[cursor : cursor + len(token.surface)]
            expected = " ".join(s for s in chunk if s) or None
            assert token.pinyin == expected
            cursor += len(token.surface)
        # A matched-length resolve never warns.
        assert list(ctx.warnings) == []

    @settings(max_examples=50)
    @given(tokens=token_lists(), extra=st.integers(-3, 3).filter(lambda n: n != 0))
    def test_length_mismatch_bails_out_whole(
        self, tokens: list[ChineseToken], extra: int
    ) -> None:
        # An engine that merged or dropped a position would smear every
        # later token's reading one slot over; the helper must clear ALL
        # pinyin (not slice past the divergence) and say so once.
        sentence = "".join(t.surface for t in tokens)
        count = len(sentence) + extra
        if count < 0:
            count = 0
        syllables = ["xx1"] * count
        ctx = FrontendContext(profile="cn_current")
        out = resolve_by_char_alignment(
            tokens, syllables, ctx, source="pinyin.test", engine="test"
        )
        if not tokens:
            assert out == []
            return
        assert _shape(out) == _shape(tokens)
        assert all(t.pinyin is None and t.confidence is None for t in out)
        assert [w.code for w in ctx.warnings] == ["PINYIN_LENGTH_MISMATCH"]
        # A None context must stay usable (no crash, same clearing).
        out_no_ctx = resolve_by_char_alignment(
            tokens, syllables, None, source="pinyin.test", engine="test"
        )
        assert all(t.pinyin is None for t in out_no_ctx)

    @settings(max_examples=50)
    @given(tokens=token_lists(), data=st.data())
    def test_confidence_is_per_token_minimum(
        self, tokens: list[ChineseToken], data: st.DataObject
    ) -> None:
        sentence = "".join(t.surface for t in tokens)
        syllables = ["xx1"] * len(sentence)
        confidences = data.draw(
            st.lists(
                st.floats(min_value=0.0, max_value=1.0, allow_nan=False),
                min_size=len(sentence),
                max_size=len(sentence),
            )
        )
        threshold = 0.9
        ctx = FrontendContext(profile="cn_current")
        out = resolve_by_char_alignment(
            tokens,
            syllables,
            ctx,
            source="pinyin.test",
            engine="test",
            confidences=confidences,
            low_confidence_threshold=threshold,
        )
        cursor = 0
        expected_warnings = 0
        for token in out:
            token_confs = confidences[cursor : cursor + len(token.surface)]
            assert token.confidence == min(token_confs)
            if min(token_confs) < threshold:
                expected_warnings += 1
            cursor += len(token.surface)
        low = [w for w in ctx.warnings if w.code == "LOW_CONFIDENCE_PINYIN"]
        assert len(low) == expected_warnings
