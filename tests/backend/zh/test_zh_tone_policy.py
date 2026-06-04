"""Tests for the tone-emission policy module.

Two layers:

* Unit tests on the policy classes themselves, with synthetic
  ``ParsedPinyin`` inputs.
* Integration tests that load the ``cn_ncb`` profile end-to-end
  via :func:`brailix.core.config.load_profile` and translate a
  syllable through :mod:`brailix.backend.zh`, verifying the tone cell
  is or isn't emitted according to the NCB rules.

The integration tests double as a smoke check that:

* the new profile loads cleanly,
* the policy factory picks ``NcbOmissionPolicy``,
* the JSON resource is locatable from the configured path.
"""

from __future__ import annotations

import pytest

from brailix.backend.zh import translate_word
from brailix.backend.zh.pinyin_parser import ParsedPinyin, parse_pinyin
from brailix.backend.zh.tone import (
    build_tone_policy,
    register,
    registered_names,
    tone_policy_for,
)
from brailix.backend.zh.tone.basic import BasicTonePolicy
from brailix.backend.zh.tone.ncb_omission import NcbOmissionPolicy
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.ir.inline import Word

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_tone(cells) -> bool:
    return any(c.role == "zh_tone" for c in cells)


@pytest.fixture(scope="module")
def cn_current():
    return load_profile("cn_current")


@pytest.fixture(scope="module")
def cn_ncb():
    return load_profile("cn_ncb")


@pytest.fixture
def ctx():
    return BackendContext()


# ---------------------------------------------------------------------------
# BasicTonePolicy: legacy flag-driven behavior
# ---------------------------------------------------------------------------


class TestBasicTonePolicy:
    def test_default_emits_1_through_4(self, cn_current):
        p = BasicTonePolicy(profile=cn_current)
        for tone in ("1", "2", "3", "4"):
            assert p.should_emit_tone(
                syllable=f"ma{tone}",
                parsed=ParsedPinyin(initial="m", final="a", tone=tone),
            )

    def test_default_suppresses_neutral(self, cn_current):
        p = BasicTonePolicy(profile=cn_current)
        assert not p.should_emit_tone(
            syllable="ma5",
            parsed=ParsedPinyin(initial="m", final="a", tone="5"),
        )

    def test_default_suppresses_when_no_tone(self, cn_current):
        p = BasicTonePolicy(profile=cn_current)
        assert not p.should_emit_tone(
            syllable="ma",
            parsed=ParsedPinyin(initial="m", final="a", tone=""),
        )

    def test_flag_off_disables(self, cn_current):
        cn_current.features["tone"] = False
        try:
            p = BasicTonePolicy(profile=cn_current)
            assert not p.should_emit_tone(
                syllable="ma2",
                parsed=ParsedPinyin(initial="m", final="a", tone="2"),
            )
        finally:
            del cn_current.features["tone"]

    def test_neutral_flag_off_emits_5(self, cn_current):
        cn_current.features["tone_omit_neutral"] = False
        try:
            p = BasicTonePolicy(profile=cn_current)
            assert p.should_emit_tone(
                syllable="ma5",
                parsed=ParsedPinyin(initial="m", final="a", tone="5"),
            )
        finally:
            del cn_current.features["tone_omit_neutral"]


# ---------------------------------------------------------------------------
# NcbOmissionPolicy: per-initial omission + exceptions
# ---------------------------------------------------------------------------


class TestNcbOmissionPolicy:
    """The cn_ncb profile loads this policy through the factory.

    Each test asserts the rule from a specific lesson of
    《国家通用盲文自学辅导讲义》 (the NCB self-study guide) — cross-reference
    the lesson number in the test name so failures point straight at the
    source rule.
    """

    @pytest.fixture(scope="class")
    def policy(self, cn_ncb):
        return build_tone_policy(cn_ncb)

    def test_factory_returns_ncb_policy(self, policy):
        assert isinstance(policy, NcbOmissionPolicy)

    # -- Lesson 2: b/p/m/f ----------------------------------------------

    def test_lesson_2_b_omits_tone_4(self, policy):
        # ban4 → ban (b omits tone 4)
        assert not policy.should_emit_tone(
            syllable="ban4",
            parsed=parse_pinyin("ban4"),
        )

    def test_lesson_2_b_keeps_other_tones(self, policy):
        # ban1 → ban1 (b omits only tone 4)
        assert policy.should_emit_tone(
            syllable="ban1",
            parsed=parse_pinyin("ban1"),
        )

    def test_lesson_2_f_omits_tone_1(self, policy):
        # fa1 → fa
        assert not policy.should_emit_tone(
            syllable="fa1",
            parsed=parse_pinyin("fa1"),
        )

    # -- Lesson 3: tou / le4 exceptions ---------------------------------

    def test_lesson_3_tou_never_omitted(self, policy):
        # tou1/tou2/tou3/tou4 all keep their tone — because toneless "tou"
        # is a neutral tone.
        for syl in ("tou1", "tou2", "tou3", "tou4"):
            assert policy.should_emit_tone(
                syllable=syl,
                parsed=parse_pinyin(syl),
            ), f"{syl} should keep its tone (Lesson 3 exception)"

    def test_lesson_3_other_t_syllables_follow_omit_2(self, policy):
        # tai2 (second tone) → tai (omitted)
        assert not policy.should_emit_tone(
            syllable="tai2",
            parsed=parse_pinyin("tai2"),
        )

    def test_lesson_3_le4_not_omitted(self, policy):
        # le4 not omitted, because toneless "le" is always a neutral tone.
        assert policy.should_emit_tone(
            syllable="le4",
            parsed=parse_pinyin("le4"),
        )

    def test_lesson_3_other_l_tone_4_omitted(self, policy):
        # lu4 → lu (l omits tone 4)
        assert not policy.should_emit_tone(
            syllable="lu4",
            parsed=parse_pinyin("lu4"),
        )

    # -- Lesson 7: zi4 exception ----------------------------------------

    def test_lesson_7_zi4_not_omitted(self, policy):
        assert policy.should_emit_tone(
            syllable="zi4",
            parsed=parse_pinyin("zi4"),
        )

    # -- Lesson 8: zero-initial -----------------------------------------

    def test_lesson_8_zero_initial_tone_4_default_omit(self, policy):
        # ai4 → ai
        assert not policy.should_emit_tone(
            syllable="ai4",
            parsed=parse_pinyin("ai4"),
        )

    def test_lesson_8_wo3_in_omit_list(self, policy):
        assert not policy.should_emit_tone(
            syllable="wo3",
            parsed=parse_pinyin("wo3"),
        )

    def test_lesson_8_e2_kept(self, policy):
        # e2 (鹅) is kept — e1-e4 are in keep_syllables.
        assert policy.should_emit_tone(
            syllable="e2",
            parsed=parse_pinyin("e2"),
        )

    def test_lesson_8_o3_omitted(self, policy):
        # o1-o4 (interjections) all omitted.
        assert not policy.should_emit_tone(
            syllable="o3",
            parsed=parse_pinyin("o3"),
        )

    # -- Rule 5 reverse exception: yi4/er4/wo4/ye4/you4 ----------------
    #
    # Per scratch_1.txt §9.5 (the syllables yī, ér, wǒ, yě, yǒu omit their
    # tone mark; the syllables yì, èr, wò, yè, yòu do NOT omit it). Without
    # these in keep, they would wrongly be omitted by
    # zero_initial.default_omit_tone=4.

    def test_lesson_8_wo4_kept_to_distinguish_from_wo3(self, policy):
        # 沃 (wo4) keeps tone — distinguishes from 我 (wo3, omitted).
        assert policy.should_emit_tone(
            syllable="wo4",
            parsed=parse_pinyin("wo4"),
        )

    def test_lesson_8_yi4_kept(self, policy):
        # 友谊 example: yì (yi4) is kept, contrasted with you3 omit.
        assert policy.should_emit_tone(
            syllable="yi4",
            parsed=parse_pinyin("yi4"),
        )

    def test_lesson_8_er4_kept(self, policy):
        assert policy.should_emit_tone(
            syllable="er4",
            parsed=parse_pinyin("er4"),
        )

    def test_lesson_8_ye4_kept(self, policy):
        # 事业 example shi4/ye4 — ye4 kept.
        assert policy.should_emit_tone(
            syllable="ye4",
            parsed=parse_pinyin("ye4"),
        )

    def test_lesson_8_you4_kept(self, policy):
        assert policy.should_emit_tone(
            syllable="you4",
            parsed=parse_pinyin("you4"),
        )

    def test_lesson_8_wo2_naturally_kept(self, policy):
        # Other-tone variants (1-3 for wo/ye/you, 2-3 for yi, 1/3-4 for er)
        # are kept naturally — default_omit_tone is 4, and these aren't 4.
        # Tests the rule-5 list is precise (only the listed tone is omitted).
        assert policy.should_emit_tone(
            syllable="wo2",
            parsed=parse_pinyin("wo2"),
        )

    # -- Lesson 9: boundary rule ----------------------------------------

    def test_lesson_9_boundary_keeps_syllabic_i_initial(self, policy):
        # ci2/ai4: ci2 normally would be omitted (initial c omits tone 2);
        # boundary rule keeps it because ai is zero-initial.
        assert policy.should_emit_tone(
            syllable="ci2",
            parsed=parse_pinyin("ci2"),
            next_syllable="ai4",
            next_parsed=parse_pinyin("ai4"),
        )

    def test_lesson_9_boundary_inactive_when_next_has_initial(self, policy):
        # ci2/bai (next has initial b) — boundary does NOT fire,
        # so ci2 follows the c→omit-2 rule and tone is omitted.
        assert not policy.should_emit_tone(
            syllable="ci2",
            parsed=parse_pinyin("ci2"),
            next_syllable="bai",
            next_parsed=parse_pinyin("bai"),
        )

    def test_lesson_9_boundary_inactive_at_word_end(self, policy):
        # next_syllable=None: boundary cannot apply; default c→omit-2 wins.
        assert not policy.should_emit_tone(
            syllable="ci2",
            parsed=parse_pinyin("ci2"),
        )

    # -- Neutral tone (always suppressed) -------------------------------

    def test_neutral_tone_always_suppressed(self, policy):
        assert not policy.should_emit_tone(
            syllable="zi5",
            parsed=ParsedPinyin(initial="z", final="", tone="5"),
        )


# ---------------------------------------------------------------------------
# End-to-end: cn_ncb profile + zh backend
# ---------------------------------------------------------------------------


class TestEndToEnd:
    """Smoke tests that go all the way through translate_word."""

    def test_cn_ncb_profile_loads(self, cn_ncb):
        assert cn_ncb.name == "cn_ncb"
        # Reuses current tables — both initials and finals are populated.
        assert "b" in cn_ncb.initials
        assert "ai" in cn_ncb.finals

    def test_cn_ncb_emits_ban1_with_tone(self, ctx, cn_ncb):
        # ban1 (b initial, omit_tone=4) — tone 1 not in omit set → keep.
        cells = translate_word(
            Word(surface="般", reading="ban1"),
            ctx, cn_ncb,
        )
        assert _has_tone(cells)

    def test_cn_ncb_omits_ban4(self, ctx, cn_ncb):
        # ban4 (b initial, omit_tone=4) → no tone cell.
        cells = translate_word(
            Word(surface="办", reading="ban4"),
            ctx, cn_ncb,
        )
        assert not _has_tone(cells)

    def test_cn_ncb_keeps_zi4(self, ctx, cn_ncb):
        # zi4 is a per-initial exception.
        cells = translate_word(
            Word(surface="字", reading="zi4"),
            ctx, cn_ncb,
        )
        assert _has_tone(cells)

    def test_cn_ncb_boundary_keeps_ci2_before_zero_initial(
        self, ctx, cn_ncb
    ):
        # Within a word, ci2/ai4: boundary rule should keep ci2's tone,
        # omit ai4's. Confirmed against scratch_1.txt §9.7: 慈爱 → ⠉⠂⠪.
        cells = translate_word(
            Word(surface="慈爱", reading="ci2 ai4"),
            ctx, cn_ncb,
        )
        tone_cells = [c for c in cells if c.role == "zh_tone"]
        # Exactly one tone cell expected — ci2's, not ai4's.
        assert len(tone_cells) == 1
        assert tone_cells[0].dots == cn_ncb.tones["2"]

    def test_cn_ncb_shiye_keeps_both_tones(self, ctx, cn_ncb):
        # 事业 shi4/ye4 — per scratch_1.txt §9.7: ⠱⠆⠑⠆.
        # shi4: kept by boundary rule (syllabic-i + next zero-initial).
        # ye4: kept by rule 5 reverse exception (ye4 in keep_syllables).
        cells = translate_word(
            Word(surface="事业", reading="shi4 ye4"),
            ctx, cn_ncb,
        )
        tone_cells = [c for c in cells if c.role == "zh_tone"]
        assert len(tone_cells) == 2
        # Both should be tone-4 cells.
        for tc in tone_cells:
            assert tc.dots == cn_ncb.tones["4"]

    def test_cn_current_still_uses_basic_policy(self, ctx, cn_current):
        # Sanity: cn_current did not opt into ncb_omission, so the
        # policy resolver returns Basic and tones 1-4 are all emitted.
        policy = tone_policy_for(cn_current)
        assert isinstance(policy, BasicTonePolicy)
        cells = translate_word(
            Word(surface="办", reading="ban4"),
            ctx, cn_current,
        )
        assert _has_tone(cells)

    def test_cross_word_boundary_two_hanzichars(self, ctx, cn_ncb):
        # Cross-IR-node boundary: 慈 + 爱 as two separate HanziChar
        # nodes (no Space between) should also trigger the Lesson-9
        # boundary — same outcome as the Word("慈爱") case.
        from brailix.backend.block import translate_document
        from brailix.ir.document import DocumentIR, Paragraph
        from brailix.ir.inline import HanziChar
        doc = DocumentIR(blocks=[Paragraph(children=[
            HanziChar(surface="慈", reading="ci2"),
            HanziChar(surface="爱", reading="ai4"),
        ])])
        braille = translate_document(doc, ctx, cn_ncb)
        cells = braille.blocks[0].cells
        tone_cells = [c for c in cells if c.role == "zh_tone"]
        assert len(tone_cells) == 1, (
            "cross-Word boundary should keep ci2's tone (and omit ai4's "
            "per zero-initial default-omit-4)"
        )
        assert tone_cells[0].dots == cn_ncb.tones["2"]

    def test_cross_word_boundary_breaks_on_space(self, ctx, cn_ncb):
        # When a Space sits between the two HanziChars, they're not
        # written together as one word; boundary rule must NOT fire and ci2
        # gets omitted per the c-initial rule.
        from brailix.backend.block import translate_document
        from brailix.ir.document import DocumentIR, Paragraph
        from brailix.ir.inline import HanziChar, Space
        doc = DocumentIR(blocks=[Paragraph(children=[
            HanziChar(surface="慈", reading="ci2"),
            Space(),
            HanziChar(surface="爱", reading="ai4"),
        ])])
        braille = translate_document(doc, ctx, cn_ncb)
        cells = braille.blocks[0].cells
        tone_cells = [c for c in cells if c.role == "zh_tone"]
        assert tone_cells == [], (
            "Space between 慈 and 爱 breaks 连写 — ci2's tone should be "
            "omitted by the standard c-omit-2 rule"
        )

    def test_cross_word_boundary_inactive_when_next_has_initial(
        self, ctx, cn_ncb
    ):
        # 慈 (ci2, syllabic-i) followed by 北 (bei3, has b initial) —
        # boundary rule doesn't fire because next is not zero-initial.
        from brailix.backend.block import translate_document
        from brailix.ir.document import DocumentIR, Paragraph
        from brailix.ir.inline import HanziChar
        doc = DocumentIR(blocks=[Paragraph(children=[
            HanziChar(surface="慈", reading="ci2"),
            HanziChar(surface="北", reading="bei3"),
        ])])
        braille = translate_document(doc, ctx, cn_ncb)
        cells = braille.blocks[0].cells
        tone_cells = [c for c in cells if c.role == "zh_tone"]
        # ci2: c-omit-2, no boundary → omitted.
        # bei3: b-omit-4, tone 3 → kept.
        # Only one tone cell, and it should be tone-3.
        assert len(tone_cells) == 1
        assert tone_cells[0].dots == cn_ncb.tones["3"]

    def test_policy_does_not_leak_across_freshly_loaded_profiles(self, ctx):
        # Regression: an earlier version cached policies keyed on
        # id(profile). When pytest dropped a module-scoped NCB profile,
        # the policy entry outlived it (NcbOmissionPolicy holds no
        # profile reference), and Python could reuse that address for a
        # subsequently-loaded cn_current — silently returning the NCB
        # policy and dropping tones in random integration tests.
        # Loading both fresh in sequence here exercises the keying.
        ncb = load_profile("cn_ncb")
        ncb_policy = tone_policy_for(ncb)
        assert isinstance(ncb_policy, NcbOmissionPolicy)

        default = load_profile("cn_current")
        default_policy = tone_policy_for(default)
        assert isinstance(default_policy, BasicTonePolicy)

        # zai4 must emit tone under cn_current's Basic policy even
        # though cn_ncb's NCB policy would omit it.
        cells = translate_word(
            Word(surface="在", reading="zai4"),
            ctx, default,
        )
        assert _has_tone(cells)


# ---------------------------------------------------------------------------
# Registry behavior — ARCHITECTURE.md §7.3
# ---------------------------------------------------------------------------


class TestRegistry:
    """The :mod:`brailix.backend.zh.tone` registry is the integration
    seam for new tone standards.  These tests pin the contract:
    builtins are present; a third party can register a new strategy
    and select it via a profile; the lookup error lists known names."""

    def test_builtin_strategies_are_registered(self):
        names = registered_names()
        assert "basic" in names
        assert "ncb_omission" in names

    def test_unknown_strategy_error_lists_registered_names(
        self, cn_ncb
    ):
        from brailix.core.errors import ConfigurationError

        cn_ncb.features["zh"]["tone_strategy"] = "no_such_strategy"
        try:
            with pytest.raises(ConfigurationError) as excinfo:
                tone_policy_for(cn_ncb)
            msg = str(excinfo.value)
            assert "no_such_strategy" in msg
            # Error message must enumerate known names so the user
            # can fix the typo without grepping source.
            assert "basic" in msg
            assert "ncb_omission" in msg
        finally:
            cn_ncb.features["zh"]["tone_strategy"] = "ncb_omission"

    def test_third_party_strategy_can_register_and_apply(
        self, ctx, cn_ncb
    ):
        """A plugin / out-of-tree caller registers a custom strategy
        and the profile picks it up by name.  No changes to backend.zh
        or its tone package itself.  This is what ARCHITECTURE.md
        §7.3 calls out: an adapter is imported only when first requested —
        third-party adapters live wherever, register their name, and
        get dispatched."""
        from brailix.backend.zh.pinyin_parser import ParsedPinyin
        from brailix.backend.zh.tone import _REGISTRY

        # Sentinel strategy: emits iff the syllable starts with 'x'.
        class _XStrategy:
            def should_emit_tone(self, *, syllable, parsed, **_):
                return syllable.startswith("x")

        # Snapshot + register + restore — tests are leak-proof even
        # if a future builder partially fails after registration.
        saved = dict(_REGISTRY)
        try:
            register("test_plugin", lambda profile: _XStrategy())
            assert "test_plugin" in registered_names()

            # Wire a profile at this strategy.
            cn_ncb.features["zh"]["tone_strategy"] = "test_plugin"
            policy = tone_policy_for(cn_ncb)
            assert isinstance(policy, _XStrategy)
            assert policy.should_emit_tone(
                syllable="xi1",
                parsed=ParsedPinyin(initial="x", final="i", tone="1"),
            )
            assert not policy.should_emit_tone(
                syllable="ma2",
                parsed=ParsedPinyin(initial="m", final="a", tone="2"),
            )
        finally:
            cn_ncb.features["zh"]["tone_strategy"] = "ncb_omission"
            _REGISTRY.clear()
            _REGISTRY.update(saved)

    def test_register_is_idempotent(self):
        """Re-registering the same name is a no-op — test fixtures
        and module reloads don't need teardown gymnastics."""
        from brailix.backend.zh.tone import _REGISTRY

        saved = dict(_REGISTRY)
        try:
            first = lambda profile: None  # noqa: E731
            second = lambda profile: None  # noqa: E731
            register("test_idempotent", first)
            register("test_idempotent", second)
            # First registration wins (silently kept).
            assert _REGISTRY["test_idempotent"] is first
        finally:
            _REGISTRY.clear()
            _REGISTRY.update(saved)
