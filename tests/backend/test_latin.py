"""Tests for :mod:`brailix.backend.latin`.

V3 Latin (Current Chinese Braille convention for embedded English): one
prefix on the first letter of a word, bare cells for the rest. Mid-word capitals
lose their case (proper-noun trade-off). Non-letter chars fall back to
the punctuation table. Full UEB / contractions etc. are future work.
"""

from __future__ import annotations

import pytest

from brailix.backend.latin import translate_latin
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import LatinAcronym, LatinWord


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx(profile):
    return BackendContext(profile=profile.name)


class TestLatinLetterLookup:
    """V3 Latin (Current Chinese Braille): prefix only on first letter,
    bare cells after.

    LatinWord / LatinAcronym share the same path. Mid-word case info is
    lost (e.g., ``McDonald``'s ``D`` emits as a bare cell, no prefix).
    Non-letter chars fall back to the punctuation table.
    """

    def test_lowercase_word_emits_one_prefix_then_bare(self, ctx, profile):
        # "hi" → latin_lower prefix (c_56) + h + i (3 cells total).
        cells = translate_latin(LatinWord(surface="hi", span=Span(0, 2)), ctx, profile)
        assert len(cells) == 3
        assert all(c.role == "latin_letter" for c in cells)
        assert cells[0].dots == (5, 6)        # latin_lower prefix
        assert cells[0].source_text == "h"
        assert cells[1].dots == (1, 2, 5)     # h cell (no prefix)
        assert cells[1].source_text == "h"
        assert cells[2].dots == (2, 4)        # i cell, bare
        assert cells[2].source_text == "i"

    def test_mixed_case_first_upper_then_bare(self, ctx, profile):
        # "Hi" → latin_upper prefix (c_6) + H cell + i cell (3 cells).
        cells = translate_latin(
            LatinWord(surface="Hi", span=Span(0, 2)), ctx, profile
        )
        assert len(cells) == 3
        assert cells[0].dots == (6,)          # latin_upper prefix
        assert cells[0].source_text == "H"
        assert cells[1].source_text == "H"    # H bare cell
        assert cells[2].source_text == "i"    # i bare cell

    def test_acronym_doubled_upper_prefix_for_all_caps_word(self, ctx, profile):
        # "CPU" → ⠠⠠ (whole-word capitals doubles the sign) + C + P + U
        # (5 cells total). A single ⠠ now unambiguously means "first
        # letter only is capital", so CPU and Cpu stay distinguishable.
        cells = translate_latin(LatinAcronym(surface="CPU"), ctx, profile)
        assert len(cells) == 5
        assert cells[0].dots == (6,)          # doubled latin_upper prefix
        assert cells[1].dots == (6,)
        assert cells[0].source_text == "C"
        assert cells[2].source_text == "C"
        assert cells[3].source_text == "P"
        assert cells[4].source_text == "U"

    def test_all_caps_latin_word_also_doubles_prefix(self, ctx, profile):
        # The doubling keys off the surface being all-capitals, not off
        # the LatinAcronym node type — an all-caps LatinWord doubles too.
        cells = translate_latin(
            LatinWord(surface="NVDA", span=Span(0, 4)), ctx, profile
        )
        assert len(cells) == 6
        assert cells[0].dots == (6,)
        assert cells[1].dots == (6,)

    def test_two_letter_all_caps_doubles_but_single_capital_does_not(
        self, ctx, profile
    ):
        # "AB" → ⠠⠠⠁⠃; a lone "A" keeps the single ⠠.
        ab = translate_latin(LatinWord(surface="AB", span=Span(0, 2)), ctx, profile)
        assert [c.dots for c in ab] == [(6,), (6,), (1,), (1, 2)]
        a = translate_latin(LatinWord(surface="A", span=Span(0, 1)), ctx, profile)
        assert [c.dots for c in a] == [(6,), (1,)]

    def test_mid_word_capital_emits_bare_no_extra_prefix(self, ctx, profile):
        # "McDonald" → upper prefix + M + c + D + o + n + a + l + d
        # The mid-word D is rendered as a bare cell (same dots as 'd'
        # in cn_current since case-distinguishing cells live in the
        # prefix); no per-letter prefix emitted.
        cells = translate_latin(
            LatinWord(surface="McDonald", span=Span(0, 8)), ctx, profile
        )
        # 1 prefix + 8 letter cells = 9 cells.
        assert len(cells) == 9
        assert cells[0].dots == (6,)          # only one upper prefix
        # No further (6,) prefix cells later in the sequence; in
        # cn_current letter D shares dot pattern (6,) with the prefix,
        # so a naive surface scan would over-count. Instead use the
        # cell-count invariant: prefix + one cell per surface char.
        assert len(cells) == 1 + len("McDonald")

    def test_word_with_punct_mid_word_falls_back(self, ctx, profile):
        # "a:b" → prefix + a + ':' (punct) + b (4 cells: prefix is on 'a',
        # ':' falls through to punct, 'b' is bare).
        cells = translate_latin(
            LatinWord(surface="a:b", span=Span(0, 3)), ctx, profile
        )
        roles_seq = [c.role for c in cells]
        # latin_letter: prefix + a + b = 3; punct: ':' = 1.
        assert roles_seq.count("latin_letter") == 3
        assert roles_seq.count("punct") == 1

    def test_unknown_char_yields_unknown_with_warning(self, ctx, profile):
        cells = translate_latin(
            LatinWord(surface="☃", span=Span(0, 1)), ctx, profile
        )
        assert len(cells) == 1
        assert cells[0].role == "unknown"
        assert any(w.code == "UNKNOWN_PUNCT" for w in ctx.warnings)

    def test_empty_surface_yields_no_cells(self, ctx, profile):
        cells = translate_latin(LatinWord(surface="", span=None), ctx, profile)
        assert cells == []

    def test_no_span_still_works(self, ctx, profile):
        cells = translate_latin(LatinWord(surface="hi"), ctx, profile)
        assert len(cells) == 3
        assert all(c.source_span is None for c in cells)

    def test_span_propagates_per_char(self, ctx, profile):
        cells = translate_latin(
            LatinWord(surface="ab", span=Span(10, 12)), ctx, profile
        )
        # Cells from the first char (prefix + bare a) share span (10,11).
        # Cell from 'b' has span (11,12).
        assert cells[0].source_span == Span(10, 11)
        assert cells[1].source_span == Span(10, 11)
        assert cells[2].source_span == Span(11, 12)

    def test_single_letter_word_is_prefix_plus_letter(self, ctx, profile):
        # "I" → upper prefix + I cell (2 cells, no "subsequent" letters).
        cells = translate_latin(LatinWord(surface="I", span=Span(0, 1)), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (6,)
        assert cells[1].source_text == "I"

    def test_single_greek_lowercase_gets_greek_prefix(self, ctx, profile):
        # τ → Greek lower-case sign ⠨ (c_46) + τ cell ⠞ (c_2345).
        cells = translate_latin(
            LatinWord(surface="τ", span=Span(0, 1)), ctx, profile
        )
        assert len(cells) == 2
        assert all(c.role == "latin_letter" for c in cells)
        assert cells[0].dots == (4, 6)              # Greek lower-case sign
        assert cells[1].dots == (2, 3, 4, 5)        # τ
        assert cells[0].source_text == "τ"

    def test_single_greek_uppercase_gets_greek_upper_prefix(self, ctx, profile):
        # Α (U+0391) → Greek upper-case sign ⠸ (c_456) + Α cell (c_1).
        cells = translate_latin(
            LatinWord(surface="Α", span=Span(0, 1)), ctx, profile
        )
        assert len(cells) == 2
        assert cells[0].dots == (4, 5, 6)           # Greek upper-case sign
        assert cells[1].dots == (1,)                # Α (= cell c_1)

    def test_greek_acronym_one_upper_prefix_for_whole_word(self, ctx, profile):
        # ΑΒΓ → one upper prefix + three bare Greek cells (= 4 cells).
        from brailix.ir.inline import LatinAcronym as _Acronym  # noqa
        cells = translate_latin(_Acronym(surface="ΑΒΓ"), ctx, profile)
        assert len(cells) == 4
        assert cells[0].dots == (4, 5, 6)           # Greek upper-case sign
        # First letter bare cell + the two following bare cells.
        assert cells[1].dots == (1,)                # Α
        assert cells[2].dots == (1, 2)              # Β
        assert cells[3].dots == (1, 2, 4, 5)        # Γ

    def test_greek_lowercase_word_one_prefix_then_bare(self, ctx, profile):
        # ταυ → Greek lower-case sign + τ + α + υ (4 cells).
        cells = translate_latin(
            LatinWord(surface="ταυ", span=Span(0, 3)), ctx, profile
        )
        assert len(cells) == 4
        assert cells[0].dots == (4, 6)              # Greek lower-case sign
        assert cells[1].dots == (2, 3, 4, 5)        # τ
        assert cells[2].dots == (1,)                # α (bare)
        assert cells[3].dots == (1, 3, 6)           # υ (bare)

    def test_non_letter_first_char_does_not_consume_prefix(self, ctx, profile):
        # If somehow the first character isn't a letter, the prefix is
        # deferred to the next letter. Rare in practice (segmenter
        # usually classifies such surfaces differently) but the
        # invariant matters for correctness.
        cells = translate_latin(
            LatinWord(surface=":ab", span=Span(0, 3)), ctx, profile
        )
        roles_seq = [c.role for c in cells]
        # ':' is punct (1 cell); then 'a' gets the prefix path (2 cells:
        # prefix + a); 'b' is bare (1 cell). Total 4 cells.
        assert roles_seq[0] == "punct"
        assert roles_seq.count("latin_letter") == 3
        assert len(cells) == 4


class TestLatinHousing:
    """Pin the module boundary: translate_latin lives in
    :mod:`brailix.backend.latin`, NOT in ``backend.punct``.
    This guards against accidental relocation back to punct.py.
    """

    def test_function_lives_in_backend_latin(self):
        from brailix.backend import latin as latin_module

        assert latin_module.translate_latin is translate_latin

    def test_punct_no_longer_exports_translate_latin(self):
        from brailix.backend import punct as punct_module

        assert not hasattr(punct_module, "translate_latin")

    def test_dispatcher_routes_to_latin_module(self):
        from brailix.backend import dispatch

        # Ensure dispatcher's import path is the new latin module —
        # the symbol it calls must come from backend.latin.
        assert "latin" in dispatch.__dict__.get("latin_backend").__name__
