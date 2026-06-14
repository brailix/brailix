"""Tests for the shared frontend XML helpers."""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.frontend._xml import (
    local_name,
    strip_namespace,
    strip_whitespace_text,
    strip_xml_invalid_chars,
)


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
