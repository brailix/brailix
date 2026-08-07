"""``Pipeline.profile_features`` — the same standard with a flag set apart.

A profile's JSON settles the braille standard, but a few of its flags are
genuinely a caller's choice *within* that standard. Whether Current Chinese
Braille writes a tone cell on every syllable or on none is the case that
brought this in: both are legitimate, the difference is visible in every
word, and asking a proofreading front-end to ship a second profile — or to
hand-edit JSON — to offer the choice would be absurd.

What has to hold for the seam to be safe rather than merely convenient:

* the override reaches the **cells**, not just the profile object;
* it moves the **fingerprint**, because it changes the braille and a cache
  keyed on an unmoved digest would serve the other setting's output;
* it never touches the profile it derives from, which another pipeline may
  still be compiling with.
"""

from __future__ import annotations

import pytest

from brailix.core.config import load_profile
from brailix.core.errors import ConfigurationError
from brailix.pipeline import Pipeline, block_hash

# A real reading engine: with no readings every syllable is a blank cell and
# a tone cell would have nothing to attach to.
pytest.importorskip("pypinyin")

_BASE = {"profile": "cn_current", "analyzer": "char", "resolver": "pypinyin"}

TEXT = "我在重庆"


def _cells(pipe: Pipeline, text: str = TEXT) -> str:
    return pipe.translate_text(text).render("unicode")


def _roles(pipe: Pipeline, text: str = TEXT) -> list[str]:
    """Every emitted cell's role, in order."""
    return [
        cell.role
        for block in pipe.translate_text(text).braille_ir.blocks
        for cell in block.cells
    ]


class TestTheOverrideReachesTheCells:
    def test_turning_tone_off_drops_a_cell_per_toned_syllable(self) -> None:
        marked = Pipeline(**_BASE)
        plain = Pipeline(**_BASE, profile_features={"zh.tone": False})
        # 我 wǒ / 在 zài / 重 chóng / 庆 qìng — four non-neutral tones, so
        # four fewer cells and nothing else moved.
        assert len(_cells(plain)) == len(_cells(marked)) - 4

    def test_only_the_tone_cells_go(self) -> None:
        """Read the roles, not the glyphs: dropping the tone cells from the
        marked compile must reproduce the unmarked one exactly. A weaker
        check (fewer cells) would pass for an override that also changed a
        final."""
        marked = _roles(Pipeline(**_BASE))
        plain = _roles(Pipeline(**_BASE, profile_features={"zh.tone": False}))
        assert "zh_tone" in marked
        assert [r for r in marked if r != "zh_tone"] == plain

    def test_explicitly_on_matches_the_shipped_profile(self) -> None:
        assert _cells(Pipeline(**_BASE, profile_features={"zh.tone": True})) == (
            _cells(Pipeline(**_BASE))
        )

    def test_a_scheme_that_fixes_its_own_rule_is_unaffected(self) -> None:
        """国家通用盲文方案 selects a tone strategy that reads a per-initial
        omission table instead of the flag, so the override is a no-op there
        — which is why a front-end offering the choice has to ask whether it
        applies rather than assume."""
        ncb = {**_BASE, "profile": "cn_ncb"}
        assert _cells(Pipeline(**ncb, profile_features={"zh.tone": False})) == (
            _cells(Pipeline(**ncb))
        )


class TestTheOverrideIsPartOfTheCompilationIdentity:
    def test_it_moves_the_fingerprint(self) -> None:
        assert Pipeline(**_BASE).fingerprint != Pipeline(
            **_BASE, profile_features={"zh.tone": False}
        ).fingerprint

    def test_setting_a_flag_to_what_it_already_was_does_not(self) -> None:
        """An override that changes nothing must not invalidate caches: the
        digest is over the resolved content, not over whether an override
        happened to be passed."""
        assert Pipeline(**_BASE).fingerprint == Pipeline(
            **_BASE, profile_features={"zh.tone": True}
        ).fingerprint

    def test_block_hash_moves_with_it(self) -> None:
        # The cache-poisoning guard: same text, same profile name, different
        # tone marking must not share a block cache key.
        marked = Pipeline(**_BASE)
        plain = Pipeline(**_BASE, profile_features={"zh.tone": False})
        block = marked.parse_text(TEXT).blocks[0]
        assert block_hash(
            block, "cn_current", fingerprint=marked.fingerprint
        ) != block_hash(block, "cn_current", fingerprint=plain.fingerprint)


class TestItDerivesRatherThanMutates:
    def test_the_loaded_profile_is_left_alone(self) -> None:
        Pipeline(**_BASE, profile_features={"zh.tone": False})
        assert load_profile("cn_current").feature("zh.tone") is True

    def test_two_pipelines_do_not_see_each_others_overrides(self) -> None:
        plain = Pipeline(**_BASE, profile_features={"zh.tone": False})
        marked = Pipeline(**_BASE)
        assert plain._profile.feature("zh.tone") is False
        assert marked._profile.feature("zh.tone") is True

    def test_every_other_part_of_the_profile_survives(self) -> None:
        """It is the same standard with one flag moved — the tables, the
        language and the name all have to come across, or the override would
        quietly be a different profile."""
        base = load_profile("cn_current")
        derived = base.with_features({"zh.tone": False})
        assert derived.name == base.name
        assert derived.language == base.language
        assert derived.lang_table("finals") == base.lang_table("finals")
        assert derived.punctuation == base.punctuation
        assert derived.feature("zh.tone_omit_neutral") is True

    def test_an_empty_override_returns_the_same_object(self) -> None:
        base = load_profile("cn_current")
        assert base.with_features({}) is base


class TestConstructionContract:
    def test_the_field_is_read_only_after_construction(self) -> None:
        pipe = Pipeline(**_BASE)
        with pytest.raises(AttributeError, match="read-only"):
            pipe.profile_features = {"zh.tone": False}

    def test_the_caller_cannot_mutate_what_they_passed(self) -> None:
        caller = {"zh.tone": False}
        pipe = Pipeline(**_BASE, profile_features=caller)
        caller["zh.tone"] = True
        assert pipe.profile_features["zh.tone"] is False

    @pytest.mark.parametrize(
        "value",
        [
            {"enabled": False},
            ["a", "b"],
            {"nested": {"deep": 1}},
            set(),
            bytearray(b"x"),
        ],
    )
    def test_a_container_value_is_refused(self, value: object) -> None:
        """The hole the previous test could not see.

        Freezing the mapping protects the mapping, not the objects inside
        it: a container value stayed shared with the caller, who could edit
        it after construction and change what this pipeline compiles — while
        ``fingerprint``, computed once from the old contents, stayed put. Two
        different outputs then wore one ``source_hash``, which is a cache
        serving the wrong braille rather than an error anyone would see.
        """
        with pytest.raises(ConfigurationError, match="a feature flag is a scalar"):
            Pipeline(**_BASE, profile_features={"plugin.option": value})

    @pytest.mark.parametrize("value", [True, False, None, 7, 1.5, "ncb_omission"])
    def test_every_scalar_shape_is_accepted(self, value: object) -> None:
        # The other half: restricting the value type must not narrow the
        # flags a profile can actually declare. The shipped profiles use
        # bool and str; int / float / None round out the JSON scalars.
        pipe = Pipeline(**_BASE, profile_features={"plugin.option": value})
        assert pipe._profile.feature("plugin.option") == value

    def test_a_container_is_refused_through_the_profile_api_too(self) -> None:
        # Both entry points share one write point, so neither can be the one
        # that forgot to check.
        base = load_profile("cn_current")
        with pytest.raises(ConfigurationError, match="a feature flag is a scalar"):
            base.with_features({"plugin.option": {"enabled": False}})

    def test_replace_carries_the_override(self) -> None:
        from dataclasses import replace

        pipe = Pipeline(**_BASE, profile_features={"zh.tone": False})
        assert replace(pipe, mode="strict").profile_features["zh.tone"] is False


class TestTheKeySpace:
    def test_a_group_the_profile_never_declared_is_created(self) -> None:
        pipe = Pipeline(**_BASE, profile_features={"made.up.flag": 7})
        assert pipe._profile.feature("made.up.flag") == 7
        assert pipe._profile.feature("zh.tone") is True

    def test_a_flat_key_lands_at_the_top_level_and_shadows_nothing(self) -> None:
        """An override is addressed by the *whole* path, groups included.

        ``{"tone": False}`` writes a top-level ``tone`` entry — a legal thing
        to write, since the features table is an open extension point and a
        plugin may declare a flag of its own there. What it is not is a way to
        reach ``zh.tone``: a feature is read at exactly one address, so the
        Chinese tone flag keeps its value and the braille is unchanged.

        It used to be a way, through a legacy flat→dotted alias map that made
        six flags answer to two names each. Pinned here as the behaviour it
        is now, because "my override did nothing" is a question this test's
        answer should be findable from.
        """
        pipe = Pipeline(**_BASE, profile_features={"tone": False})
        assert pipe._profile.features["tone"] is False
        assert pipe._profile.feature("zh.tone") is True
        assert _cells(pipe) == _cells(Pipeline(**_BASE))

    def test_the_grouped_key_is_what_changes_the_braille(self) -> None:
        pipe = Pipeline(**_BASE, profile_features={"zh.tone": False})
        assert len(_cells(pipe)) == len(_cells(Pipeline(**_BASE))) - 4

    def test_a_scalar_in_the_path_is_refused_loudly(self) -> None:
        """``zh.tone`` is a bool, so ``zh.tone.strict`` cannot be written
        without destroying it — and an override that silently ate the flag
        it was aimed at is the worst outcome available."""
        with pytest.raises(ConfigurationError, match="not a group of features"):
            Pipeline(**_BASE, profile_features={"zh.tone.strict": True})
