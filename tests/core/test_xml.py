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


class TestLocalName:
    def test_strips_clark_prefix(self) -> None:
        assert local_name("{urn:x}math") == "math"

    def test_bare_tag_unchanged(self) -> None:
        assert local_name("math") == "math"
