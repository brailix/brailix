"""One byte-decoding rule, three normalized intermediate formats.

MathML, MusicXML and SVG are the standardized mediating formats for the math,
music and graphics subsystems, and each has a pass-through adapter whose
protocol (:mod:`brailix.core.protocols`) accepts ``str | bytes``. *Bytes of
XML are self-describing*: a byte order mark, the byte pattern of the ``<?xml``
declaration, or the ``encoding`` that declaration names says what they are.

All three used to answer ``data.decode("utf-8")`` instead, so a legal UTF-16
document — which Finale and several Windows exporters write, and which the
input layer's own file reader has always accepted — came back as a soft-failure
node: a missing score, a blank graphic, an error formula, with no exception
anywhere to say why. The rule now lives once, in
:func:`brailix.core._xml.decode_xml_bytes`, and is unit-tested there.

What this file pins is that each vertical *uses* it, and that is deliberately a
shared **contract test** rather than a shared helper — the same split
``test_soft_failure_policy.py`` makes and for the same reason: the three
subsystems must stay independently replaceable (ARCHITECTURE#arch-mediators),
so a test may span them where production code may not. What each adapter
*recovers to* when the bytes really are undecodable stays its own business, and
these only check that it recovers rather than raising.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

import pytest

from brailix.core._xml import local_name
from brailix.frontend.graphics.adapters.svg import SVGSourceAdapter
from brailix.frontend.math.adapters.mathml import MathMLSourceAdapter
from brailix.frontend.music.adapters.musicxml import MusicXMLSourceAdapter


@dataclass(frozen=True)
class _Vertical:
    """One subsystem's pass-through adapter, plus what its output looks like."""

    name: str
    convert: Callable[[str | bytes], str]
    document: str
    root: str
    #: A substring present only in this vertical's soft-failure document.
    failure_marker: str

    def __str__(self) -> str:  # readable parametrize ids
        return self.name


VERTICALS = [
    _Vertical(
        name="mathml",
        convert=lambda src: MathMLSourceAdapter().to_mathml(src),
        document="<math><mtext>café</mtext></math>",
        root="math",
        failure_marker="merror",
    ),
    _Vertical(
        name="musicxml",
        convert=lambda src: MusicXMLSourceAdapter().to_musicxml(src),
        document=(
            "<score-partwise><movement-title>café</movement-title>"
            "</score-partwise>"
        ),
        root="score-partwise",
        failure_marker="music-error",
    ),
    _Vertical(
        name="svg",
        convert=lambda src: SVGSourceAdapter().to_svg(src),
        document='<svg viewBox="0 0 10 10"><desc>café</desc></svg>',
        root="svg",
        failure_marker="data-bk-error",
    ),
]


def _assert_read(vertical: _Vertical, out: str) -> None:
    """The adapter read the document rather than soft-failing on it."""
    assert vertical.failure_marker not in out, out[:200]
    assert local_name(ET.fromstring(out).tag) == vertical.root
    assert "café" in out


@pytest.mark.parametrize("vertical", VERTICALS, ids=str)
class TestBytesFollowXmlEncodingRules:
    def test_utf8_without_a_mark(self, vertical: _Vertical) -> None:
        _assert_read(vertical, vertical.convert(vertical.document.encode("utf-8")))

    def test_utf8_with_a_mark(self, vertical: _Vertical) -> None:
        # The mark is metadata; leaving it in the text puts a stray U+FEFF
        # before the root element.
        raw = vertical.document.encode("utf-8-sig")
        out = vertical.convert(raw)
        _assert_read(vertical, out)
        assert not out.startswith("﻿")

    @pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
    def test_utf16_with_a_mark(self, vertical: _Vertical, encoding: str) -> None:
        raw = "﻿".encode(encoding) + vertical.document.encode(encoding)
        _assert_read(vertical, vertical.convert(raw))

    @pytest.mark.parametrize("encoding", ["utf-16-le", "utf-16-be"])
    def test_utf16_without_a_mark_but_with_a_declaration(
        self, vertical: _Vertical, encoding: str
    ) -> None:
        # XML Appendix F: with no mark, the encoded bytes of ``<?xm`` are what
        # identify the family.
        doc = f'<?xml version="1.0" encoding="UTF-16"?>{vertical.document}'
        _assert_read(vertical, vertical.convert(doc.encode(encoding)))

    def test_a_declared_single_byte_encoding(self, vertical: _Vertical) -> None:
        doc = f'<?xml version="1.0" encoding="ISO-8859-1"?>{vertical.document}'
        _assert_read(vertical, vertical.convert(doc.encode("iso-8859-1")))

    def test_str_input_is_unaffected(self, vertical: _Vertical) -> None:
        _assert_read(vertical, vertical.convert(vertical.document))


@pytest.mark.parametrize("vertical", VERTICALS, ids=str)
class TestUndecodableBytesStillDegrade:
    """Bytes that decode under no rule are still a soft failure, not a raise —
    the adapters' existing contract, kept while the rule around it widened."""

    def test_reports_the_decode_failure(self, vertical: _Vertical) -> None:
        out = vertical.convert(b"<a>\xff\xfd\xfe</a>")
        assert vertical.failure_marker in out
        assert "undecodable bytes" in out
        ET.fromstring(out)  # the soft-failure document is itself well-formed

    def test_a_declared_encoding_python_lacks_degrades_too(
        self, vertical: _Vertical
    ) -> None:
        raw = b'<?xml version="1.0" encoding="x-made-up"?><a/>'
        out = vertical.convert(raw)
        assert vertical.failure_marker in out
        assert "undecodable bytes" in out
