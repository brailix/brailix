"""Profile-consistency canary: one probe battery across EVERY shipped profile.

The golden corpus pins ``cn_current`` alone, so a "switch the profile and
the output goes blank" defect never ran anywhere — the adversarial review
found exactly that class sitting in ``ja_current`` (Latin case signs that
silently vanish, list markers that silently drop, full-width letters that
rendered as blank placeholder cells). This canary is the structural gate
for it:

* **prose probes** (Latin upper / lower, digits, the digit-then-letter
  re-arm shape, full-width letters, ordered / unordered lists) must
  translate, on every shipped profile, to non-empty output with **no
  placeholder cells** — a cell with no dots whose role is not ``space``
  is the "no rule found" placeholder, and the probe strings are chosen
  so that every shipped profile has a rule for every one of their
  characters;
* nothing here is **silent**: content-bearing probes may warn (a profile
  without ``letter_prefix.*`` warns ``LATIN_CASE_SIGN_MISSING``; a
  punct table without the list markers warns ``LIST_MARKER_UNMAPPED`` —
  both are registered resource debts announced per use), but the list
  marker specifically must either RENDER or WARN, never vanish;
* **math / music probes** are the two domains a shipped profile may
  legitimately not cover (``ja_current`` ships no math or music tables —
  a registered debt, not a defect), so they may soft-fail — but only
  loudly: placeholder cells there must ride warnings.

It is a canary, not a golden: no exact cells are pinned (that needs
humanly verified expectations per braille standard, which ``ja_current``
does not have yet). What is pinned is weaker and universal — every
shipped profile translates the prose basics to non-blank output and
degrades loudly — which is precisely the property whose absence let the
F2 / F3 / F7 class ship unnoticed.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix import Pipeline
from brailix.backend.music import translate_tree as music_translate_tree
from brailix.core.config.loader import iter_builtin_profiles, load_profile
from brailix.core.context import BackendContext
from brailix.input import parse_markdown

# "Shipped" = what the package itself carries (``brailix/profiles/``),
# not whatever user folders a front-end might add on top.
SHIPPED_PROFILES = iter_builtin_profiles()

# Prose probes every shipped profile must fully render. Keys name the
# defect shape each probe guards:
PROSE_PROBES: dict[str, str] = {
    "latin_lower": "abc",
    "latin_upper": "CPU",
    "digit_then_letter_rearm": "42ab",  # F2 shape: bare a–j is a digit cell
    "digits": "42",
    "fullwidth_latin": "Ａｂ",  # F7 shape: prose fold, not blank cells
}

# Inline math: the one prose-adjacent domain a profile may not cover.
MATH_PROBE = "$x+1$"

# One MusicXML note, driven through the music backend directly (music
# reaches the pipeline as a parsed embedded tree, not as markdown).
MUSIC_NOTE_XML = (
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
)


def _build_pipeline(profile_name: str) -> Pipeline:
    """One pinned-adapter Pipeline per profile, so the canary is
    deterministic regardless of which optional tokenizers are installed
    (the golden suite's discipline, applied here)."""
    if profile_name.startswith("ja"):
        return Pipeline(profile=profile_name, analyzer="kana")
    return Pipeline(profile=profile_name, analyzer="char", resolver="null")


@pytest.fixture(
    scope="module",
    params=SHIPPED_PROFILES,
    ids=SHIPPED_PROFILES,
)
def pipe(request: pytest.FixtureRequest) -> Pipeline:
    return _build_pipeline(request.param)


def _all_cells(result) -> list:
    return [c for block in result.braille_ir.blocks for c in block.cells]


def _placeholders(cells: list) -> list:
    """The "no rule found" cell: no dots, and not an orthographic space
    (a typed / synthesised separator blank is legitimate content)."""
    return [c for c in cells if not c.dots and c.role != "space"]


def _codes(result) -> list[str]:
    return [w.code for w in result.warnings]


class TestProseProbes:
    """Latin letters, digits and their boundary shapes render with no
    placeholder cells on every shipped profile."""

    def test_every_prose_probe_renders_fully(self, pipe):
        profile_name = pipe.profile_name
        for probe_name, probe in PROSE_PROBES.items():
            result = pipe.translate_text(probe)
            cells = _all_cells(result)
            assert cells, (
                f"{profile_name}: prose probe {probe_name!r} ({probe!r}) "
                f"produced no cells at all"
            )
            placeholders = _placeholders(cells)
            assert not placeholders, (
                f"{profile_name}: prose probe {probe_name!r} ({probe!r}) "
                f"rendered {len(placeholders)} blank placeholder cells "
                f"(roles: {sorted({c.role for c in placeholders})}) — a "
                f"shipped profile must have a rule for every character "
                f"of the probe battery"
            )

    def test_probe_characters_do_not_vanish(self, pipe):
        # Non-blank output is not enough: a defect can also DROP content
        # (the list-marker class). Every probe letter / digit must
        # contribute at least one content cell of its own kind.
        profile_name = pipe.profile_name
        for probe_name, probe in PROSE_PROBES.items():
            cells = _all_cells(pipe.translate_text(probe))
            letters = sum(1 for ch in probe if ch.isalpha())
            digits = sum(1 for ch in probe if ch.isdigit())
            assert sum(1 for c in cells if c.role == "latin_letter") >= letters, (
                f"{profile_name}: probe {probe_name!r} lost letters "
                f"({letters} in the source, fewer latin_letter cells out)"
            )
            assert sum(1 for c in cells if c.role == "digit") >= digits, (
                f"{profile_name}: probe {probe_name!r} lost digits "
                f"({digits} in the source, fewer digit cells out)"
            )


class TestListProbes:
    """List markers render — or announce their absence — on every shipped
    profile. A marker that neither renders nor warns is the F3 defect
    this gate exists for."""

    def test_list_markers_render_or_warn(self, pipe):
        profile_name = pipe.profile_name
        doc = parse_markdown(
            "- one\n\n1. one\n",
            language=pipe.profile_language,
            profile=profile_name,
        )
        result = pipe.translate_document(doc)
        cells = _all_cells(result)
        assert cells, f"{profile_name}: list probes produced no cells"
        assert not _placeholders(cells), (
            f"{profile_name}: list probes rendered blank placeholder cells"
        )
        # Two items, one unordered one ordered: BOTH markers must be
        # accounted for — rendered, or covered by the drop warning.
        codes = _codes(result)
        if "LIST_MARKER_UNMAPPED" not in codes:
            assert sum(1 for c in cells if c.role == "list_marker") >= 2, (
                f"{profile_name}: list markers neither rendered nor warned "
                f"(F3 class: silent marker loss)"
            )


class TestMathProbe:
    """Inline math may soft-fail (a profile without math tables is a
    registered debt) — but never silently."""

    def test_math_renders_or_warns(self, pipe):
        profile_name = pipe.profile_name
        result = pipe.translate_text(MATH_PROBE)
        cells = _all_cells(result)
        assert cells, f"{profile_name}: math probe produced no cells"
        if _placeholders(cells):
            assert _codes(result), (
                f"{profile_name}: math probe rendered blank placeholder "
                f"cells with no warning — soft failure must be loud"
            )


class TestMusicProbe:
    """Same contract as the math probe, on the music backend: a profile
    without music tables may degrade to placeholder cells, and if it
    does, the warnings panel must say so."""

    def test_music_renders_or_warns(self, pipe):
        profile_name = pipe.profile_name
        profile = load_profile(profile_name)
        ctx = BackendContext(profile=profile_name, block_type="score")
        cells = music_translate_tree(ET.fromstring(MUSIC_NOTE_XML), ctx, profile)
        assert cells, f"{profile_name}: music probe produced no cells"
        if any(c.role == "music_unknown" for c in cells):
            assert len(ctx.warnings) > 0, (
                f"{profile_name}: music probe degraded to placeholder "
                f"cells with no warning — soft failure must be loud"
            )


def test_canary_actually_covers_profiles() -> None:
    """Regex-rot guard for the battery itself: the shipped set the canary
    parametrizes over must include each language family, or the whole
    gate silently collapses to one profile (the F9 blind spot again)."""
    assert set(SHIPPED_PROFILES) >= {"cn_current", "cn_ncb", "ja_current"}
