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
        path.write_bytes(b"\xef\xbb\xbf" + "# 标题\n".encode("utf-8"))
        doc = parse_file(path)
        # A surviving BOM would make line one "﻿# 标题", failing ^#.
        assert isinstance(doc.blocks[0], Heading)

    def test_bom_plain_strips_bom(self, tmp_path: Path) -> None:
        path = tmp_path / "bom.txt"
        path.write_bytes(b"\xef\xbb\xbf" + "你好".encode("utf-8"))
        doc = parse_file(path)
        assert doc.blocks[0].text == "你好"


class TestSuffixDispatch:
    def test_md_suffix_routes_to_markdown_parser(self, tmp_path: Path) -> None:
        # Markdown structure (heading + list) only the markdown parser
        # would produce; plain would lump it into one paragraph.
        path = tmp_path / "doc.md"
        path.write_text("# 标题\n\n- 一项\n- 二项\n", encoding="utf-8")
        doc = parse_file(path)
        assert isinstance(doc.blocks[0], Heading)
        assert isinstance(doc.blocks[1], List)

    def test_markdown_suffix_also_routes_to_markdown_parser(
        self, tmp_path: Path
    ) -> None:
        path = tmp_path / "doc.markdown"
        path.write_text("# 标题\n", encoding="utf-8")
        doc = parse_file(path)
        assert isinstance(doc.blocks[0], Heading)

    def test_suffix_is_case_insensitive(self, tmp_path: Path) -> None:
        # Windows / mixed-case filenames shouldn't fall through to plain.
        path = tmp_path / "doc.MD"
        path.write_text("# 标题\n", encoding="utf-8")
        doc = parse_file(path)
        assert isinstance(doc.blocks[0], Heading)

    def test_txt_suffix_routes_to_plain_parser(self, tmp_path: Path) -> None:
        # Markdown-looking content in a .txt must NOT get parsed: each
        # blank-line-separated chunk stays a literal Paragraph (the plain
        # adapter splits on blank lines but never interprets `#` / `-`).
        path = tmp_path / "doc.txt"
        path.write_text("# not a heading\n\n- not a list\n", encoding="utf-8")
        doc = parse_file(path)
        assert len(doc.blocks) == 2
        assert all(isinstance(b, Paragraph) for b in doc.blocks)
        assert doc.blocks[0].text == "# not a heading"
        assert doc.blocks[1].text == "- not a list"

    def test_unknown_suffix_falls_back_to_plain(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.log"
        path.write_text("一段日志。", encoding="utf-8")
        doc = parse_file(path)
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "一段日志。"

    def test_no_suffix_falls_back_to_plain(self, tmp_path: Path) -> None:
        path = tmp_path / "README"
        path.write_text("项目说明", encoding="utf-8")
        doc = parse_file(path)
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
        doc = parse_file(path)
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
        doc = parse_file(path)
        assert isinstance(doc.blocks[0], Paragraph)
        assert not isinstance(doc.blocks[0], ScoreBlock)

    def test_musicxml_suffix_still_routes_to_music(self, tmp_path: Path) -> None:
        # The dedicated .musicxml suffix is unconditional (no sniff needed).
        path = tmp_path / "song.musicxml"
        path.write_text(_SCORE_XML, encoding="utf-8")
        doc = parse_file(path)
        assert isinstance(doc.blocks[0], ScoreBlock)


class TestEncoding:
    def test_utf8_chinese_content_round_trips(self, tmp_path: Path) -> None:
        # The whole reason we pin UTF-8: Chinese content is the
        # library's primary use case and must survive read.
        path = tmp_path / "zh.txt"
        path.write_text("我在重庆。", encoding="utf-8")
        doc = parse_file(path)
        assert doc.blocks[0].text == "我在重庆。"

    def test_non_utf8_bytes_raise(self, tmp_path: Path) -> None:
        # GBK-encoded Chinese bytes aren't valid UTF-8; we propagate
        # the decode error rather than silently mangling content.
        path = tmp_path / "gbk.txt"
        path.write_bytes("我在重庆。".encode("gbk"))
        with pytest.raises(UnicodeDecodeError):
            parse_file(path)


class TestPathHandling:
    def test_accepts_str_path(self, tmp_path: Path) -> None:
        # Caller-friendly: ``str(path)`` should work just as well as
        # passing a ``Path`` directly.
        path = tmp_path / "doc.md"
        path.write_text("# 标题\n", encoding="utf-8")
        doc = parse_file(str(path))
        assert isinstance(doc.blocks[0], Heading)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_file(tmp_path / "does_not_exist.md")


class TestMetadataPropagation:
    def test_defaults_when_not_specified(self, tmp_path: Path) -> None:
        path = tmp_path / "doc.txt"
        path.write_text("hi", encoding="utf-8")
        doc = parse_file(path)
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
