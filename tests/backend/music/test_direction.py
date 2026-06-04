"""Unit tests for M3.4: ``<direction>`` handler suite.

Covers BANA Table 22:
* (A-C) dynamics symbols (``<dynamics><p/></dynamics>`` etc.)
* (C) word-form expressions (``<words>cresc.</words>``)
* (C) hairpins (``<wedge type="crescendo|diminuendo|stop"/>``)

All cells funnel through the §6.4 template: feature gate
(``show_dynamics`` / ``show_words``) → ``nuances`` resource lookup
→ ``emit_cells_for_entity``. The hairpin tests also exercise the
``pending_hairpin`` state so a ``stop`` matches the right opener.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import MusicBrailleContext, emit_tree
from brailix.backend.music.dispatch import _emit_element
from brailix.core.config import load_profile
from brailix.core.context import BackendContext


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def _dots(cells):
    return [c.dots for c in cells]


def _roles(cells):
    return [c.role for c in cells]


# ---------------------------------------------------------------------------
# <dynamics> — symbol-form (pp / p / mf / f / ff)
# ---------------------------------------------------------------------------


def _wrap_direction(inner: str) -> ET.Element:
    """Wrap a marker in a full ``<direction>/<direction-type>`` chain
    so the dispatcher descends through the container layers."""
    return ET.fromstring(
        f"<direction><direction-type>{inner}</direction-type></direction>"
    )


class TestDynamicsSymbol:
    @pytest.mark.parametrize(
        "tag, expected_dots",
        [
            # dynamic_pp = ">pp" = (3,4,5)(1,2,3,4)(1,2,3,4)
            ("pp", [(3, 4, 5), (1, 2, 3, 4), (1, 2, 3, 4)]),
            # dynamic_p = ">p" = (3,4,5)(1,2,3,4)
            ("p",  [(3, 4, 5), (1, 2, 3, 4)]),
            # dynamic_mf = ">mf" = (3,4,5)(1,3,4)(1,2,4)
            ("mf", [(3, 4, 5), (1, 3, 4), (1, 2, 4)]),
            # dynamic_f  = ">f" = (3,4,5)(1,2,4)
            ("f",  [(3, 4, 5), (1, 2, 4)]),
            # dynamic_ff = ">ff" = (3,4,5)(1,2,4)(1,2,4)
            ("ff", [(3, 4, 5), (1, 2, 4), (1, 2, 4)]),
        ],
    )
    def test_known_symbols(self, profile, ctx, tag, expected_dots):
        direction = _wrap_direction(f"<dynamics><{tag}/></dynamics>")
        cells = emit_tree(direction, ctx, profile)
        assert _dots(cells) == expected_dots
        assert all(c.role == "music_dynamic" for c in cells)

    def test_multiple_symbols_in_one_block(self, profile, ctx):
        # <dynamics><p/><f/></dynamics> rare but legal — both emit.
        direction = _wrap_direction("<dynamics><p/><f/></dynamics>")
        cells = emit_tree(direction, ctx, profile)
        # >p + >f
        assert _dots(cells) == [
            (3, 4, 5), (1, 2, 3, 4),
            (3, 4, 5), (1, 2, 4),
        ]

    def test_unknown_symbol_synthesized(self, profile, ctx):
        # S2: <sfz/> not in pre-built nuances entries, but the
        # synthesized form ``>sfz`` = (3,4,5)(2,3,4)(1,2,4)(1,3,5,6)
        # gives a valid BANA word-form dynamic per Par. 22.3.
        direction = _wrap_direction("<dynamics><sfz/></dynamics>")
        cells = emit_tree(direction, ctx, profile)
        assert _dots(cells) == [(3, 4, 5), (2, 3, 4), (1, 2, 4), (1, 3, 5, 6)]
        assert all(c.role == "music_dynamic" for c in cells)
        # No warning — synthesis is a valid path, not a fallback.
        assert ctx.warnings.warnings == []

    def test_other_dynamics_synthesized(self, profile, ctx):
        # <other-dynamics>X</other-dynamics> — synthesize the X text.
        direction = _wrap_direction(
            "<dynamics><other-dynamics>fp</other-dynamics></dynamics>"
        )
        cells = emit_tree(direction, ctx, profile)
        # >fp = (3,4,5)(1,2,4)(1,2,3,4)
        assert _dots(cells) == [(3, 4, 5), (1, 2, 4), (1, 2, 3, 4)]

    def test_feature_gate_disables_all(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_dynamics",
            False,
        )
        direction = _wrap_direction("<dynamics><f/></dynamics>")
        cells = emit_tree(direction, ctx, profile)
        assert cells == []

    def test_unsupported_form_warns_but_falls_back(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "dynamics_form",
            "full",
        )
        direction = _wrap_direction("<dynamics><p/></dynamics>")
        cells = emit_tree(direction, ctx, profile)
        # Falls back to abbreviated form
        assert _dots(cells) == [(3, 4, 5), (1, 2, 3, 4)]
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# <words> — textual cues that map to BANA word-sign entries
# ---------------------------------------------------------------------------


class TestWords:
    @pytest.mark.parametrize(
        "text",
        ["cresc.", "cresc", "crescendo", "CRESC.", "  cresc  "],
    )
    def test_cresc_variants(self, profile, ctx, text):
        direction = _wrap_direction(f"<words>{text.strip()}</words>")
        cells = emit_tree(direction, ctx, profile)
        # dynamic_cresc = ">cr'" = (3,4,5)(1,4)(1,2,3,5)(3,)
        assert _dots(cells) == [(3, 4, 5), (1, 4), (1, 2, 3, 5), (3,)]
        assert all(c.role == "music_word" for c in cells)

    def test_decresc(self, profile, ctx):
        direction = _wrap_direction("<words>decresc.</words>")
        cells = emit_tree(direction, ctx, profile)
        # dynamic_decresc = ">decr'" = (3,4,5)(1,4,5)(1,5)(1,4)(1,2,3,5)(3,)
        assert _dots(cells)[0] == (3, 4, 5)  # word sign
        assert cells[-1].dots == (3,)         # abbreviation period

    def test_dimin(self, profile, ctx):
        direction = _wrap_direction("<words>dim.</words>")
        cells = emit_tree(direction, ctx, profile)
        # dynamic_dimin = ">dim'"
        assert cells[0].role == "music_word"
        assert cells[-1].dots == (3,)

    def test_unknown_single_word_synthesized(self, profile, ctx):
        # S2: "Allegro" — synthesized as ``>allegro`` (Par. 22.3 word
        # form). No leading period because the source text doesn't end
        # with one.
        direction = _wrap_direction("<words>Allegro</words>")
        cells = emit_tree(direction, ctx, profile)
        # >allegro = > + a + l + l + e + g + r + o
        assert _dots(cells)[0] == (3, 4, 5)            # >
        assert _dots(cells)[1] == (1,)                 # a
        assert _dots(cells)[2] == (1, 2, 3)            # l
        # No abbreviation period
        assert _dots(cells)[-1] != (3,)
        assert all(c.role == "music_word" for c in cells)
        assert ctx.warnings.warnings == []

    def test_single_word_ending_period_adds_period_cell(self, profile, ctx):
        direction = _wrap_direction("<words>poco.</words>")
        cells = emit_tree(direction, ctx, profile)
        # Last cell should be ``'`` = (3,) abbreviation period
        assert _dots(cells)[-1] == (3,)

    def test_multi_word_text_warns_without_translator(self, profile, ctx):
        # "poco a poco" — multi-word; with no inline_text_translator
        # wired (the bare test ctx), it defers with a warning.
        direction = _wrap_direction("<words>poco a poco</words>")
        cells = emit_tree(direction, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_multi_word_text_uses_injected_translator(self, profile):
        from brailix.core.context import BackendContext
        from brailix.ir.braille import BrailleCell

        marker = BrailleCell(dots=(1, 2, 3), role="latin")
        ctx = BackendContext(
            profile="cn_current",
            block_type="score",
            options={"inline_text_translator": lambda _t: [marker]},
        )
        direction = _wrap_direction("<words>poco a poco</words>")
        cells = emit_tree(direction, ctx, profile)
        # Translator output is spliced into the stream; no deferred warning.
        assert cells == [marker]
        assert not ctx.warnings.warnings

    def test_empty_words_silently_skipped(self, profile, ctx):
        direction = _wrap_direction("<words></words>")
        cells = emit_tree(direction, ctx, profile)
        assert cells == []
        # No warning for vendor's empty <words/> bookkeeping.
        assert ctx.warnings.warnings == []

    def test_feature_gate(self, profile, ctx, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_words",
            False,
        )
        direction = _wrap_direction("<words>cresc.</words>")
        cells = emit_tree(direction, ctx, profile)
        assert cells == []


# ---------------------------------------------------------------------------
# <wedge> — hairpins with paired open/stop markers
# ---------------------------------------------------------------------------


class TestWedge:
    def test_crescendo_opens_with_diverging_hairpin(self, profile, ctx):
        direction = _wrap_direction('<wedge type="crescendo"/>')
        cells = emit_tree(direction, ctx, profile)
        # diverging_hairpin = ">c" = (3,4,5)(1,4)
        assert _dots(cells) == [(3, 4, 5), (1, 4)]
        assert all(c.role == "music_hairpin" for c in cells)

    def test_diminuendo_opens_with_converging_hairpin(self, profile, ctx):
        direction = _wrap_direction('<wedge type="diminuendo"/>')
        cells = emit_tree(direction, ctx, profile)
        # converging_hairpin = ">d" = (3,4,5)(1,4,5)
        assert _dots(cells) == [(3, 4, 5), (1, 4, 5)]

    def test_stop_after_crescendo_emits_div_terminator(self, profile, ctx):
        # Same mctx for both wedges so pending_hairpin survives.
        mctx = MusicBrailleContext(profile=profile, backend=ctx)
        cells: list = []
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="crescendo"/>'),
        )
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="stop"/>'),
        )
        # diverging hairpin (2 cells) + diverging_hairpin_terminator (1 cell) = 3
        # diverging_hairpin_terminator = ">3" = (3,4,5)(2,5)
        assert _dots(cells)[-2:] == [(3, 4, 5), (2, 5)]

    def test_stop_after_diminuendo_emits_conv_terminator(
        self, profile, ctx,
    ):
        mctx = MusicBrailleContext(profile=profile, backend=ctx)
        cells: list = []
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="diminuendo"/>'),
        )
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="stop"/>'),
        )
        # converging_hairpin_terminator = ">4" = (3,4,5)(2,5,6)
        assert _dots(cells)[-2:] == [(3, 4, 5), (2, 5, 6)]

    def test_orphan_stop_silently_ignored(self, profile, ctx):
        # No prior opening — stop is a no-op (suggests upstream XML
        # inconsistency, not the score's intent; we don't warn).
        direction = _wrap_direction('<wedge type="stop"/>')
        cells = emit_tree(direction, ctx, profile)
        assert cells == []
        assert ctx.warnings.warnings == []

    def test_continue_is_noop(self, profile, ctx):
        # MusicXML <wedge type="continue"/> just confirms an ongoing
        # hairpin; no extra cells.
        mctx = MusicBrailleContext(profile=profile, backend=ctx)
        cells: list = []
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="crescendo"/>'),
        )
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="continue"/>'),
        )
        # Only the opening cells (no second pair)
        assert len(cells) == 2

    def test_unknown_type_warns(self, profile, ctx):
        direction = _wrap_direction('<wedge type="zigzag"/>')
        cells = emit_tree(direction, ctx, profile)
        assert cells == []
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes

    def test_feature_gate_disables_hairpin(self, profile, ctx, monkeypatch):
        # Same gate as <dynamics> — show_dynamics covers both.
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_dynamics",
            False,
        )
        direction = _wrap_direction('<wedge type="crescendo"/>')
        cells = emit_tree(direction, ctx, profile)
        assert cells == []

    def test_pending_hairpin_clears_after_stop(self, profile, ctx):
        # Open → stop → orphan stop should remain a no-op.
        mctx = MusicBrailleContext(profile=profile, backend=ctx)
        cells: list = []
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="crescendo"/>'),
        )
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="stop"/>'),
        )
        before_orphan = list(cells)
        _emit_element(
            cells, mctx,
            _wrap_direction('<wedge type="stop"/>'),
        )
        assert cells == before_orphan

    def test_two_hairpins_in_sequence(self, profile, ctx):
        # cresc → stop → dim → stop — each stop pairs with the most
        # recent opener.
        mctx = MusicBrailleContext(profile=profile, backend=ctx)
        cells: list = []
        for inner in (
            '<wedge type="crescendo"/>',
            '<wedge type="stop"/>',
            '<wedge type="diminuendo"/>',
            '<wedge type="stop"/>',
        ):
            _emit_element(cells, mctx, _wrap_direction(inner))
        # cresc-open (2) + cresc-stop (2) + dim-open (2) + dim-stop (2)
        assert len(cells) == 8
        # Verify which terminator was picked for each stop
        # cresc-stop = ">3" / dim-stop = ">4"
        assert _dots(cells)[2:4] == [(3, 4, 5), (2, 5)]   # cresc terminator
        assert _dots(cells)[6:8] == [(3, 4, 5), (2, 5, 6)]  # dim terminator


# ---------------------------------------------------------------------------
# Pipeline integration: dynamics + words + hairpin in a real score
# ---------------------------------------------------------------------------


SCORE_WITH_DYNAMICS_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<direction><direction-type><dynamics><p/></dynamics></direction-type></direction>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    '<direction><direction-type><wedge type="crescendo"/></direction-type></direction>'
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    '<direction><direction-type><wedge type="stop"/></direction-type></direction>'
    "<direction><direction-type><dynamics><f/></dynamics></direction-type></direction>"
    "<note><pitch><step>E</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestPipelineIntegration:
    def test_score_with_dynamics_emits_correct_role_sequence(
        self, profile, ctx,
    ):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SCORE_WITH_DYNAMICS_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        roles = [c.role for c in cells]

        # >p (2 cells) + octave (1) + C (1)
        # + cresc opener (2) + D (1)
        # + cresc terminator (2) + >f (2) + E (1)
        # — D and E are within 3rd of prev so no octave re-mark
        assert roles == [
            "music_dynamic", "music_dynamic",
            "music_octave", "music_note",
            "music_hairpin", "music_hairpin",
            "music_note",
            "music_hairpin", "music_hairpin",
            "music_dynamic", "music_dynamic",
            "music_note",
        ]
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []
