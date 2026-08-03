"""Tests for the shared core XML helpers (:mod:`brailix.core._xml`)."""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.core._xml import (
    XmlDecodeError,
    decode_xml_bytes,
    local_name,
    safe_fromstring,
    strip_namespace,
    strip_whitespace_text,
    strip_xml_invalid_chars,
    strip_xml_prolog,
    tree_depth_exceeds,
    xml_root_element,
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


class TestDecodeXmlBytes:
    """Bytes of XML say what encoding they are in — a BOM, the byte pattern of
    the ``<?xml`` declaration, or the ``encoding`` that declaration names.
    Three pass-through adapters used to answer ``utf-8`` regardless, so a legal
    UTF-16 score / formula / graphic soft-failed on the way in while the input
    layer's own file reader accepted the very same bytes.
    """

    DOC = '<?xml version="1.0"?><score-partwise><part/></score-partwise>'

    def test_plain_utf8(self) -> None:
        assert decode_xml_bytes(self.DOC.encode("utf-8")) == self.DOC

    def test_utf8_bom_is_consumed(self) -> None:
        # A mark left in the text puts a stray U+FEFF before the root element,
        # which is what the old ``decode("utf-8")`` did.
        assert decode_xml_bytes(self.DOC.encode("utf-8-sig")) == self.DOC

    @staticmethod
    def _with_bom(text: str, encoding: str) -> bytes:
        # The ``utf-16`` / ``utf-32`` codecs write a mark themselves; the
        # explicit-endian ones do not, so give them the one a real exporter
        # would.
        prefix = b"" if encoding in ("utf-16", "utf-32") else "﻿".encode(encoding)
        return prefix + text.encode(encoding)

    @pytest.mark.parametrize("encoding", ["utf-16", "utf-16-le", "utf-16-be"])
    def test_utf16_with_a_byte_order_mark(self, encoding: str) -> None:
        assert decode_xml_bytes(self._with_bom(self.DOC, encoding)) == self.DOC

    @pytest.mark.parametrize("encoding", ["utf-32", "utf-32-le", "utf-32-be"])
    def test_utf32_marks_are_tested_before_utf16(self, encoding: str) -> None:
        # The UTF-32LE mark BEGINS with the UTF-16LE mark, so a check ordered
        # the other way decodes a UTF-32 document into interleaved garbage.
        assert decode_xml_bytes(self._with_bom(self.DOC, encoding)) == self.DOC

    @pytest.mark.parametrize(
        "encoding", ["utf-16-le", "utf-16-be", "utf-32-le", "utf-32-be"]
    )
    def test_no_mark_but_the_declaration_bytes_give_it_away(
        self, encoding: str
    ) -> None:
        # XML Appendix F: with no BOM, the encoded bytes of ``<?xm`` identify
        # the family. This is the only thing that can, which is why a UTF-16
        # document with neither a mark nor a declaration is not well-formed.
        assert decode_xml_bytes(self.DOC.encode(encoding)) == self.DOC

    def test_a_declared_single_byte_encoding_is_honoured(self) -> None:
        doc = '<?xml version="1.0" encoding="ISO-8859-1"?><a>café</a>'
        assert decode_xml_bytes(doc.encode("iso-8859-1")) == doc

    def test_an_encoding_attribute_in_the_body_is_not_read(self) -> None:
        # The scan is bounded to the declaration itself; running past ``?>``
        # would let a document's own content choose how it gets decoded.
        doc = '<?xml version="1.0"?><a encoding="cp500">中</a>'
        assert decode_xml_bytes(doc.encode("utf-8")) == doc

    def test_a_declared_encoding_python_lacks_is_reported(self) -> None:
        raw = b'<?xml version="1.0" encoding="x-made-up"?><a/>'
        with pytest.raises(XmlDecodeError, match="x-made-up"):
            decode_xml_bytes(raw)

    def test_bytes_that_decode_under_no_rule_are_reported(self) -> None:
        # No mark and no declaration means UTF-8, and these are not UTF-8.
        with pytest.raises(XmlDecodeError, match="UTF-8"):
            decode_xml_bytes(b"<a>\xff\xfe\xfd</a>")

    def test_the_error_is_the_only_one_a_caller_must_catch(self) -> None:
        # Both failure modes (undecodable bytes, unknown codec name) arrive as
        # one exception type, so an adapter's soft-failure branch stays single.
        assert issubclass(XmlDecodeError, ValueError)


class TestStripXmlPrologAndRootElement:
    """The prologue walk. Its job is to *locate the root element* — not to
    guess where a DOCTYPE ends by counting brackets, which is what it used to
    do and what mangled legal documents.
    """

    def test_locates_the_root_after_a_declaration(self) -> None:
        assert strip_xml_prolog('<?xml version="1.0"?><a/>') == "<a/>"

    def test_locates_the_root_after_an_external_doctype(self) -> None:
        src = (
            '<?xml version="1.0"?>'
            '<!DOCTYPE score-partwise PUBLIC "-//Recordare//DTD MusicXML 3.1 '
            'Partwise//EN" "http://www.musicxml.org/dtds/partwise.dtd">'
            "<score-partwise/>"
        )
        assert strip_xml_prolog(src) == "<score-partwise/>"

    def test_brackets_inside_dtd_quotes_do_not_end_the_subset(self) -> None:
        # The reported defect: counting ``[`` / ``]`` alone took the ``]`` out
        # of a legal attribute default for the end of the internal subset, left
        # the scan inside the DOCTYPE, then took the ``>`` beside it for the
        # end of the declaration — handing the caller ``c">]><score-partwise/>``
        # and turning a document ElementTree parses into a "parse error".
        src = (
            "<!DOCTYPE score-partwise ["
            '<!ATTLIST part id CDATA "a]b>c">'
            "]><score-partwise/>"
        )
        assert strip_xml_prolog(src) == "<score-partwise/>"
        assert ET.fromstring(src).tag == "score-partwise"  # it was always legal

    @pytest.mark.parametrize(
        "prologue",
        [
            '<!DOCTYPE h [ <!ATTLIST h a CDATA "x]y"> ]>',
            "<!DOCTYPE h [ <!ENTITY e ']]]'> ]>",
            # A ``]`` in a comment or a processing instruction closes nothing,
            # and neither does the ``>`` beside it.
            "<!DOCTYPE h [ <!-- ] > --> ]>",
            "<!DOCTYPE h [ <?tool ]> ?> ]>",
            # A conditional section brings its own nesting.
            "<!DOCTYPE h [ <![IGNORE[ <!ELEMENT x EMPTY> ]]> ]>",
            # Quoted ``>`` outside the subset has to be skipped too.
            "<!DOCTYPE h PUBLIC \"-//x//DTD y>z//EN\" 'h>.dtd' [ <!ENTITY e 'v'> ]>",
        ],
    )
    def test_the_internal_subset_is_markup_not_a_search_for_a_bracket(
        self, prologue: str
    ) -> None:
        assert strip_xml_prolog(f"{prologue}\n<h/>") == "<h/>"
        assert xml_root_element(f"{prologue}\n<h/>") == "h"

    def test_a_comment_before_the_doctype_is_walked_past(self) -> None:
        # Illustrator writes its generator comment between the declaration and
        # the DOCTYPE. The old walk was "declaration, then DOCTYPE, in that
        # order or not at all", so it stopped at the comment and left the
        # ``<!ENTITY`` in the subset for ``safe_fromstring`` to refuse — every
        # Illustrator SVG soft-failed as an "expansion bomb".
        src = (
            '<?xml version="1.0" encoding="utf-8"?>\n'
            "<!-- Generator: Adobe Illustrator 24.0.1 -->\n"
            '<!DOCTYPE svg PUBLIC "-//W3C//DTD SVG 1.1//EN" '
            '"http://www.w3.org/Graphics/SVG/1.1/DTD/svg11.dtd" '
            '[<!ENTITY ns_extend "http://ns.adobe.com/Extensibility/1.0/">]>\n'
            '<svg xmlns="http://www.w3.org/2000/svg"/>'
        )
        stripped = strip_xml_prolog(src)
        assert stripped == '<svg xmlns="http://www.w3.org/2000/svg"/>'
        assert safe_fromstring(stripped).tag.endswith("svg")

    def test_a_document_with_no_prologue_is_returned_unchanged(self) -> None:
        assert strip_xml_prolog("<math><mi>x</mi></math>") == (
            "<math><mi>x</mi></math>"
        )

    @pytest.mark.parametrize(
        "text",
        [
            "<!DOCTYPE h [",  # subset never closed
            "<!DOCTYPE h [ <!ENTITY e 'unterminated ]>",  # quote never closed
            "<!DOCTYPE h [ <!-- unterminated ]>",  # comment never closed
            "<!DOCTYPE h",  # declaration never closed
            "<!-- unterminated comment",
            "plain text, no markup",
            "",
        ],
    )
    def test_nothing_to_find_leaves_the_text_alone(self, text: str) -> None:
        # A malformed document keeps every byte it arrived with, so whoever
        # diagnoses it sees what the author actually wrote.
        assert strip_xml_prolog(text) == text
        assert xml_root_element(text) == ""

    def test_root_element_names(self) -> None:
        assert xml_root_element("<score-partwise/>") == "score-partwise"
        assert xml_root_element("  \n\t<a></a>") == "a"
        assert xml_root_element("<?xml version='1.0'?><b/>") == "b"
        assert xml_root_element("<!-- <c/> --><d/>") == "d"
        assert xml_root_element("<!DOCTYPE e SYSTEM 'x>y.dtd'><e/>") == "e"
        assert xml_root_element("<!DOCTYPE f [ <!ENTITY g '>'> ]><f/>") == "f"
        # A namespace prefix is not part of the name.
        assert xml_root_element("<mx:score-timewise/>") == "score-timewise"


class TestDoctypeIsParsedNotRefused:
    """What the prologue strip is and is not for.

    Three docstrings disagreed about this: one said expat leaves an external
    DTD alone, another said ``safe_fromstring`` trips on one, and the two
    pass-through adapters said ElementTree rejects DTD constructs outright.
    These pin the answer for the Python versions actually supported, so the
    next reader has one fact instead of three claims.
    """

    @pytest.mark.parametrize(
        "src",
        [
            "<!DOCTYPE html><html/>",
            '<!DOCTYPE a SYSTEM "a.dtd"><a/>',
            '<?xml version="1.0" standalone="no"?><a/>',
            "<!DOCTYPE a []><a/>",
            '<!DOCTYPE a [<!NOTATION gif SYSTEM "gif">]><a/>',
            "<!DOCTYPE a [<!ELEMENT a EMPTY>]><a/>",
        ],
    )
    def test_element_tree_parses_every_doctype_shape(self, src: str) -> None:
        assert ET.fromstring(src).tag in {"a", "html"}
        assert safe_fromstring(src).tag in {"a", "html"}

    def test_an_encoding_declaration_on_a_str_is_accepted_too(self) -> None:
        # ElementTree's parser takes ``str`` with a declaration in it (lxml
        # does not, which is where the folklore comes from).
        src = '<?xml version="1.0" encoding="UTF-8"?><math><mi>x</mi></math>'
        assert safe_fromstring(src).tag == "math"

    def test_what_the_strip_is_actually_for(self) -> None:
        # Not ElementTree: the ``<!ENTITY`` refusal. A DOCTYPE with an internal
        # subset is turned away wholesale by that guard even when the document
        # never references an entity, and real exporters ship one.
        src = '<!DOCTYPE a [<!ENTITY unused "x">]><a>plain</a>'
        with pytest.raises(ET.ParseError, match="entity declarations"):
            safe_fromstring(src)
        assert safe_fromstring(strip_xml_prolog(src)).text == "plain"

    def test_stripping_cannot_smuggle_an_expansion_past_the_guard(self) -> None:
        # The guarantee the guard exists for survives the strip: with the
        # declarations gone, a reference left in the body is undefined and
        # fails as an ordinary parse error. Nothing expands either way.
        src = '<!DOCTYPE a [<!ENTITY lol "lol">]><a>&lol;</a>'
        with pytest.raises(ET.ParseError, match="undefined entity"):
            safe_fromstring(strip_xml_prolog(src))


class TestStripXmlInvalidChars:
    def test_drops_c0_controls_except_whitespace(self) -> None:
        # Form-feed, NUL, vertical-tab, bell, escape are illegal in XML 1.0.
        assert strip_xml_invalid_chars("a\x0cb\x00c\x0bd\x07e\x1bf") == "abcdef"

    def test_keeps_tab_newline_carriage_return(self) -> None:
        # The three whitespace controls are valid XML 1.0 chars.
        assert strip_xml_invalid_chars("a\tb\nc\rd") == "a\tb\nc\rd"

    def test_keeps_ordinary_text(self) -> None:
        assert strip_xml_invalid_chars("我在重庆 x^2 ⠿") == "我在重庆 x^2 ⠿"

    def test_drops_the_two_bmp_noncharacters(self) -> None:
        # U+FFFE / U+FFFF are outside XML 1.0's Char production (which ends
        # the BMP at U+FFFD) and expat rejects them as an invalid token. They
        # were not being stripped, so a formula containing one made the
        # *soft-failure* document itself fail to parse and the "normalizer
        # never raises" contract break — found by the normalizer's own
        # property test, not by this one, because the model here shared the
        # omission.
        text = "a" + chr(0xFFFE) + "b" + chr(0xFFFF) + "c"
        assert strip_xml_invalid_chars(text) == "abc"

    def test_keeps_the_noncharacters_xml_actually_allows(self) -> None:
        # Discouraged but legal, and expat parses them: dropping these would
        # silently mangle text rather than protect anything.
        text = "a" + chr(0xFFFD) + "b" + chr(0xFDD0) + "c" + chr(0x1FFFE) + "d"
        assert strip_xml_invalid_chars(text) == text
        assert ET.fromstring(f"<r>{text}</r>").text == text

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


class TestStripNamespaceNonElementNodes:
    """A comment and a processing instruction are ``Element`` instances whose
    ``tag`` is the factory *function* that built them, not a string. The walk
    visits them like any other child, and ``node.tag.startswith("{")`` on one
    raised ``AttributeError: 'function' object has no attribute 'startswith'``
    — the one class of exception the soft-failure boundaries deliberately
    re-raise, so it crashed the compile rather than degrading.

    ``ET.fromstring`` drops both, so the string path never produced one; a
    **pre-parsed** ``ET.Element`` is an equally supported way to hand a tree
    to ``MathInline`` / ``MusicInline`` / ``GraphicInline``, and that path
    strips namespaces on whatever the caller built.
    """

    def test_preserves_comment_nodes(self) -> None:
        root = ET.Element("{urn:x}math")
        comment = ET.Comment("source note")
        root.append(comment)

        strip_namespace(root)

        assert root.tag == "math"
        assert root[0] is comment
        assert root[0].text == "source note"

    def test_preserves_processing_instruction_nodes(self) -> None:
        root = ET.Element("{urn:x}math")
        pi = ET.ProcessingInstruction("target", "value")
        root.append(pi)

        strip_namespace(root)

        assert root.tag == "math"
        assert root[0] is pi

    def test_still_strips_elements_below_a_comment(self) -> None:
        # The comment must be stepped over, not treated as a stop sign: its
        # siblings and its own children still get stripped.
        root = ET.Element("{urn:x}math")
        root.append(ET.Comment("note"))
        ET.SubElement(root, "{urn:x}mi").text = "x"

        strip_namespace(root)

        assert [c.tag for c in root][1] == "mi"

    def test_tree_carrying_a_comment_still_serializes(self) -> None:
        # The point of not raising is that the tree stays usable: the IR
        # round-trip serializes a stored tree with ``ET.tostring``.
        root = ET.Element("{urn:x}math")
        root.append(ET.Comment("note"))
        strip_namespace(root)
        assert ET.tostring(root, encoding="unicode") == "<math><!--note--></math>"


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

    def test_comment_body_is_not_nulled(self) -> None:
        # A comment's ``text`` is its *body*, not markup content, so the
        # whitespace rule does not apply to it — and nulling it made the tree
        # unserializable: ``ET.tostring`` writes the body with no None
        # handling, so a blank comment came out as a literal ``<!--None-->``.
        root = ET.Element("r")
        root.append(ET.Comment("   "))
        strip_whitespace_text(root)
        assert ET.tostring(root, encoding="unicode") == "<r><!--   --></r>"

    def test_tail_beside_a_comment_is_still_nulled(self) -> None:
        # The tail is ordinary inter-element whitespace, not part of the
        # comment — the guard is on ``text`` only.
        root = ET.fromstring("<r><a>x</a></r>")
        comment = ET.Comment("note")
        comment.tail = "\n  "
        root.append(comment)
        strip_whitespace_text(root)
        assert root[1].tail is None


class TestTreeDepthExceeds:
    # Boundary agreement with an independent depth model (at-limit /
    # past-limit / width-vs-depth) is property-tested over generated trees
    # in test_xml_properties.py; only the depth-SAFETY pin stays an example
    # (a 5000-deep tree is too expensive to generate per property example).

    @staticmethod
    def _chain(depth: int) -> ET.Element:
        # A linear tree whose nesting depth is exactly `depth` (root = 1).
        root = ET.Element("math")
        cur = root
        for _ in range(depth - 1):
            cur = ET.SubElement(cur, "mrow")
        return root

    def test_probe_is_itself_depth_safe(self) -> None:
        # A 5000-deep tree against a small limit short-circuits to True
        # without the probe itself recursing / overflowing.
        assert tree_depth_exceeds(self._chain(5000), 150) is True


class TestLocalName:
    def test_strips_clark_prefix(self) -> None:
        assert local_name("{urn:x}math") == "math"

    def test_bare_tag_unchanged(self) -> None:
        assert local_name("math") == "math"

    def test_unclosed_brace_degenerates_to_empty(self) -> None:
        # Garbage Clark notation (opening brace, no closing one) has no
        # local name to extract; the current, deliberate behaviour is an
        # empty string — pinned so the defensive corner can't drift
        # silently (flagged by mutation testing).
        assert local_name("{no-close") == ""


class TestStripNamespaceDegenerate:
    def test_unclosed_brace_tag_is_left_alone(self) -> None:
        # strip_namespace only rewrites a tag with a COMPLETE {ns} prefix;
        # an unclosed brace is garbage it must pass through untouched
        # rather than truncate on a guess.
        root = ET.Element("{no-close")
        strip_namespace(root)
        assert root.tag == "{no-close"
