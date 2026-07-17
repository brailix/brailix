"""Property-based tests for the shared core XML helpers.

The :mod:`brailix.core._xml` helpers run at every untrusted-XML boundary
(MathML / MusicXML payloads, ``.blx`` round-trips, docx converters), so
their contracts are pinned over generated trees and strings rather than a
handful of shapes:

* ``strip_namespace`` rewrites every tag to its ``local_name`` — nothing
  else changes (structure, text, attributes), and stripping twice equals
  stripping once;
* ``strip_whitespace_text`` nulls exactly the whitespace-only text / tail
  strings, preserves meaningful ones, and is idempotent;
* ``strip_xml_invalid_chars`` removes exactly the XML-1.0-illegal code
  points — it only ever deletes, is idempotent, and is the identity on
  clean text;
* ``tree_depth_exceeds`` agrees with an independently computed depth for
  any tree and any limit.

Security regressions (entity-bomb rejection incl. the verified UTF-16
bypass) and the depth-safety pins (5000-deep trees) stay example-tested in
``test_xml.py`` — those are specific attack shapes, not value spaces.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core._xml import (
    local_name,
    strip_namespace,
    strip_whitespace_text,
    strip_xml_invalid_chars,
    tree_depth_exceeds,
)

_TAGS = ["math", "mi", "part", "{urn:x}math", "{urn:x}mi", "{urn:y}note"]
_TEXTS = [None, "x", "标", " keep ", "  ", "\n\t ", ""]


@st.composite
def _trees(draw: st.DrawFn) -> ET.Element:
    def build(depth: int) -> ET.Element:
        elem = ET.Element(draw(st.sampled_from(_TAGS)))
        elem.text = draw(st.sampled_from(_TEXTS))
        if depth < 3:
            for _ in range(draw(st.integers(0, 2))):
                child = build(depth + 1)
                child.tail = draw(st.sampled_from(_TEXTS))
                elem.append(child)
        return elem

    return build(0)


def _model_depth(elem: ET.Element) -> int:
    return 1 + max((_model_depth(child) for child in elem), default=0)


class TestStripNamespace:
    @settings(max_examples=100)
    @given(tree=_trees())
    def test_rewrites_every_tag_to_local_name_and_nothing_else(
        self, tree: ET.Element
    ) -> None:
        before = [
            (local_name(el.tag), el.text, el.tail, dict(el.attrib), len(el))
            for el in tree.iter()
        ]
        strip_namespace(tree)
        after = [
            (el.tag, el.text, el.tail, dict(el.attrib), len(el))
            for el in tree.iter()
        ]
        assert after == before
        assert not any(el.tag.startswith("{") for el in tree.iter())

    @settings(max_examples=60)
    @given(tree=_trees())
    def test_idempotent(self, tree: ET.Element) -> None:
        strip_namespace(tree)
        once = [el.tag for el in tree.iter()]
        strip_namespace(tree)
        assert [el.tag for el in tree.iter()] == once


class TestStripWhitespaceText:
    @settings(max_examples=100)
    @given(tree=_trees())
    def test_nulls_exactly_the_whitespace_only_strings(self, tree: ET.Element) -> None:
        expected = [
            (
                el.text if el.text is not None and el.text.strip() else None,
                el.tail if el.tail is not None and el.tail.strip() else None,
            )
            for el in tree.iter()
        ]
        strip_whitespace_text(tree)
        assert [(el.text, el.tail) for el in tree.iter()] == expected

    @settings(max_examples=60)
    @given(tree=_trees())
    def test_idempotent(self, tree: ET.Element) -> None:
        strip_whitespace_text(tree)
        once = [(el.text, el.tail) for el in tree.iter()]
        strip_whitespace_text(tree)
        assert [(el.text, el.tail) for el in tree.iter()] == once


def _is_xml_invalid(ch: str) -> bool:
    # XML 1.0: C0 controls except tab / LF / CR, plus the lone surrogates.
    cp = ord(ch)
    return (cp <= 0x08 or cp in (0x0B, 0x0C) or 0x0E <= cp <= 0x1F
            or 0xD800 <= cp <= 0xDFFF)


# Strings assembled from raw code points so lone surrogates and C0
# controls actually appear (st.text() excludes surrogates by default).
_raw_strings = st.lists(
    st.one_of(
        st.integers(0, 0x20),
        st.integers(0xD7FF, 0xE001),
        st.sampled_from([ord("a"), ord("我"), ord("<"), ord("&"), 0x0C, 0x00]),
    ),
    max_size=12,
).map(lambda cps: "".join(chr(cp) for cp in cps))


class TestStripXmlInvalidChars:
    @settings(max_examples=150)
    @given(text=_raw_strings)
    def test_removes_exactly_the_illegal_code_points(self, text: str) -> None:
        cleaned = strip_xml_invalid_chars(text)
        # Deletion-only, targeting exactly the illegal set — equivalent to
        # filtering by the XML 1.0 rule, which also gives idempotence and
        # identity-on-clean-input for free.
        assert cleaned == "".join(ch for ch in text if not _is_xml_invalid(ch))
        assert strip_xml_invalid_chars(cleaned) == cleaned


class TestTreeDepthExceeds:
    @settings(max_examples=100)
    @given(tree=_trees(), limit=st.integers(0, 6))
    def test_agrees_with_independent_depth_model(
        self, tree: ET.Element, limit: int
    ) -> None:
        assert tree_depth_exceeds(tree, limit) == (_model_depth(tree) > limit)
