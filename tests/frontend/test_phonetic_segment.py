"""Phonetic-region recognition: segmenter + normalizer + end-to-end.

A phonetic transcription is recognised as a protected ``/.../`` or
``[...]`` region — but only when its content looks like IPA (carries an
IPA-distinct symbol), so ordinary slashed / bracketed prose (file paths,
ratios, footnote refs) is never disturbed. ``$...$`` math wins every
conflict. The normalizer strips the delimiters and narrows the span to
the bare phoneme run.
"""

from __future__ import annotations

import pytest

from brailix.core.config import load_profile
from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.frontend.normalize import DefaultNormalizer
from brailix.frontend.segment import (
    _IPA_DISTINCT_CHARS,
    DefaultSegmenter,
    _qualifies_as_phonetic,
)
from brailix.ir.document import Paragraph
from brailix.ir.inline import PhoneticInline
from brailix.pipeline import Pipeline


def _segs(text: str):
    block = Paragraph(text=text, span=Span(0, len(text)) if text else None)
    return DefaultSegmenter().segment(block, FrontendContext(profile="cn_current"))


def _phonetic_surfaces(text: str) -> list[str]:
    return [s.surface for s in _segs(text) if s.type == "phonetic_inline"]


def _normalize(text: str):
    segs = _segs(text)
    return DefaultNormalizer().normalize(segs, FrontendContext(profile="cn_current"))


# ---------------------------------------------------------------------------
# Recognition
# ---------------------------------------------------------------------------


class TestRecognition:
    def test_slash_delimiter(self):
        assert _phonetic_surfaces("/kæt/") == ["/kæt/"]

    def test_bracket_delimiter(self):
        assert _phonetic_surfaces("[kæt]") == ["[kæt]"]

    def test_embedded_in_prose(self):
        assert _phonetic_surfaces("他读 /tʃiːz/ 这个词") == ["/tʃiːz/"]

    def test_two_regions_one_line(self):
        assert _phonetic_surfaces("/kæt/ 和 /dɒɡ/") == ["/kæt/", "/dɒɡ/"]

    def test_stress_marks_kept_inside_region(self):
        # The stress mark stays in the region (the backend flags it later).
        assert _phonetic_surfaces("单词 /ˈæpl/ 苹果") == ["/ˈæpl/"]


# ---------------------------------------------------------------------------
# Guard: ordinary slashed / bracketed prose is NOT phonetic
# ---------------------------------------------------------------------------


class TestGuardRejectsProse:
    def test_file_path(self):
        assert _phonetic_surfaces("input/output") == []

    def test_ratio_and_date(self):
        assert _phonetic_surfaces("5/17") == []

    def test_and_or(self):
        assert _phonetic_surfaces("and/or") == []

    def test_acronym_path(self):
        assert _phonetic_surfaces("TCP/IP") == []

    def test_triple_slash_no_ipa(self):
        assert _phonetic_surfaces("x/y/z") == []

    def test_footnote_bracket(self):
        assert _phonetic_surfaces("[注1]") == []

    def test_bracketed_number_list(self):
        assert _phonetic_surfaces("[1,2,3]") == []


class TestQualifiesGuard:
    def test_accepts_ipa_content(self):
        assert _qualifies_as_phonetic("kæt")
        assert _qualifies_as_phonetic("həˈləʊ")
        assert _qualifies_as_phonetic("tʃiːz")

    def test_rejects_pure_ascii(self):
        # Documented limitation: an all-ASCII transcription isn't
        # auto-detected (it carries no IPA-distinct character).
        assert not _qualifies_as_phonetic("pet")
        assert not _qualifies_as_phonetic("output")

    def test_rejects_digits_and_cjk(self):
        assert not _qualifies_as_phonetic("5/17")
        assert not _qualifies_as_phonetic("注1")

    def test_rejects_empty(self):
        assert not _qualifies_as_phonetic("")


# ---------------------------------------------------------------------------
# Coexistence with math (``$...$`` wins)
# ---------------------------------------------------------------------------


class TestMathPrecedence:
    def test_phonetic_inside_math_is_not_captured(self):
        # The inner /æ/ would qualify as phonetic on its own, but it sits
        # inside a math island, so math wins and there is no phonetic seg.
        types = [s.type for s in _segs("$/æ/$")]
        assert "phonetic_inline" not in types
        assert "math_inline" in types

    def test_math_and_phonetic_side_by_side(self):
        segs = _segs("/kæt/ 与 $x$")
        kinds = [s.type for s in segs]
        assert "phonetic_inline" in kinds
        assert "math_inline" in kinds


# ---------------------------------------------------------------------------
# Normalizer: phonetic_inline segment -> PhoneticInline node
# ---------------------------------------------------------------------------


class TestNormalize:
    def test_produces_phonetic_node(self):
        out = _normalize("/kæt/")
        assert len(out) == 1
        node = out[0]
        assert isinstance(node, PhoneticInline)

    def test_delimiters_stripped_from_surface(self):
        node = _normalize("/kæt/")[0]
        assert node.surface == "kæt"

    def test_bracket_delimiters_stripped(self):
        node = _normalize("[tʃiːz]")[0]
        assert node.surface == "tʃiːz"

    def test_span_narrowed_to_inner(self):
        # "/kæt/" spans [0,5); the node narrows to the inner "kæt" [1,4).
        node = _normalize("/kæt/")[0]
        assert node.span == Span(1, 4)


# ---------------------------------------------------------------------------
# Drift guard: every non-ASCII table symbol is recognised by the frontend
# ---------------------------------------------------------------------------


def test_ipa_distinct_chars_cover_table():
    profile = load_profile("cn_current")
    table_non_ascii = {
        ch for key in profile.phonetic for ch in key if ord(ch) >= 0x80
    }
    missing = table_non_ascii - _IPA_DISTINCT_CHARS
    assert not missing, (
        f"segment._IPA_DISTINCT_CHARS misses table symbols {sorted(missing)} — "
        f"regions containing them wouldn't be recognised as phonetic"
    )


# ---------------------------------------------------------------------------
# End-to-end pipeline
# ---------------------------------------------------------------------------


class TestEndToEnd:
    def test_translate_transcription(self):
        pipe = Pipeline(profile="cn_current", mode="normal")
        result = pipe.translate_text("/kæt/")
        # k(13) æ(146) t(2345) = ⠅⠩⠞
        assert result.render() == "⠅⠩⠞"
        assert not result.warnings

    @pytest.mark.requires("jieba")
    def test_mixed_with_chinese(self):
        pipe = Pipeline(profile="cn_current", mode="normal")
        result = pipe.translate_text("猫 /kæt/")
        rendered = result.render()
        assert "⠅⠩⠞" in rendered
        assert not result.warnings

    def test_stress_mark_warns_but_recovers(self):
        pipe = Pipeline(profile="cn_current", mode="normal")
        result = pipe.translate_text("/ˈæpl/")
        assert [w.code for w in result.warnings] == ["PHONETIC_UNKNOWN_SYMBOL"]
        # æ p l still rendered after the flagged stress mark.
        assert "⠩⠏⠇" in result.render()

    def test_ncb_profile_also_supports(self):
        pipe = Pipeline(profile="cn_ncb", mode="normal")
        result = pipe.translate_text("/kæt/")
        assert result.render() == "⠅⠩⠞"
        assert not result.warnings
