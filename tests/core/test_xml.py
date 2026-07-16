"""Tests for the shared core XML helpers (:mod:`brailix.core._xml`)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.core._xml import (
    local_name,
    safe_fromstring,
    strip_namespace,
    strip_whitespace_text,
    strip_xml_invalid_chars,
    tree_depth_exceeds,
)


class TestSafeFromstring:
    """:func:`safe_fromstring` parses untrusted XML but refuses entity
    declarations (the billion-laughs / quadratic-blowup DoS vector)."""

    def test_parses_plain_xml(self) -> None:
        assert safe_fromstring("<a><b>x</b></a>").tag == "a"

    def test_accepts_bytes(self) -> None:
        assert safe_fromstring(b"<r><c/></r>").tag == "r"

    def test_allows_predefined_entities(self) -> None:
        # lt/gt/amp/apos/quot are always available and never declared.
        assert safe_fromstring("<a>x &amp; y</a>").text == "x & y"

    def test_allows_external_doctype(self) -> None:
        # Real MusicXML files carry an external DTD reference (no internal
        # entities); it must still parse.
        doc = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE score-partwise PUBLIC '
            '"-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">'
            "<score-partwise><part/></score-partwise>"
        )
        assert safe_fromstring(doc).tag == "score-partwise"

    _BOMB = (
        '<?xml version="1.0"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]>'
        "<lolz>&lol2;</lolz>"
    )

    def test_rejects_internal_entity_declaration(self) -> None:
        with pytest.raises(ET.ParseError, match="entity declarations"):
            safe_fromstring(self._BOMB)

    def test_rejects_entity_declaration_in_bytes(self) -> None:
        with pytest.raises(ET.ParseError, match="entity declarations"):
            safe_fromstring(self._BOMB.encode("utf-8"))

    # A UTF-16 (LE / BE) document spreads the ASCII bytes of ``<!ENTITY`` with
    # interleaved NULs, so a plain ASCII byte scan misses the declaration — yet
    # expat auto-detects UTF-16 from the BOM / declaration and would expand the
    # entity. The declaration must be caught in every encoding expat decodes,
    # not just UTF-8, or the whole billion-laughs guard is bypassable.
    _BOMB_UTF16 = (
        '<?xml version="1.0" encoding="UTF-16"?>'
        '<!DOCTYPE lolz [<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;">]>'
        "<lolz>&lol2;</lolz>"
    )

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
    def test_rejects_utf16_entity_declaration(self, encoding: str) -> None:
        with pytest.raises(ET.ParseError, match="entity declarations"):
            safe_fromstring(self._BOMB_UTF16.encode(encoding))

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
    def test_legit_utf16_without_entities_still_parses(self, encoding: str) -> None:
        # The UTF-16 guard must not false-reject a legitimate UTF-16 document
        # (Finale / some Windows exporters write UTF-16-with-BOM MusicXML).
        doc = '<?xml version="1.0" encoding="UTF-16"?><score><part/></score>'
        root = safe_fromstring(doc.encode(encoding))
        assert root.tag == "score"

    def test_legit_utf16_external_doctype_still_parses(self) -> None:
        # A real MusicXML file carries an external DTD (no internal entities);
        # in UTF-16 it must still parse, not trip the entity guard.
        doc = (
            '<?xml version="1.0" encoding="UTF-16"?>'
            '<!DOCTYPE score-partwise PUBLIC '
            '"-//Recordare//DTD MusicXML 3.1 Partwise//EN" '
            '"http://www.musicxml.org/dtds/partwise.dtd">'
            "<score-partwise><part/></score-partwise>"
        )
        assert safe_fromstring(doc.encode("utf-16")).tag == "score-partwise"


class TestStripXmlInvalidChars:
    def test_drops_c0_controls_except_whitespace(self) -> None:
        # Form-feed, NUL, vertical-tab, bell, escape are illegal in XML 1.0.
        assert strip_xml_invalid_chars("a\x0cb\x00c\x0bd\x07e\x1bf") == "abcdef"

    def test_keeps_tab_newline_carriage_return(self) -> None:
        # The three whitespace controls are valid XML 1.0 chars.
        assert strip_xml_invalid_chars("a\tb\nc\rd") == "a\tb\nc\rd"

    def test_keeps_ordinary_text(self) -> None:
        assert strip_xml_invalid_chars("我在重庆 x^2 ⠿") == "我在重庆 x^2 ⠿"

    def test_result_is_xml_parseable_after_escaping(self) -> None:
        # The whole point: a sanitized + escaped string embeds cleanly.
        from xml.sax.saxutils import escape

        dirty = "before\x0c<after> & more\x00"
        doc = f"<r>{escape(strip_xml_invalid_chars(dirty))}</r>"
        root = ET.fromstring(doc)  # must not raise
        assert root.text == "before<after> & more"


class TestStripNamespace:
    def test_strips_clark_prefix_recursively(self) -> None:
        root = ET.fromstring('<m:math xmlns:m="urn:x"><m:mi>x</m:mi></m:math>')
        strip_namespace(root)
        assert root.tag == "math"
        assert [c.tag for c in root] == ["mi"]

    def test_leaves_bare_tags_untouched(self) -> None:
        root = ET.fromstring("<math><mi>x</mi></math>")
        strip_namespace(root)
        assert root.tag == "math"
        assert root[0].tag == "mi"

    def test_deeply_nested_does_not_overflow(self) -> None:
        # Iterative, not recursive: a tree far deeper than Python's recursion
        # limit must strip without RecursionError (an untrusted MathML / .blx
        # payload reaches here via the IR-deserialization boundary).
        depth = 5000
        root = ET.Element("{urn:x}math")
        cur = root
        for _ in range(depth):
            cur = ET.SubElement(cur, "{urn:x}mrow")
        strip_namespace(root)  # must not raise
        assert root.tag == "math"
        node, seen = root, 0
        while len(node):
            node = node[0]
            assert node.tag == "mrow"
            seen += 1
        assert seen == depth


class TestStripWhitespaceText:
    def test_nulls_pure_whitespace_text_and_tail(self) -> None:
        root = ET.fromstring("<r>\n  <a>x</a>\n  <b>y</b>\n</r>")
        strip_whitespace_text(root)
        assert root.text is None  # was "\n  "
        assert root[0].tail is None  # was "\n  "
        assert root[0].text == "x"  # real text preserved

    def test_keeps_meaningful_text(self) -> None:
        root = ET.fromstring("<r> keep <a>x</a></r>")
        strip_whitespace_text(root)
        assert root.text == " keep "  # not pure whitespace → kept

    def test_deeply_nested_does_not_overflow(self) -> None:
        depth = 5000
        root = ET.Element("r")
        cur = root
        for _ in range(depth):
            cur = ET.SubElement(cur, "a")
            cur.text = "   "  # pure whitespace at every level
        strip_whitespace_text(root)  # must not raise
        node = root
        while len(node):
            node = node[0]
        assert node.text is None  # deepest whitespace text nulled


class TestTreeDepthExceeds:
    @staticmethod
    def _chain(depth: int) -> ET.Element:
        # A linear tree whose nesting depth is exactly `depth` (root = 1).
        root = ET.Element("math")
        cur = root
        for _ in range(depth - 1):
            cur = ET.SubElement(cur, "mrow")
        return root

    def test_shallow_is_within_limit(self) -> None:
        assert tree_depth_exceeds(self._chain(10), 150) is False

    def test_exactly_at_limit_is_not_exceeded(self) -> None:
        assert tree_depth_exceeds(self._chain(150), 150) is False

    def test_one_past_limit_is_exceeded(self) -> None:
        assert tree_depth_exceeds(self._chain(151), 150) is True

    def test_single_element_is_depth_one(self) -> None:
        assert tree_depth_exceeds(ET.Element("math"), 1) is False

    def test_probe_is_itself_depth_safe(self) -> None:
        # A 5000-deep tree against a small limit short-circuits to True
        # without the probe itself recursing / overflowing.
        assert tree_depth_exceeds(self._chain(5000), 150) is True

    def test_width_is_not_depth(self) -> None:
        # A wide-but-shallow tree (root + many children) is depth 2.
        root = ET.Element("math")
        for _ in range(1000):
            ET.SubElement(root, "mn")
        assert tree_depth_exceeds(root, 2) is False
        assert tree_depth_exceeds(root, 1) is True


class TestLocalName:
    def test_strips_clark_prefix(self) -> None:
        assert local_name("{urn:x}math") == "math"

    def test_bare_tag_unchanged(self) -> None:
        assert local_name("math") == "math"
