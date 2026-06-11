"""End-to-end frontend.music tests.

Drives :func:`brailix.frontend.music.parse_music_tree` over every
adapter (musicxml / mxl / plain) and verifies the soft-failure
contract: malformed input never raises, it lands in a
``<music-error>`` and the warning collector is populated.
"""

from __future__ import annotations

import io
import xml.etree.ElementTree as ET
import zipfile

from brailix.core.context import MusicContext
from brailix.frontend.music import parse_music_tree

# ---------------------------------------------------------------------------
# musicxml adapter (pass-through)
# ---------------------------------------------------------------------------


SIMPLE_SCORE = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Piano</part-name></score-part>'
    "</part-list>"
    '<part id="P1">'
    '<measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure>"
    "</part>"
    "</score-partwise>"
)


class TestMusicXMLAdapter:
    def test_pass_through(self):
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(SIMPLE_SCORE, ctx)
        assert isinstance(tree, ET.Element)
        assert tree.tag == "score-partwise"
        assert tree.attrib.get("version") == "4.0"
        # Note structure survives.
        note = tree.find(".//note")
        assert note is not None
        assert note.find("pitch/step").text == "C"

    def test_namespace_stripped(self):
        ns_xml = (
            '<score-partwise xmlns="http://www.musicxml.org/dtds/3.0/musicxml.dtd">'
            '<part id="P1"/>'
            "</score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(ns_xml, ctx)
        # Local name preserved without {ns} prefix.
        assert tree.tag == "score-partwise"
        assert tree[0].tag == "part"

    def test_xml_declaration_stripped(self):
        xml = '<?xml version="1.0" encoding="UTF-8"?>' + SIMPLE_SCORE
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert tree.tag == "score-partwise"

    def test_doctype_stripped(self):
        # Older Sibelius/Finale exports prepend an external DOCTYPE.
        xml = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 4.0 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">'
            + SIMPLE_SCORE
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert tree.tag == "score-partwise"

    def test_empty_input_soft_fails(self):
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree("", ctx)
        assert tree.tag == "score-partwise"
        assert tree.find("music-error") is not None

    def test_malformed_xml_soft_fails(self):
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree("<not-closed>", ctx)
        assert tree.tag == "score-partwise"
        err = tree.find("music-error")
        assert err is not None
        assert "parse error" in err.attrib.get("data-reason", "")

    def test_malformed_with_control_char_soft_fails(self):
        # A vendor-malformed source carrying an XML-1.0-illegal control
        # char (here form-feed) must still soft-fail to a well-formed
        # <music-error> — the surface is echoed into the wrapper, so an
        # un-stripped control char would make the re-parse raise.
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree("<score-partwise>\x0c<unclosed", ctx)
        assert tree.tag == "score-partwise"
        assert tree.find("music-error") is not None

    def test_malformed_with_nul_byte_soft_fails(self):
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree("<part>\x00 bad", ctx)
        assert tree.tag == "score-partwise"
        assert tree.find("music-error") is not None

    def test_bytes_input_utf8(self):
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(SIMPLE_SCORE.encode("utf-8"), ctx)
        assert tree.tag == "score-partwise"


# ---------------------------------------------------------------------------
# mxl adapter (ZIP)
# ---------------------------------------------------------------------------


def _make_mxl_bytes(score_xml: str, *, with_container: bool = True) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        if with_container:
            container = (
                '<?xml version="1.0" encoding="UTF-8" standalone="no"?>'
                '<container><rootfiles>'
                '<rootfile full-path="score.musicxml" '
                'media-type="application/vnd.recordare.musicxml+xml"/>'
                '</rootfiles></container>'
            )
            zf.writestr("META-INF/container.xml", container)
        zf.writestr("score.musicxml", score_xml)
    return buf.getvalue()


class TestMxlAdapter:
    def test_unzip_and_parse(self):
        mxl_bytes = _make_mxl_bytes(SIMPLE_SCORE)
        ctx = MusicContext(source="mxl")
        tree = parse_music_tree(mxl_bytes, ctx)
        assert tree.tag == "score-partwise"
        assert tree.find(".//note/pitch/step").text == "C"

    def test_missing_container_falls_back_to_xml_entry(self):
        mxl_bytes = _make_mxl_bytes(SIMPLE_SCORE, with_container=False)
        ctx = MusicContext(source="mxl")
        tree = parse_music_tree(mxl_bytes, ctx)
        assert tree.tag == "score-partwise"
        # Fallback still finds score.musicxml inside the archive.
        assert tree.find(".//note/pitch/step").text == "C"

    def test_invalid_zip_soft_fails(self):
        ctx = MusicContext(source="mxl")
        tree = parse_music_tree(b"not a zip file", ctx)
        assert tree.tag == "score-partwise"
        assert tree.find("music-error") is not None

    def test_empty_bytes_soft_fails(self):
        ctx = MusicContext(source="mxl")
        tree = parse_music_tree(b"", ctx)
        assert tree.tag == "score-partwise"
        assert tree.find("music-error") is not None

    def test_string_input_routed_to_musicxml(self):
        # An .mxl source name with a string payload is a caller error,
        # but should round-trip via the MusicXML adapter (not crash).
        ctx = MusicContext(source="mxl")
        tree = parse_music_tree(SIMPLE_SCORE, ctx)
        assert tree.tag == "score-partwise"
        assert tree.find(".//note") is not None


# ---------------------------------------------------------------------------
# plain adapter (last-resort fallback)
# ---------------------------------------------------------------------------


class TestPlainAdapter:
    def test_emits_music_error(self):
        ctx = MusicContext(source="plain")
        tree = parse_music_tree("C D E", ctx)
        assert tree.tag == "score-partwise"
        err = tree.find("music-error")
        assert err is not None
        assert "plain music source unsupported" in err.attrib.get("data-reason", "")

    def test_bytes_input(self):
        ctx = MusicContext(source="plain")
        tree = parse_music_tree(b"do re mi", ctx)
        assert tree.tag == "score-partwise"
        assert tree.find("music-error") is not None


# ---------------------------------------------------------------------------
# Adapter missing
# ---------------------------------------------------------------------------


class TestUnknownSource:
    def test_unknown_source_returns_none_and_warns(self):
        ctx = MusicContext(source="midi")  # not yet registered
        tree = parse_music_tree(b"\x00", ctx)
        assert tree is None
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_ADAPTER_MISSING" in codes


# ---------------------------------------------------------------------------
# Normalizer-level details
# ---------------------------------------------------------------------------


class TestNormalizer:
    def test_whitespace_text_stripped(self):
        xml = (
            "<score-partwise>\n  <part id='P1'>\n    "
            "<measure number='1'/>\n  </part>\n</score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        # No pure-whitespace text on the root.
        assert tree.text is None
        # Part element similarly has no whitespace text inside.
        assert tree[0].text is None

    def test_attributes_preserved(self):
        xml = '<score-partwise version="4.0" xml:lang="en"><part id="P1"/></score-partwise>'
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        # The xml:lang attribute has a namespace; ElementTree leaves it
        # as ``{http://www.w3.org/XML/1998/namespace}lang`` — we don't
        # rewrite attributes (only tags), so it survives intact.
        assert tree.attrib.get("version") == "4.0"
        assert tree[0].attrib.get("id") == "P1"

    def test_voice_numbers_densified(self):
        # Finale emits per-staff voice blocks (1/5/9/13); normalize
        # remaps each part to a dense 1..N so in-accord grouping works.
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<note><voice>1</voice></note>"
            "<note><voice>5</voice></note>"
            "<note><voice>9</voice></note>"
            "</measure></part></score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert [v.text for v in tree.iter("voice")] == ["1", "2", "3"]

    def test_voice_numbers_already_dense_unchanged(self):
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<note><voice>1</voice></note>"
            "<note><voice>2</voice></note>"
            "</measure></part></score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert [v.text for v in tree.iter("voice")] == ["1", "2"]

    def test_voice_numbers_remapped_per_part(self):
        # Each part remaps independently; a single odd voice → "1".
        xml = (
            "<score-partwise>"
            "<part id='P1'><measure number='1'>"
            "<note><voice>3</voice></note></measure></part>"
            "<part id='P2'><measure number='1'>"
            "<note><voice>7</voice></note></measure></part>"
            "</score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert [v.text for v in tree.find("part[@id='P1']").iter("voice")] == ["1"]
        assert [v.text for v in tree.find("part[@id='P2']").iter("voice")] == ["1"]

    def test_missing_type_inferred_from_divisions(self):
        # divisions=2 → quarter lasts 2 units; a typeless note of
        # duration 4 is a half note.
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<attributes><divisions>2</divisions></attributes>"
            "<note><pitch><step>C</step><octave>4</octave></pitch>"
            "<duration>4</duration></note>"
            "</measure></part></score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert tree.find(".//note/type").text == "half"

    def test_existing_type_not_overwritten(self):
        # duration says whole, but an explicit <type> wins untouched.
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<attributes><divisions>1</divisions></attributes>"
            "<note><duration>4</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert [t.text for t in tree.iter("type")] == ["quarter"]


# ---------------------------------------------------------------------------
# Soft-failure backstops — nothing on this path may raise
# ---------------------------------------------------------------------------


def _has_music_error(tree) -> bool:
    return tree.tag == "music-error" or any(
        e.tag == "music-error" for e in tree.iter()
    )


class TestNonDecimalVoiceNumbers:
    def test_circled_digit_voice_sorts_instead_of_raising(self):
        # "①".isdigit() is True but int("①") raises — the voice remap
        # used to blow a ValueError out of the normalizer's
        # never-raises contract.  It now sorts into the non-numeric
        # bucket and the remap proceeds.
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<note><voice>①</voice></note></measure></part>"
            "</score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert tree is not None
        assert [v.text for v in tree.iter("voice")] == ["1"]


class TestRaisingAdapterBackstop:
    def test_raising_adapter_degrades_to_music_error(self):
        # The registry is open to third-party adapters; one that raises
        # must degrade to the standard <music-error> tree, never crash
        # the pipeline.
        from brailix.frontend.music.registry import music_source_registry

        class _Boom:
            source = "boom-raise-test"

            def to_musicxml(self, src, ctx=None):
                raise RuntimeError("boom")

        music_source_registry.register("boom-raise-test", _Boom)
        ctx = MusicContext(source="boom-raise-test")
        tree = parse_music_tree(b"\x00", ctx)
        assert tree is not None
        assert _has_music_error(tree)


class TestMxlUnreadableArchives:
    def test_zip_runtime_error_degrades_to_music_error(self, monkeypatch):
        # zipfile raises more than BadZipFile: RuntimeError for an
        # encrypted entry, zlib.error for a corrupt deflate stream,
        # NotImplementedError for an unsupported compression method.
        # The mxl adapter's narrow except used to let all of them
        # escape and crash the pipeline.  (``writestr`` normalises the
        # flag bits, so a genuinely encrypted archive can't be built
        # with stdlib alone — inject the error at the read seam the
        # way zipfile itself would raise it.)
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            zf.writestr("score.xml", b"<score-partwise/>")

        def _boom(self, name, pwd=None):
            raise RuntimeError(f"File {name!r} is encrypted")

        monkeypatch.setattr(zipfile.ZipFile, "read", _boom)
        ctx = MusicContext(source="mxl")
        tree = parse_music_tree(buf.getvalue(), ctx)
        assert tree is not None
        assert _has_music_error(tree)

    def test_ambiguous_duration_warns_and_skips(self):
        # divisions=2, duration=3 → dotted quarter: not a plain type.
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<attributes><divisions>2</divisions></attributes>"
            "<note><duration>3</duration></note>"
            "</measure></part></score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert tree.find(".//note/type") is None  # left for the backend
        assert "MUSIC_DURATION_AMBIGUOUS" in [
            w.code for w in ctx.warnings.warnings
        ]

    def test_grace_note_without_duration_skipped(self):
        # Grace notes carry no <duration> — skip silently, no warning.
        xml = (
            "<score-partwise><part id='P1'><measure number='1'>"
            "<attributes><divisions>1</divisions></attributes>"
            "<note><grace/><pitch><step>C</step><octave>4</octave></pitch>"
            "</note></measure></part></score-partwise>"
        )
        ctx = MusicContext(source="musicxml")
        tree = parse_music_tree(xml, ctx)
        assert tree.find(".//note/type") is None
        assert "MUSIC_DURATION_AMBIGUOUS" not in [
            w.code for w in ctx.warnings.warnings
        ]
