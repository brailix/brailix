"""Tests for :func:`brailix.input.parse_file` — suffix-based dispatch
to the existing plain / markdown parsers.

The function is a thin combiner: read file as UTF-8, pick parser by
suffix. Tests pin the dispatch table, UTF-8 handling, and propagation
of the ``language`` / ``profile`` knobs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from brailix.input import parse_file
from brailix.ir.document import Heading, List, Paragraph, ScoreBlock


class TestBomHandling:
    """A UTF-8 BOM (Windows Notepad / Word "save as .txt|.md" writes one)
    must be stripped, not survive into the first block."""

    def test_bom_markdown_still_detects_heading(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.md"
        path.write_bytes(b"\xef\xbb\xbf" + "# 标题\n".encode())
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        # A surviving BOM would make line one "﻿# 标题", failing ^#.
        assert isinstance(doc.blocks[0], Heading)

    def test_bom_plain_strips_bom(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.txt"
        path.write_bytes(b"\xef\xbb\xbf" + "你好".encode())
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].text == "你好"

    def test_utf16_le_txt_decodes(self, tmp_path: Path) -> None:
        # Windows Notepad's "save as .txt" writes UTF-16 LE + BOM; utf-8-sig
        # alone used to crash on it with UnicodeDecodeError.
        path = tmp_path / "notepad.txt"
        path.write_bytes(b"\xff\xfe" + "你好".encode("utf-16-le"))
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].text == "你好"

    def test_utf16_le_md_detects_heading(self, tmp_path: Path) -> None:
        path = tmp_path / "notepad.md"
        path.write_bytes(b"\xff\xfe" + "# 标题\n".encode("utf-16-le"))
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Heading)

    def test_utf16_be_txt_decodes(self, tmp_path: Path) -> None:
        path = tmp_path / "be.txt"
        path.write_bytes(b"\xfe\xff" + "你好".encode("utf-16-be"))
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].text == "你好"


class TestSuffixDispatch:
    def test_md_suffix_routes_to_markdown_parser(self, tmp_path: Path) -> None:
        # Markdown structure (heading + list) only the markdown parser
        # would produce; plain would lump it into one paragraph.
        path = tmp_path / "doc.md"
        path.write_text("# 标题\n\n- 一项\n- 二项\n", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Heading)
        assert isinstance(doc.blocks[1], List)

    def test_markdown_suffix_also_routes_to_markdown_parser(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "doc.markdown"
        path.write_text("# 标题\n", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Heading)

    def test_suffix_is_case_insensitive(self, tmp_path: Path) -> None:
        # Windows / mixed-case filenames shouldn't fall through to plain.
        path = tmp_path / "doc.MD"
        path.write_text("# 标题\n", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Heading)

    def test_txt_suffix_routes_to_plain_parser(self, tmp_path: Path) -> None:
        # Markdown-looking content in a .txt must NOT get parsed: each
        # blank-line-separated chunk stays a literal Paragraph (the plain
        # adapter splits on blank lines but never interprets `#` / `-`).
        path = tmp_path / "doc.txt"
        path.write_text("# not a heading\n\n- not a list\n", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert len(doc.blocks) == 2
        assert all(isinstance(b, Paragraph) for b in doc.blocks)
        assert doc.blocks[0].text == "# not a heading"
        assert doc.blocks[1].text == "- not a list"

    def test_unknown_suffix_falls_back_to_plain(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.log"
        path.write_text("一段日志。", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "一段日志。"

    def test_no_suffix_falls_back_to_plain(self, tmp_path: Path) -> None:
        path = tmp_path / "README"
        path.write_text("项目说明", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Paragraph)


_SCORE_XML = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    "<score-partwise version=\"3.1\">\n"
    "  <part-list><score-part id=\"P1\"><part-name>Music</part-name>"
    "</score-part></part-list>\n"
    "  <part id=\"P1\"></part>\n"
    "</score-partwise>\n"
)


class TestXmlSniffing:
    def test_score_xml_routes_to_music(self, tmp_path: Path) -> None:
        # A .xml whose head is a MusicXML score becomes a ScoreBlock.
        path = tmp_path / "score.xml"
        path.write_text(_SCORE_XML, encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)

    def test_non_score_xml_falls_back_to_plain(self, tmp_path: Path) -> None:
        # A generic .xml (here MathML) must NOT be force-parsed as a score —
        # it falls back to plain text instead of producing MUSIC_* warnings
        # / an empty score tree.
        path = tmp_path / "eq.xml"
        path.write_text(
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi></math>',
            encoding="utf-8",
        )
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Paragraph)
        assert not isinstance(doc.blocks[0], ScoreBlock)

    def test_utf16_score_xml_routes_to_music(self, tmp_path: Path) -> None:
        # Regression: a UTF-16 MusicXML .xml (Finale / some Windows exporters
        # write a BOM) must route to a ScoreBlock, not crash. The .xml sniff
        # used to read utf-8-sig and raise UnicodeDecodeError before the sniff
        # ran, while the byte-identical .musicxml parsed fine.
        path = tmp_path / "score.xml"
        path.write_bytes(_SCORE_XML.encode("utf-16"))  # encodes a BOM
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)

    def test_utf16_non_score_xml_falls_back_to_plain(
        self, tmp_path: Path
    ) -> None:
        # A UTF-16 non-score .xml must also survive the BOM-aware sniff and
        # degrade to plain text instead of crashing on the decode.
        path = tmp_path / "eq.xml"
        path.write_bytes(
            '<math xmlns="http://www.w3.org/1998/Math/MathML"><mi>x</mi>'
            "</math>".encode("utf-16")
        )
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Paragraph)
        assert not isinstance(doc.blocks[0], ScoreBlock)

    def test_musicxml_suffix_still_routes_to_music(self, tmp_path: Path) -> None:
        # The dedicated .musicxml suffix is unconditional (no sniff needed).
        path = tmp_path / "song.musicxml"
        path.write_text(_SCORE_XML, encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], ScoreBlock)


class TestEncoding:
    def test_utf8_chinese_content_round_trips(self, tmp_path: Path) -> None:
        # The whole reason we pin UTF-8: Chinese content is the
        # library's primary use case and must survive read.
        path = tmp_path / "zh.txt"
        path.write_text("我在重庆。", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        assert doc.blocks[0].text == "我在重庆。"

    def test_non_utf8_bytes_raise(self, tmp_path: Path) -> None:
        # GBK-encoded Chinese bytes aren't valid UTF-8; we propagate
        # the decode error rather than silently mangling content.
        path = tmp_path / "gbk.txt"
        path.write_bytes("我在重庆。".encode("gbk"))
        with pytest.raises(UnicodeDecodeError):
            parse_file(path, profile="cn_current", language="zh-CN")


class TestPathHandling:
    def test_accepts_str_path(self, tmp_path: Path) -> None:
        # Caller-friendly: ``str(path)`` should work just as well as
        # passing a ``Path`` directly.
        path = tmp_path / "doc.md"
        path.write_text("# 标题\n", encoding="utf-8")
        doc = parse_file(str(path), profile="cn_current", language="zh-CN")
        assert isinstance(doc.blocks[0], Heading)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_file(tmp_path / "does_not_exist.md", profile="cn_current", language="zh-CN")


class TestMetadataPropagation:
    def test_defaults_when_not_specified(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.txt"
        path.write_text("hi", encoding="utf-8")
        doc = parse_file(path, profile="cn_current", language="zh-CN")
        # Defaults come from brailix.core.defaults; we don't pin the
        # exact value here so changing the default doesn't ripple
        # through this test.
        assert "language" in doc.metadata
        assert "profile" in doc.metadata

    def test_kwargs_override_defaults(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.md"
        path.write_text("# Title\n", encoding="utf-8")
        doc = parse_file(path, language="en", profile="ueb")
        assert doc.metadata["language"] == "en"
        assert doc.metadata["profile"] == "ueb"


class TestDocDispatch:
    def test_doc_suffix_routes_to_parse_doc(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # ``.doc`` dispatches to parse_doc (legacy binary), not the plain
        # reader. With no converter available parse_doc raises ParseError
        # naming the format — proof the dispatch reached it (the plain reader
        # would instead UTF-8-decode the binary and raise UnicodeDecodeError).
        from brailix.core.errors import ParseError

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter", lambda override: None
        )
        path = tmp_path / "legacy.doc"
        path.write_bytes(b"\xd0\xcf\x11\xe0")  # OLE magic bytes
        with pytest.raises(ParseError) as exc:
            parse_file(path, profile="cn_current", language="zh-CN")
        assert ".doc" in str(exc.value)


class TestMathtypeFallbackForwarding:
    """``mathtype_fallback`` reaches ``parse_docx`` through ``parse_file``, and
    ``Pipeline.parse_file`` drives it from the ``input.docx.mathtype_fallback``
    profile feature (mirroring how ``detect_chemistry`` is wired)."""

    def test_parse_file_forwards_mathtype_fallback_to_parse_docx(
        self, tmp_path: Path
    ) -> None:
        # An invalid value is rejected by parse_docx's validation, which runs
        # before the file is opened — proof the kwarg reached parse_docx rather
        # than being dropped (a dropped kwarg would surface FileNotFoundError
        # for the missing .docx instead). No python-docx needed: validation
        # precedes the docx import.
        with pytest.raises(ValueError, match="mathtype_fallback"):
            parse_file(tmp_path / "missing.docx", mathtype_fallback="bogus", profile="cn_current", language="zh-CN")

    def test_pipeline_parse_file_reads_profile_feature(self, monkeypatch) -> None:
        # Pipeline.parse_file forwards the input.docx.mathtype_fallback profile
        # feature to brailix.input.parse_file. cn_current ships no value, so the
        # "off" default reaches the call. Spy on the module-level _parse_file so
        # the assertion doesn't depend on a real document or the docx extra.
        import brailix.pipeline as pipeline_mod
        from brailix.ir.document import DocumentIR
        from brailix.pipeline import Pipeline

        captured: dict = {}

        def spy(path, **kwargs):
            captured.update(kwargs)
            return DocumentIR()

        monkeypatch.setattr(pipeline_mod, "_parse_file", spy)
        Pipeline(profile="cn_current").parse_file("ignored.docx")
        assert captured["mathtype_fallback"] == "off"
        # The existing chem feature is still forwarded alongside it.
        assert "chem_detection" in captured


class TestRouteTable:
    def test_format_routes_have_disjoint_suffixes(self) -> None:
        # The flattened suffix→handler lookup in parse_file assumes the route
        # suffix sets don't overlap (otherwise the last one silently wins).
        # Lock that invariant so a new format reusing a suffix fails loudly.
        from brailix.input import _FORMAT_ROUTES

        seen: set[str] = set()
        for suffixes, _handler in _FORMAT_ROUTES:
            overlap = suffixes & seen
            assert not overlap, f"suffix claimed by two routes: {overlap}"
            seen |= suffixes
