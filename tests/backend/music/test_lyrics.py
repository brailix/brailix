"""Unit tests for M5: lyric markers (BANA Tables 31-32, marker form).

The M5 first-pass design emits one ``word_sign`` cell per ``<lyric>``
element attached to a note, carrying the lyric metadata in
``source_text``. Verifies:

* show_lyrics gate suppresses cleanly,
* one marker per ``<lyric>`` child, multiple verses → multiple markers,
* syllabic and verse number land in ``source_text``,
* lyrics_form ≠ "marker" warns and falls back,
* empty / missing ``<text>`` is silently skipped.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.music import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.ir.braille import BrailleCell


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current", block_type="score")


def _roles(cells):
    return [c.role for c in cells]


def _note_with_lyrics(lyrics_xml: str, step: str = "C", octave: int = 4) -> ET.Element:
    return ET.fromstring(
        "<note>"
        f"<pitch><step>{step}</step><octave>{octave}</octave></pitch>"
        "<duration>1</duration><type>quarter</type>"
        f"{lyrics_xml}"
        "</note>"
    )


# ---------------------------------------------------------------------------
# Marker emission
# ---------------------------------------------------------------------------


class TestLyricMarker:
    def test_single_lyric_emits_one_marker(self, profile, ctx):
        note = _note_with_lyrics(
            '<lyric number="1"><syllabic>single</syllabic><text>la</text></lyric>'
        )
        cells = emit_tree(note, ctx, profile)
        markers = [c for c in cells if c.role == "music_lyric_marker"]
        assert len(markers) == 1
        # word_sign = ">" = (3,4,5)
        assert markers[0].dots == (3, 4, 5)
        # source_text records verse + syllabic + text
        assert markers[0].source_text == "lyric[1/single]:la"

    def test_marker_comes_after_note(self, profile, ctx):
        note = _note_with_lyrics(
            '<lyric><syllabic>single</syllabic><text>hi</text></lyric>'
        )
        roles = _roles(emit_tree(note, ctx, profile))
        # [octave, note, marker]
        assert roles == ["music_octave", "music_note", "music_lyric_marker"]

    def test_multiple_verses_emit_multiple_markers(self, profile, ctx):
        note = _note_with_lyrics(
            '<lyric number="1"><syllabic>single</syllabic><text>春</text></lyric>'
            '<lyric number="2"><syllabic>single</syllabic><text>夏</text></lyric>'
        )
        markers = [c for c in emit_tree(note, ctx, profile)
                   if c.role == "music_lyric_marker"]
        assert len(markers) == 2
        # Verse number recorded
        assert "lyric[1/" in markers[0].source_text
        assert "lyric[2/" in markers[1].source_text
        assert markers[0].source_text.endswith(":春")
        assert markers[1].source_text.endswith(":夏")

    @pytest.mark.parametrize(
        "syllabic",
        ["single", "begin", "middle", "end"],
    )
    def test_syllabic_recorded(self, profile, ctx, syllabic):
        note = _note_with_lyrics(
            f'<lyric><syllabic>{syllabic}</syllabic><text>ah</text></lyric>'
        )
        markers = [c for c in emit_tree(note, ctx, profile)
                   if c.role == "music_lyric_marker"]
        assert markers[0].source_text == f"lyric[1/{syllabic}]:ah"

    def test_missing_syllabic_defaults_to_single(self, profile, ctx):
        # <syllabic> is optional; absent => default "single".
        note = _note_with_lyrics('<lyric><text>oh</text></lyric>')
        markers = [c for c in emit_tree(note, ctx, profile)
                   if c.role == "music_lyric_marker"]
        assert markers[0].source_text == "lyric[1/single]:oh"

    def test_default_verse_number_is_one(self, profile, ctx):
        # <lyric> without ``number`` attribute => verse 1.
        note = _note_with_lyrics('<lyric><text>x</text></lyric>')
        markers = [c for c in emit_tree(note, ctx, profile)
                   if c.role == "music_lyric_marker"]
        assert markers[0].source_text == "lyric[1/single]:x"


# ---------------------------------------------------------------------------
# Skip cases
# ---------------------------------------------------------------------------


class TestSkipCases:
    def test_no_lyric_no_marker(self, profile, ctx):
        note = _note_with_lyrics("")
        roles = _roles(emit_tree(note, ctx, profile))
        assert "music_lyric_marker" not in roles

    def test_empty_text_skipped_silently(self, profile, ctx):
        note = _note_with_lyrics('<lyric><text></text></lyric>')
        cells = emit_tree(note, ctx, profile)
        assert "music_lyric_marker" not in _roles(cells)
        # No warning for empty text — exporters frequently emit
        # placeholder <lyric><text/></lyric> for un-set syllables.
        assert ctx.warnings.warnings == []

    def test_missing_text_element_skipped(self, profile, ctx):
        # <lyric> without <text> — skip silently.
        note = _note_with_lyrics('<lyric><syllabic>single</syllabic></lyric>')
        cells = emit_tree(note, ctx, profile)
        assert "music_lyric_marker" not in _roles(cells)
        assert ctx.warnings.warnings == []

    def test_whitespace_only_text_skipped(self, profile, ctx):
        note = _note_with_lyrics('<lyric><text>   </text></lyric>')
        cells = emit_tree(note, ctx, profile)
        assert "music_lyric_marker" not in _roles(cells)


# ---------------------------------------------------------------------------
# Feature gates
# ---------------------------------------------------------------------------


class TestFeatureGates:
    def test_show_lyrics_false_suppresses_marker(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "show_lyrics",
            False,
        )
        note = _note_with_lyrics('<lyric><text>la</text></lyric>')
        cells = emit_tree(note, ctx, profile)
        assert "music_lyric_marker" not in _roles(cells)

    @pytest.mark.parametrize(
        "form",
        ["below_score", "interleaved"],
    )
    def test_other_lyrics_form_warns_falls_back(
        self, profile, ctx, monkeypatch, form,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}),
            "lyrics_form",
            form,
        )
        note = _note_with_lyrics('<lyric><text>la</text></lyric>')
        cells = emit_tree(note, ctx, profile)
        # Falls back to marker — still emits.
        assert "music_lyric_marker" in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Inline form (M5.x): real characters via the injected translator
# ---------------------------------------------------------------------------


class TestLyricsInline:
    def test_inline_translates_to_lyric_cells(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}), "lyrics_form", "inline",
        )
        # Fake translator standing in for the zh / latin text path.
        fake = BrailleCell(dots=(1, 3, 4, 5), role="hanzi_final", source_text="春")
        ctx = BackendContext(
            profile="cn_current", block_type="score",
            options={"inline_text_translator": lambda _t: [fake]},
        )
        note = _note_with_lyrics('<lyric><text>春</text></lyric>')
        cells = emit_tree(note, ctx, profile)
        lyric_cells = [c for c in cells if c.role == "music_lyric"]
        assert len(lyric_cells) == 1
        # translator's cell, retagged music_lyric, dots preserved
        assert lyric_cells[0].dots == (1, 3, 4, 5)
        assert "music_lyric_marker" not in _roles(cells)

    def test_inline_multiple_lyrics_each_translated(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}), "lyrics_form", "inline",
        )
        fake = BrailleCell(dots=(1,), role="latin_letter", source_text="x")
        ctx = BackendContext(
            profile="cn_current", block_type="score",
            options={"inline_text_translator": lambda _t: [fake]},
        )
        note = _note_with_lyrics(
            '<lyric number="1"><text>春</text></lyric>'
            '<lyric number="2"><text>夏</text></lyric>'
        )
        cells = emit_tree(note, ctx, profile)
        assert len([c for c in cells if c.role == "music_lyric"]) == 2

    def test_inline_without_translator_falls_back_to_marker(
        self, profile, ctx, monkeypatch,
    ):
        monkeypatch.setitem(
            profile.features.setdefault("music", {}), "lyrics_form", "inline",
        )
        # ctx fixture wires no inline_text_translator.
        note = _note_with_lyrics('<lyric><text>la</text></lyric>')
        cells = emit_tree(note, ctx, profile)
        assert "music_lyric_marker" in _roles(cells)
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_UNSUPPORTED_NOTATION" in codes


# ---------------------------------------------------------------------------
# Rest does NOT emit lyric markers
# ---------------------------------------------------------------------------


class TestRestHasNoLyric:
    def test_rest_with_lyric_silently_ignores(self, profile, ctx):
        # MusicXML technically allows <lyric> on a <rest>-bearing
        # <note>, but BANA doesn't render lyrics on rests. Our
        # _emit_rest path doesn't call _emit_lyrics_marker, so even
        # if such a node arrives the lyric is silently dropped.
        note = ET.fromstring(
            "<note><rest/>"
            "<duration>1</duration><type>quarter</type>"
            "<lyric><text>oops</text></lyric>"
            "</note>"
        )
        cells = emit_tree(note, ctx, profile)
        assert "music_lyric_marker" not in _roles(cells)


# ---------------------------------------------------------------------------
# Combined: lyric + tie + fingering on the same note
# ---------------------------------------------------------------------------


class TestCombined:
    def test_full_order_octave_note_dot_tie_finger_lyric(self, profile, ctx):
        note = ET.fromstring(
            "<note>"
            "<pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>3</duration><type>quarter</type><dot/>"
            "<notations>"
            '<tied type="start"/>'
            "<technical><fingering>1</fingering></technical>"
            "</notations>"
            '<lyric><text>la</text></lyric>'
            "</note>"
        )
        roles = _roles(emit_tree(note, ctx, profile))
        # [octave, note, dot, tie(2), fingering, lyric]
        assert roles == [
            "music_octave", "music_note", "music_dot",
            "music_tie", "music_tie",
            "music_fingering",
            "music_lyric_marker",
        ]


# ---------------------------------------------------------------------------
# Pipeline integration
# ---------------------------------------------------------------------------


SONG_FRAGMENT_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<attributes>"
    "<divisions>2</divisions>"
    "<key><fifths>0</fifths></key>"
    "<time><beats>4</beats><beat-type>4</beat-type></time>"
    "<clef><sign>G</sign><line>2</line></clef>"
    "</attributes>"
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>quarter</type>"
    '<lyric><syllabic>single</syllabic><text>春</text></lyric></note>'
    "<note><pitch><step>D</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>quarter</type>"
    '<lyric><syllabic>single</syllabic><text>眠</text></lyric></note>'
    "<note><pitch><step>E</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>quarter</type>"
    '<lyric><syllabic>single</syllabic><text>不</text></lyric></note>'
    "<note><pitch><step>F</step><octave>4</octave></pitch>"
    "<duration>2</duration><type>quarter</type>"
    '<lyric><syllabic>single</syllabic><text>觉</text></lyric></note>'
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestPipelineIntegration:
    def test_song_fragment(self, profile, ctx):
        from brailix import Pipeline
        from brailix.ir.document import DocumentIR, ScoreBlock

        pipe = Pipeline(profile="cn_current")
        doc = DocumentIR(
            blocks=[ScoreBlock(text=SONG_FRAGMENT_XML, source="musicxml")]
        )
        result = pipe.translate_document(doc)
        cells = result.braille_ir.blocks[0].cells
        markers = [c for c in cells if c.role == "music_lyric_marker"]
        # Four syllables → four markers
        assert len(markers) == 4
        # M8 annotates source_text with the current part / measure when
        # those containers exist. SONG_FRAGMENT_XML wraps notes in
        # ``<part id="P1"><measure number="1">`` so each lyric marker
        # carries the ``[p=P1,m=1]`` provenance suffix.
        assert [m.source_text for m in markers] == [
            "lyric[1/single]:春 [p=P1,m=1]",
            "lyric[1/single]:眠 [p=P1,m=1]",
            "lyric[1/single]:不 [p=P1,m=1]",
            "lyric[1/single]:觉 [p=P1,m=1]",
        ]
        codes = [w.code for w in result.warnings.warnings]
        assert codes == []
