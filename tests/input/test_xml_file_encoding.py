"""An XML file is decoded by XML's rules, not by the prose reader's.

The three pass-through adapters (MathML / MusicXML / SVG) accept *bytes of
XML* on the terms the format itself sets — a byte order mark, the byte pattern
of the ``<?xml`` declaration, or the ``encoding`` that declaration names, and
UTF-8 only when none of them speaks (``tests/frontend/test_xml_source_bytes.py``
pins that as their shared rule). The **file** entries did not: ``.xml`` and
``.musicxml`` went through the prose reader, which knows a UTF-16 BOM and
nothing else, so a score declaring ``ISO-8859-1`` — or a BOM-less UTF-16 one,
which is legal precisely because the declaration's bytes identify it — parsed
when its bytes were handed to ``MusicXMLSourceAdapter.to_musicxml`` and raised
``UnicodeDecodeError`` when the *identical bytes* sat in a file. One payload,
two answers, decided by which entry point the caller reached for.

So what these pin is not a list of encodings but the *agreement*: for the same
bytes, the file route and the adapter say the same thing. The prose formats
(``.txt`` / ``.md`` / ``.abc``) keep the BOM-only rule, and that is not an
oversight — with no declaration to read, a BOM is the only thing those bytes
can say about themselves.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import MusicXMLSourceAdapter
from brailix.input import parse_file, parse_musicxml
from brailix.ir.document import Paragraph, ScoreBlock

# A part name that survives every encoding below, so one document serves them
# all: Latin-1 has no Chinese, and the point is the *encoding*, not the script.
_MARKER = "Café"

_SCORE = (
    '<score-partwise version="4.0">'
    f'<part-list><score-part id="P1"><part-name>{_MARKER}</part-name>'
    "</score-part></part-list>"
    '<part id="P1"><measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>4</duration><type>quarter</type></note>"
    "</measure></part></score-partwise>"
)

_PROSE = f"<document><para>{_MARKER}</para></document>"


def _declared(document: str, encoding: str) -> bytes:
    """``document`` with an XML declaration naming ``encoding``, encoded in it."""
    return f'<?xml version="1.0" encoding="{encoding}"?>{document}'.encode(encoding)


@dataclass(frozen=True)
class _Spelling:
    """One legal way for an XML document's bytes to say what they are."""

    name: str
    encode: Callable[[str], bytes]

    def __str__(self) -> str:  # readable parametrize ids
        return self.name


SPELLINGS = [
    # No mark, no declaration: UTF-8 is the default the spec names last.
    _Spelling("utf-8-bare", lambda doc: doc.encode("utf-8")),
    _Spelling("utf-8-bom", lambda doc: b"\xef\xbb\xbf" + doc.encode("utf-8")),
    # A byte order mark, which the prose reader already honoured.
    _Spelling("utf-16-le-bom", lambda doc: b"\xff\xfe" + doc.encode("utf-16-le")),
    _Spelling("utf-16-be-bom", lambda doc: b"\xfe\xff" + doc.encode("utf-16-be")),
    # No mark: the declaration's own bytes identify the encoding (XML 1.0
    # Appendix F). Legal, and the prose reader cannot see it.
    _Spelling(
        "utf-16-le-declaration-bytes",
        lambda doc: _declared(doc, "utf-16-le"),
    ),
    _Spelling(
        "utf-16-be-declaration-bytes",
        lambda doc: _declared(doc, "utf-16-be"),
    ),
    # An ASCII-compatible single-byte encoding, named by the declaration —
    # what a Windows-era exporter writes.
    _Spelling("iso-8859-1-declared", lambda doc: _declared(doc, "iso-8859-1")),
    _Spelling("windows-1252-declared", lambda doc: _declared(doc, "cp1252")),
]


def _adapter_reads(raw: bytes) -> str:
    return MusicXMLSourceAdapter().to_musicxml(
        raw, MusicContext(source="musicxml", profile="cn_current")
    )


def _write(tmp_path: Path, name: str, raw: bytes) -> Path:
    path = tmp_path / name
    path.write_bytes(raw)
    return path


@pytest.mark.parametrize("spelling", SPELLINGS, ids=str)
class TestTheFileEntryAgreesWithTheAdapter:
    """The claim itself: same bytes in, same document out, either way in."""

    def test_the_adapter_reads_these_bytes(self, spelling: _Spelling) -> None:
        # The reference the file routes are held to. If this ever fails, the
        # rest of the class is measuring against a moved standard.
        assert _MARKER in _adapter_reads(spelling.encode(_SCORE))

    @pytest.mark.parametrize("suffix", [".musicxml", ".xml"])
    def test_parse_musicxml_reads_them_too(
        self, spelling: _Spelling, suffix: str, tmp_path: Path
    ) -> None:
        path = _write(tmp_path, f"score{suffix}", spelling.encode(_SCORE))
        block = parse_musicxml(
            path, language="zh-CN", profile="cn_current"
        ).blocks[0]
        assert isinstance(block, ScoreBlock)
        assert block.source == "musicxml"
        assert _MARKER in block.text

    @pytest.mark.parametrize("suffix", [".musicxml", ".xml"])
    def test_the_suffix_dispatch_reads_them_too(
        self, spelling: _Spelling, suffix: str, tmp_path: Path
    ) -> None:
        """``.xml`` sniffs the root element before routing, so it has to
        decode the file *before* it knows what it is — which is exactly where
        the undecodable-by-UTF-8 score used to die."""
        path = _write(tmp_path, f"score{suffix}", spelling.encode(_SCORE))
        block = parse_file(path, language="zh-CN", profile="cn_current").blocks[0]
        assert isinstance(block, ScoreBlock)
        assert _MARKER in block.text

    def test_a_non_score_xml_falls_back_to_plain_text_intact(
        self, spelling: _Spelling, tmp_path: Path
    ) -> None:
        """The other half of the ``.xml`` route: not a score, so plain text —
        and the characters have to survive that branch too."""
        path = _write(tmp_path, "notes.xml", spelling.encode(_PROSE))
        blocks = parse_file(path, language="zh-CN", profile="cn_current").blocks
        assert all(isinstance(b, Paragraph) for b in blocks)
        assert _MARKER in "".join(b.text for b in blocks)

    def test_the_decoded_score_is_byte_identical_across_spellings(
        self, spelling: _Spelling, tmp_path: Path
    ) -> None:
        """Every spelling of the same document decodes to the same text, up to
        its own declaration — otherwise "we accept it" would still leave the
        compiled braille depending on how the file was saved."""
        path = _write(tmp_path, "score.musicxml", spelling.encode(_SCORE))
        block = parse_musicxml(
            path, language="zh-CN", profile="cn_current"
        ).blocks[0]
        assert block.text.endswith(_SCORE)


class TestBytesThatDoNotDecode:
    """An undecodable payload is refused, and refused as one kind of thing.

    ``XmlDecodeError`` is a ``ValueError``, which is what the input entries
    document — that type is nameable from the standard library, unlike the
    private class itself.
    """

    @pytest.mark.parametrize("suffix", [".musicxml", ".xml"])
    def test_invalid_utf8_with_no_declaration(
        self, suffix: str, tmp_path: Path
    ) -> None:
        path = _write(tmp_path, f"broken{suffix}", b"<score-partwise>\xff\xfe\xfd")
        with pytest.raises(ValueError):
            parse_file(path, language="zh-CN", profile="cn_current")

    def test_a_declaration_naming_a_codec_python_does_not_have(
        self, tmp_path: Path
    ) -> None:
        raw = b'<?xml version="1.0" encoding="not-a-real-codec"?><score-partwise/>'
        path = _write(tmp_path, "exotic.musicxml", raw)
        with pytest.raises(ValueError):
            parse_musicxml(path, language="zh-CN", profile="cn_current")


class TestLineEndsAndBudget:
    def test_crlf_is_normalised_the_way_an_xml_processor_does(
        self, tmp_path: Path
    ) -> None:
        """XML 1.0 §2.11: a processor delivers CRLF and a lone CR as LF, so a
        document saved on Windows is identical downstream to the same one
        saved on Linux — including its ``source_hash``."""
        def read(path: Path) -> str:
            block = parse_musicxml(
                path, language="zh-CN", profile="cn_current"
            ).blocks[0]
            assert isinstance(block, ScoreBlock)
            return block.text

        crlf = _write(
            tmp_path, "crlf.musicxml", _SCORE.replace("><", ">\r\n<").encode()
        )
        lf = _write(
            tmp_path, "lf.musicxml", _SCORE.replace("><", ">\n<").encode()
        )
        assert read(crlf) == read(lf)
        assert "\r" not in read(crlf)

    def test_the_character_ceiling_still_applies_to_the_decoded_text(
        self, tmp_path: Path
    ) -> None:
        """The decode changed; the budget it is read under did not. A wider
        encoding rule must not become a way past ``max_text_chars``."""
        from brailix.input import InputLimits, InputTooLargeError

        path = _write(tmp_path, "score.musicxml", _declared(_SCORE, "iso-8859-1"))
        limits = InputLimits(max_text_chars=10)
        with pytest.raises(InputTooLargeError) as excinfo:
            parse_musicxml(
                path, language="zh-CN", profile="cn_current", limits=limits
            )
        assert excinfo.value.kind == "text_chars"

    def test_the_byte_ceiling_still_applies(self, tmp_path: Path) -> None:
        from brailix.input import InputLimits, InputTooLargeError

        path = _write(tmp_path, "score.xml", _declared(_SCORE, "iso-8859-1"))
        with pytest.raises(InputTooLargeError) as excinfo:
            parse_file(
                path,
                language="zh-CN",
                profile="cn_current",
                limits=InputLimits(max_file_bytes=32),
            )
        assert excinfo.value.kind == "file_bytes"
