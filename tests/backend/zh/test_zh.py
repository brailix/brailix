import pytest

from brailix.backend.zh import translate_hanzi_char, translate_word
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import HanziChar, Word


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current")


def _roles(cells):
    return [c.role for c in cells]


def _dots_for(profile, *paths):
    """Lookup helper for assertions."""
    out = []
    for kind, key in paths:
        out.append(getattr(profile, kind)[key])
    return out


class TestSingleSyllableZeroInitial:
    """我 = wo3 — no initial, final 'uo', tone 3."""

    def test_wo3(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="我", reading="wo3", span=Span(0, 1)),
            ctx, profile,
        )
        # Expected: [final("uo"), tone("3")]
        assert _roles(cells) == ["zh_final", "zh_tone"]
        assert cells[0].dots == profile.finals["uo"]
        assert cells[1].dots == profile.tones["3"]
        # Provenance is the same source character for every cell of this syllable.
        assert all(c.source_text == "我" for c in cells)
        assert all(c.source_span == Span(0, 1) for c in cells)


class TestSingleSyllableWithInitial:
    """在 = zai4 — initial z, final ai, tone 4."""

    def test_zai4(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="在", reading="zai4", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_initial", "zh_final", "zh_tone"]
        assert cells[0].dots == profile.initials["z"]
        assert cells[1].dots == profile.finals["ai"]
        assert cells[2].dots == profile.tones["4"]


class TestMultiSyllableWord:
    """重庆 = chong2 qing4."""

    def test_chongqing(self, ctx, profile):
        cells = translate_word(
            Word(surface="重庆", reading="chong2 qing4", span=Span(0, 2)),
            ctx, profile,
        )
        # chong2: ch + ong + tone2
        # qing4: q + ing + tone4
        # Total 6 cells.
        assert len(cells) == 6
        assert cells[0].dots == profile.initials["ch"]
        assert cells[1].dots == profile.finals["ong"]
        assert cells[2].dots == profile.tones["2"]
        assert cells[3].dots == profile.initials["q"]
        assert cells[4].dots == profile.finals["ing"]
        assert cells[5].dots == profile.tones["4"]

    def test_per_syllable_provenance(self, ctx, profile):
        cells = translate_word(
            Word(surface="重庆", reading="chong2 qing4", span=Span(10, 12)),
            ctx, profile,
        )
        # First three cells belong to 重 at span (10,11).
        assert all(c.source_span == Span(10, 11) for c in cells[:3])
        # Last three cells belong to 庆 at span (11,12).
        assert all(c.source_span == Span(11, 12) for c in cells[3:])


class TestToneSuppression:
    def test_neutral_tone_omitted(self, ctx, profile):
        # 了 = le5 — neutral tone, no tone cell emitted.
        cells = translate_hanzi_char(
            HanziChar(surface="了", reading="le5", span=Span(0, 1)),
            ctx, profile,
        )
        roles = _roles(cells)
        assert "zh_tone" not in roles
        # initial + final only
        assert roles == ["zh_initial", "zh_final"]

    def test_tone_disabled_via_profile(self, ctx, profile):
        profile.features["tone"] = False
        try:
            cells = translate_hanzi_char(
                HanziChar(surface="在", reading="zai4"), ctx, profile,
            )
            assert "zh_tone" not in _roles(cells)
        finally:
            profile.features["tone"] = True


class TestMissingPinyin:
    def test_emits_warning_and_unknown(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="我", reading=None, span=Span(0, 1)),
            ctx, profile,
        )
        warnings = list(ctx.warnings)
        assert any(w.code == "MISSING_PINYIN" for w in warnings)
        assert cells[0].role == "unknown"
        # one unknown per char
        assert len(cells) == 1

    def test_empty_word(self, ctx, profile):
        assert translate_word(Word(surface=""), ctx, profile) == []


class TestPinyinLengthMismatch:
    def test_mismatch_emits_warning_and_unknowns(self, ctx, profile):
        cells = translate_word(
            Word(surface="重庆", reading="chong2", span=Span(0, 2)),
            ctx, profile,
        )
        warnings = list(ctx.warnings)
        assert any(w.code == "PINYIN_LENGTH_MISMATCH" for w in warnings)
        # 2 chars → 2 unknown cells
        assert len(cells) == 2
        assert all(c.role == "unknown" for c in cells)


class TestBadPinyin:
    def test_invalid_syllable_emits_warning(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="X", reading="", span=Span(0, 1)),
            ctx, profile,
        )
        # Empty pinyin → MISSING_PINYIN (not BAD_PINYIN)
        assert any(w.code == "MISSING_PINYIN" for w in ctx.warnings)
        assert cells[0].role == "unknown"

    def test_unparseable_syllable(self, ctx, profile):
        # "ma6" has an invalid tone digit.
        cells = translate_hanzi_char(
            HanziChar(surface="妈", reading="ma6", span=Span(0, 1)),
            ctx, profile,
        )
        assert any(w.code == "BAD_PINYIN" for w in ctx.warnings)
        assert cells[0].role == "unknown"


class TestMissingMappingFallback:
    def test_unknown_initial_emits_warning(self, ctx, profile):
        # If we hack the profile to drop "z", the warning fires.
        original = profile.initials.pop("z")
        try:
            cells = translate_hanzi_char(
                HanziChar(surface="在", reading="zai4"), ctx, profile,
            )
            assert any(w.code == "MISSING_INITIAL" for w in ctx.warnings)
            # Unknown cell for initial + valid cell for final + tone
            assert cells[0].role == "unknown"
            assert cells[1].role == "zh_final"
        finally:
            profile.initials["z"] = original

    def test_unknown_final_emits_warning(self, ctx, profile):
        # Drop the "ai" mapping to exercise the MISSING_FINAL path.
        original = profile.finals.pop("ai")
        try:
            cells = translate_hanzi_char(
                HanziChar(surface="在", reading="zai4", span=Span(0, 1)),
                ctx, profile,
            )
            assert any(w.code == "MISSING_FINAL" for w in ctx.warnings)
            # initial cell ok, final cell falls back to unknown, then tone.
            assert cells[0].role == "zh_initial"
            assert cells[1].role == "unknown"
            assert cells[2].role == "zh_tone"
        finally:
            profile.finals["ai"] = original


class TestIaoIangDistinctFinals:
    """Regression: an early version of finals.json mapped both ``iao``
    and ``iang`` to ``c_1346`` (⠭) and the ``_note`` claimed they were
    homographs in Current Chinese Braille.  They are not — ``iao`` is
    ``c_345`` (⠜) and ``iang`` is ``c_1346`` (⠭).  A proofreader caught it
    on the word "聊天" (liao2 tian1): the software emitted ⠇⠭⠂... where it
    should have emitted ⠇⠜⠂... for the 聊 syllable.
    """

    def test_iao_final_is_c_345(self, profile):
        assert tuple(profile.finals["iao"]) == (3, 4, 5)

    def test_iang_final_is_c_1346(self, profile):
        assert tuple(profile.finals["iang"]) == (1, 3, 4, 6)

    def test_iao_and_iang_have_distinct_dots(self, profile):
        """Guard against a future "let's merge them again" mistake."""
        assert profile.finals["iao"] != profile.finals["iang"]

    def test_liao2_emits_l_iao_tone(self, ctx, profile):
        """End-to-end check on 聊 — the syllable from the bug report.

        Expected cells: [l(initial), iao(final), tone(2)].  The final
        cell dots must be (3,4,5), not the old buggy (1,3,4,6).
        """
        cells = translate_hanzi_char(
            HanziChar(surface="聊", reading="liao2", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_initial", "zh_final", "zh_tone"]
        assert cells[0].dots == profile.initials["l"]
        assert tuple(cells[1].dots) == (3, 4, 5)
        assert cells[2].dots == profile.tones["2"]


class TestUangUengDistinctFinals:
    """Regression parallel to :class:`TestIaoIangDistinctFinals`.

    Early ``finals.json`` mapped both ``uang`` and ``ueng`` to
    ``c_2356`` (⠶) and the ``_note`` claimed they were identical in form in
    Current Chinese Braille.
    They are not — ``uang`` is ``c_2356`` (⠶) and ``ueng`` is ``c_256``
    (⠲).  ``ueng`` only appears as the zero-initial syllable ``weng``
    (翁 / 嗡 / 瓮 / ...), which is why the bug went unnoticed for
    longer than iao did.
    """

    def test_uang_final_is_c_2356(self, profile):
        assert tuple(profile.finals["uang"]) == (2, 3, 5, 6)

    def test_ueng_final_is_c_256(self, profile):
        assert tuple(profile.finals["ueng"]) == (2, 5, 6)

    def test_uang_and_ueng_have_distinct_dots(self, profile):
        """Guard against a future "let's merge them again" mistake."""
        assert profile.finals["uang"] != profile.finals["ueng"]

    def test_weng1_emits_ueng_tone(self, ctx, profile):
        """End-to-end check on 翁 — the canonical ueng-final character.

        ``weng`` is a zero-initial syllable: w → drops, final = ueng.
        Expected cells: [ueng(final), tone(1)].  The final cell dots
        must be (2,5,6), not the old buggy (2,3,5,6).
        """
        cells = translate_hanzi_char(
            HanziChar(surface="翁", reading="weng1", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_final", "zh_tone"]
        assert tuple(cells[0].dots) == (2, 5, 6)
        assert cells[1].dots == profile.tones["1"]


class TestEAndOShareFinal:
    """Current Chinese Braille writes e/o with the same final cell.

    Orthographic ``bo`` must emit b + o(c_26), not b + Latin-like o(c_135).
    The uo final keeps c_135, so zero-initial ``wo`` remains distinct.
    """

    def test_o_final_is_c_26(self, profile):
        assert tuple(profile.finals["o"]) == (2, 6)

    def test_e_and_o_share_final(self, profile):
        assert profile.finals["o"] == profile.finals["e"]

    def test_uo_final_stays_c_135(self, profile):
        assert tuple(profile.finals["uo"]) == (1, 3, 5)

    def test_bo1_emits_b_o_tone(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="玻", reading="bo1", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_initial", "zh_final", "zh_tone"]
        assert cells[0].dots == profile.initials["b"]
        assert tuple(cells[1].dots) == (2, 6)
        assert cells[2].dots == profile.tones["1"]

    def test_wo3_still_emits_uo_tone(self, ctx, profile):
        cells = translate_hanzi_char(
            HanziChar(surface="我", reading="wo3", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_final", "zh_tone"]
        assert tuple(cells[0].dots) == (1, 3, 5)
        assert cells[1].dots == profile.tones["3"]


class TestSyllabicNasalInterjections:
    """Regression for the silent-rime-drop bug.

    Front-ends spell 嗯 as ``n``/``ng`` and 哼 as ``hng`` (narrow phonetic
    nasals), but Chinese braille writes 嗯 = en and 哼 = heng. The parser
    aliases them so a real finals cell is emitted — previously 嗯 (n2)
    silently produced only the bare initial-n cell with no warning at all.
    呣 (m) has no conventional braille syllable, so it must surface a
    MISSING_FINAL warning rather than drop the rime in silence.
    """

    def test_en_for_n2(self, ctx, profile):
        # 嗯 ń — conventional braille syllable en, zero initial.
        cells = translate_hanzi_char(
            HanziChar(surface="嗯", reading="n2", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_final", "zh_tone"]
        assert cells[0].dots == profile.finals["en"]
        assert cells[1].dots == profile.tones["2"]
        # The rime is no longer dropped, so no missing-final warning fires.
        assert not any(w.code == "MISSING_FINAL" for w in ctx.warnings)

    def test_en_for_ng2(self, ctx, profile):
        # 嗯 ńg — same conventional syllable en.
        cells = translate_hanzi_char(
            HanziChar(surface="嗯", reading="ng2", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_final", "zh_tone"]
        assert cells[0].dots == profile.finals["en"]

    def test_heng_for_hng(self, ctx, profile):
        # 哼 hng — conventional braille syllable heng (h + eng), no tone.
        cells = translate_hanzi_char(
            HanziChar(surface="哼", reading="hng", span=Span(0, 1)),
            ctx, profile,
        )
        assert _roles(cells) == ["zh_initial", "zh_final"]
        assert cells[0].dots == profile.initials["h"]
        assert cells[1].dots == profile.finals["eng"]
        assert not any(w.code == "MISSING_FINAL" for w in ctx.warnings)

    def test_m2_warns_instead_of_silent_drop(self, ctx, profile):
        # 呣 (m) has no conventional braille syllable — the empty,
        # non-syllabic final must warn rather than silently disappear.
        cells = translate_hanzi_char(
            HanziChar(surface="呣", reading="m2", span=Span(0, 1)),
            ctx, profile,
        )
        assert any(w.code == "MISSING_FINAL" for w in ctx.warnings)
        # The initial cell still stands in for the syllable; the key
        # property is that the dropped rime is now observable, not silent.
        assert cells[0].role == "zh_initial"
        assert cells[0].dots == profile.initials["m"]
