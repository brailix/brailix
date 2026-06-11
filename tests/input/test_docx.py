"""Tests for :mod:`brailix.input.docx` — Word document adapter.

Fixtures are generated programmatically with ``python-docx`` + raw
lxml so we don't have to check binary ``.docx`` blobs into the repo;
each test builds the exact document shape it needs.

The whole module is skipped when ``python-docx`` isn't importable —
the adapter is gated on the ``docx`` extras group.
"""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("docx")
pytest.importorskip("lxml")

from docx import Document  # noqa: E402
from lxml import etree  # noqa: E402

from brailix.input.docx import parse_doc, parse_docx  # noqa: E402
from brailix.ir.document import (  # noqa: E402
    Heading,
    List,
    ListItem,
    MathBlock,
    Paragraph,
    Table,
)

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_O_NS = "urn:schemas-microsoft-com:office:office"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class TestResolveDocConverter:
    """``_resolve_doc_converter`` finds the LibreOffice binary (or None)."""

    def test_command_name_resolved_via_path(self, monkeypatch) -> None:
        # A bare command name (not a file in cwd) must be looked up on PATH,
        # not skipped — the operator-precedence bug returned None for it.
        from brailix.input.docx import _resolve_doc_converter

        monkeypatch.setattr(
            "brailix.input.docx.shutil.which",
            lambda name: "/usr/bin/soffice" if name == "soffice" else None,
        )
        assert _resolve_doc_converter("soffice") == "/usr/bin/soffice"

    def test_none_when_override_not_runnable(self, monkeypatch) -> None:
        from brailix.input.docx import _resolve_doc_converter

        monkeypatch.setattr(
            "brailix.input.docx.shutil.which", lambda name: None
        )
        assert _resolve_doc_converter("nope") is None


# ---------------------------------------------------------------------------
# Fixture builders
# ---------------------------------------------------------------------------


def _omml_fragment(xml_body: str) -> etree._Element:
    """Build an ``<m:oMath>`` element from a body XML snippet."""
    wrapped = (
        f'<m:oMath xmlns:m="{_M_NS}">{xml_body}</m:oMath>'
    )
    return etree.fromstring(wrapped)


def _omml_para(xml_body: str) -> etree._Element:
    """Build an ``<m:oMathPara>`` (display) element."""
    wrapped = (
        f'<m:oMathPara xmlns:m="{_M_NS}">'
        f'<m:oMath>{xml_body}</m:oMath>'
        f'</m:oMathPara>'
    )
    return etree.fromstring(wrapped)


def _make_docx(tmp_path: Path, name: str = "doc.docx") -> tuple[Path, Document]:
    """Create a fresh empty ``.docx`` at ``tmp_path / name``."""
    path = tmp_path / name
    doc = Document()
    return path, doc


# ---------------------------------------------------------------------------
# Plain paragraph / heading / list
# ---------------------------------------------------------------------------


class TestStructuralBlocks:
    def test_single_paragraph(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("我在重庆。")
        doc.save(path)

        result = parse_docx(path)
        # python-docx adds a trailing blank paragraph by default
        # (it's the cursor position); filter empty paragraphs.
        non_empty = [
            b for b in result.blocks
            if not (isinstance(b, Paragraph) and not (b.text or "").strip())
        ]
        assert len(non_empty) == 1
        assert isinstance(non_empty[0], Paragraph)
        assert non_empty[0].text == "我在重庆。"

    def test_heading_style_becomes_heading_block(self, tmp_path: Path) -> None:
        # Headings are detected by the pStyle name. ``add_heading``
        # applies the "Heading 1" style which we recognise as level 1.
        path, doc = _make_docx(tmp_path)
        doc.add_heading("第一章", level=1)
        doc.add_heading("第一节", level=2)
        doc.add_paragraph("正文。")
        doc.save(path)

        result = parse_docx(path)
        headings = [b for b in result.blocks if isinstance(b, Heading)]
        assert [h.level for h in headings] == [1, 2]
        assert [h.text for h in headings] == ["第一章", "第一节"]

    def test_list_paragraphs_group_into_one_list(self, tmp_path: Path) -> None:
        # python-docx's ``add_paragraph(style="List Bullet")`` attaches
        # the bullet-list style which triggers ``numPr``.
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("一项", style="List Bullet")
        doc.add_paragraph("二项", style="List Bullet")
        doc.add_paragraph("三项", style="List Bullet")
        doc.save(path)

        result = parse_docx(path)
        lists = [b for b in result.blocks if isinstance(b, List)]
        assert len(lists) == 1
        assert [it.text for it in lists[0].items] == ["一项", "二项", "三项"]
        assert all(isinstance(it, ListItem) for it in lists[0].items)

    def test_table_with_two_rows(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        t = doc.add_table(rows=2, cols=2)
        t.rows[0].cells[0].text = "甲"
        t.rows[0].cells[1].text = "乙"
        t.rows[1].cells[0].text = "丙"
        t.rows[1].cells[1].text = "丁"
        doc.save(path)

        result = parse_docx(path)
        tables = [b for b in result.blocks if isinstance(b, Table)]
        assert len(tables) == 1
        rows = tables[0].rows
        assert len(rows) == 2
        assert [c.text for c in rows[0].cells] == ["甲", "乙"]
        assert [c.text for c in rows[1].cells] == ["丙", "丁"]


# ---------------------------------------------------------------------------
# Paragraph alignment (``w:jc`` → Block.align)
# ---------------------------------------------------------------------------


class TestParagraphAlignment:
    """A paragraph's ``w:jc`` survives as :attr:`Block.align`, but only for
    the alignments braille layout can honour (centre / right)."""

    def _only_paragraph(self, result) -> Paragraph:
        paras = [
            b for b in result.blocks
            if isinstance(b, Paragraph) and (b.text or "").strip()
        ]
        assert len(paras) == 1
        return paras[0]

    def test_centered_paragraph_carries_center(self, tmp_path: Path) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("居中标题")
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.save(path)

        assert self._only_paragraph(parse_docx(path)).align == "center"

    def test_right_aligned_paragraph_carries_right(self, tmp_path: Path) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("二〇二六年五月")
        para.alignment = WD_ALIGN_PARAGRAPH.RIGHT
        doc.save(path)

        assert self._only_paragraph(parse_docx(path)).align == "right"

    def test_justified_paragraph_has_no_align(self, tmp_path: Path) -> None:
        # Braille has no justification convention, so "both" normalises to
        # None — the paragraph reads flush-left, same as untagged prose.
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("一段两端对齐的正文内容")
        para.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
        doc.save(path)

        assert self._only_paragraph(parse_docx(path)).align is None

    def test_default_paragraph_has_no_align(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("普通左对齐段落")
        doc.save(path)

        assert self._only_paragraph(parse_docx(path)).align is None

    def test_centered_heading_carries_center(self, tmp_path: Path) -> None:
        # Alignment is recorded regardless of block kind: a centred level-2
        # heading carries align so the layout can centre it even though the
        # default rule centres only level 1.
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        path, doc = _make_docx(tmp_path)
        h = doc.add_heading("居中小标题", level=2)
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.save(path)

        headings = [b for b in parse_docx(path).blocks if isinstance(b, Heading)]
        assert len(headings) == 1
        assert headings[0].level == 2
        assert headings[0].align == "center"


class TestAlignmentEndToEnd:
    """parse → translate → layout: a centred Word paragraph renders centred."""

    def test_centered_paragraph_renders_centered(self, tmp_path: Path) -> None:
        from docx.enum.text import WD_ALIGN_PARAGRAPH

        from brailix.pipeline import Pipeline
        from brailix.renderer.layout import LayoutOptions, LayoutRenderer
        from brailix.renderer.unicode_braille import dots_to_char

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("一")
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER
        doc.save(path)

        ir = parse_docx(path)
        result = Pipeline(profile="cn_current").translate_document(ir)
        out = LayoutRenderer(options=LayoutOptions(line_width=20)).render(
            result.braille_ir
        )
        blank = dots_to_char(())
        content = [ln for ln in out.split("\n") if any(c != blank for c in ln)]
        assert content
        # Genuinely centred → leading padding well past the 2-cell first-line
        # indent a plain (flush-left) paragraph would have used.
        leading_blanks = len(content[0]) - len(content[0].lstrip(blank))
        assert leading_blanks > 2


# ---------------------------------------------------------------------------
# Math handling
# ---------------------------------------------------------------------------


class TestMath:
    def test_display_math_paragraph_becomes_math_block(
        self, tmp_path: Path
    ) -> None:
        # A paragraph that contains only ``<m:oMathPara>`` produces a
        # MathBlock; the OMML XML is preserved as ``MathBlock.text``.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        para._p.append(_omml_para(
            '<m:f><m:num><m:r><m:t>x</m:t></m:r></m:num>'
            '<m:den><m:r><m:t>2</m:t></m:r></m:den></m:f>'
        ))
        doc.save(path)

        result = parse_docx(path)
        math_blocks = [b for b in result.blocks if isinstance(b, MathBlock)]
        assert len(math_blocks) == 1
        assert math_blocks[0].source == "omml"
        # Round-trip preserved enough that the OMML adapter can still
        # find the fraction tags.
        assert "<m:f" in math_blocks[0].text or "m:f" in math_blocks[0].text

    def test_inline_math_embedded_in_paragraph_text(
        self, tmp_path: Path
    ) -> None:
        # Inline ``m:oMath`` (no ``m:oMathPara`` wrapper) stays in the
        # paragraph; the docx adapter swaps it for ``$<math>...</math>$``
        # so the segmenter recognises it as inline math.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("公式 ")
        para._p.append(_omml_fragment(
            '<m:sSup>'
            '<m:e><m:r><m:t>x</m:t></m:r></m:e>'
            '<m:sup><m:r><m:t>2</m:t></m:r></m:sup>'
            '</m:sSup>'
        ))
        # python-docx ``add_run`` after the math node appends text.
        para.add_run(" 是平方。")
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        # The inline equation should sit in the same paragraph text as
        # the surrounding Chinese.
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "公式" in joined
        assert "是平方" in joined
        assert "$<math" in joined and "</math>$" in joined


# ---------------------------------------------------------------------------
# MathType / Equation 3.0 OLE handling
# ---------------------------------------------------------------------------


def _embed_ole_equation(
    doc: Document, paragraph, blob: bytes, *, progid: str = "Equation.DSMT4"
) -> str:
    """Attach ``blob`` as an OLE equation part and reference it from ``paragraph``.

    Inserts a minimal ``<w:object>`` + ``<o:OLEObject>`` skeleton inside
    a new ``<w:r>`` at the end of ``paragraph``, and creates the matching
    ``OLE_OBJECT`` relationship so ``parse_docx`` can resolve the rId
    back to the part blob. Returns the assigned rId for visibility.

    The OLE part's content type and partname mirror what Word emits;
    python-docx's ``Part`` constructor + ``relate_to`` does the rest of
    the bookkeeping (rels XML, [Content_Types].xml, package zip layout).
    """
    from docx.opc.constants import RELATIONSHIP_TYPE as RT
    from docx.opc.packuri import PackURI
    from docx.opc.part import Part

    # python-docx assigns unique rIds across parts; pick the next
    # available bin number too so multiple equations don't collide.
    existing = [
        n for n in doc.part.package.iter_parts()
        if "/word/embeddings/oleObject" in n.partname
    ]
    idx = len(existing) + 1
    ole_part = Part(
        partname=PackURI(f"/word/embeddings/oleObject{idx}.bin"),
        content_type="application/vnd.openxmlformats-officedocument.oleObject",
        blob=blob,
        package=doc.part.package,
    )
    rid = doc.part.relate_to(ole_part, RT.OLE_OBJECT)

    # Build the in-paragraph markup. Word's typical layout is:
    #   <w:r>
    #     <w:object>
    #       <o:OLEObject Type="Embed" ProgID="..." r:id="...".../>
    #     </w:object>
    #   </w:r>
    obj_xml = (
        f'<w:r xmlns:w="{_W_NS}" xmlns:o="{_O_NS}" xmlns:r="{_R_NS}">'
        f'<w:object>'
        f'<o:OLEObject Type="Embed" ProgID="{progid}" '
        f'ShapeID="1000" DrawAspect="Content" ObjectID="1" '
        f'r:id="{rid}"/>'
        f'</w:object>'
        f'</w:r>'
    )
    paragraph._p.append(etree.fromstring(obj_xml))
    return rid


def _mtef_sample_payload() -> bytes:
    """Hand-crafted MTEF v5 payload encoding ``x²``.

    Reuses the same byte-builder the math-adapter tests use so any
    selector regression shows up here AND in the adapter tests.
    """
    from tests.frontend.math._mtef_builder import (
        v5_line,
        v5_prelude,
        v5_simple_char_line,
        v5_tmpl,
    )
    base = v5_simple_char_line(ord("x"))
    sup = v5_simple_char_line(ord("2"))
    # selector 28 = superscript
    return v5_prelude() + v5_line(v5_tmpl(28, [base, sup]))


def _eqnolehdr_wrapped(payload: bytes) -> bytes:
    """Prepend a 28-byte EQNOLEFILEHDR so the blob mimics an OLE stream."""
    header = (
        bytes([0x1C, 0x00])  # cbHdr=28
        + bytes([0x00, 0x00, 0x02, 0x00])  # version
        + bytes([0x00, 0x00])  # cf
        + len(payload).to_bytes(4, "little")  # cbObject
        + bytes(16)  # reserved
    )
    assert len(header) == 28
    return header + payload


class TestMathTypeOLE:
    def test_inline_ole_equation_becomes_inline_math(
        self, tmp_path: Path
    ) -> None:
        # A paragraph containing "公式 [OLE x²] 是平方" — the docx adapter
        # should resolve the OLE rId, run MTEF→MathML, and surface the
        # result as inline $<math>...</math>$ text in the paragraph.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("公式 ")
        _embed_ole_equation(
            doc, para, _eqnolehdr_wrapped(_mtef_sample_payload())
        )
        para.add_run(" 是平方。")
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "公式" in joined
        assert "是平方" in joined
        assert "$<math" in joined and "</math>$" in joined
        assert "<msup>" in joined
        assert "<mi>x</mi>" in joined
        assert "<mn>2</mn>" in joined

    def test_inline_ole_equation_real_mathtype_bytes(
        self, tmp_path: Path
    ) -> None:
        # End-to-end with REAL MathType bytes (the committed Equation
        # Native fixture), not the synthetic slot-0 builder: docx OLE →
        # MTEF decode → MathML must reproduce y = x³ with the base x
        # *inside* the <msup> (the preceding-sibling fix), proving the
        # docx path handles the real wire shape — the synthetic builder
        # puts the base in slot 0, which masked this exact bug class once.
        import pathlib

        blob = (
            pathlib.Path(__file__).parent.parent
            / "frontend" / "math" / "fixtures"
            / "mathtype_v5_y_eq_x_cubed.bin"
        ).read_bytes()  # already a full Equation Native stream (EQNOLEFILEHDR)
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("结果 ")
        _embed_ole_equation(doc, para, blob)
        doc.save(path)

        result = parse_docx(path)
        joined = "\n".join(
            p.text or "" for p in result.blocks if isinstance(p, Paragraph)
        )
        assert "$<math" in joined and "</math>$" in joined
        assert "<mi>y</mi>" in joined
        assert "<mo>=</mo>" in joined
        # Base x lives inside the script, not orphaned as a sibling.
        assert "<msup><mi>x</mi><mn>3</mn></msup>" in joined

    def test_equation_3_progid_also_recognised(
        self, tmp_path: Path
    ) -> None:
        # ProgID "Equation.3" is the legacy Microsoft Equation 3.0
        # marker; many old textbooks use it. Same MTEF wire format.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("旧公式 ")
        _embed_ole_equation(
            doc, para,
            _eqnolehdr_wrapped(_mtef_sample_payload()),
            progid="Equation.3",
        )
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "<msup>" in joined

    def test_unknown_progid_is_skipped(self, tmp_path: Path) -> None:
        # An OLE object that isn't an equation (e.g. an embedded
        # spreadsheet) must not crash parse_docx — we just leave it
        # out of the IR.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("文本 ")
        _embed_ole_equation(
            doc, para, b"random bytes", progid="Excel.Sheet.12"
        )
        para.add_run(" 末尾")
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "文本" in joined
        assert "末尾" in joined
        assert "$<math" not in joined

    def test_malformed_mtef_yields_merror_not_crash(
        self, tmp_path: Path
    ) -> None:
        # Truncated MTEF payload still has to produce a recognisable
        # inline math marker (with merror inside) — the document must
        # round-trip end-to-end without throwing.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("坏 ")
        # First byte is a v5 version, but the rest is junk → adapter
        # raises internally and falls back to merror_wrap.
        _embed_ole_equation(doc, para, bytes([5, 1, 0, 11, 0, 0xFF, 0xFF]))
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "$<math" in joined
        assert "merror" in joined

    def test_no_ole_objects_path_unaffected(self, tmp_path: Path) -> None:
        # Sanity: a document without any OLE objects produces no
        # ole_blobs entries and should walk identically to the pre-
        # patch behaviour.
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("纯文本")
        doc.save(path)

        result = parse_docx(path)
        # Build the blob map manually to confirm it's empty.
        from brailix.input.docx import _build_ole_blob_map
        assert _build_ole_blob_map(doc) == {}
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        non_empty = [p for p in paragraphs if (p.text or "").strip()]
        assert len(non_empty) == 1
        assert non_empty[0].text == "纯文本"


# ---------------------------------------------------------------------------
# Word EQ field (legacy ``eq \\f(...)`` style equations)
# ---------------------------------------------------------------------------


def _embed_eq_field(paragraph, instr: str) -> None:
    """Append a Word EQ field to ``paragraph`` using the multi-run form.

    Builds the canonical ``fldChar(begin) → instrText → fldChar(end)``
    sequence inside three runs, with an optional ``separate`` + cached
    result that the parser must ignore.
    """
    runs_xml = (
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:fldChar w:fldCharType="begin"/>'
        f'</w:r>'
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:instrText xml:space="preserve">{instr}</w:instrText>'
        f'</w:r>'
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:fldChar w:fldCharType="end"/>'
        f'</w:r>'
    )
    # ``etree.fromstring`` accepts only one root, so wrap and unpack.
    wrapper = etree.fromstring(f'<root xmlns:w="{_W_NS}">{runs_xml}</root>')
    for run in list(wrapper):
        paragraph._p.append(run)


def _embed_eq_field_with_result(paragraph, instr: str, cached: str) -> None:
    """EQ field with a ``separate`` + cached-result section."""
    runs_xml = (
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:fldChar w:fldCharType="begin"/>'
        f'</w:r>'
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:instrText xml:space="preserve">{instr}</w:instrText>'
        f'</w:r>'
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:fldChar w:fldCharType="separate"/>'
        f'</w:r>'
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:t>{cached}</w:t>'
        f'</w:r>'
        f'<w:r xmlns:w="{_W_NS}">'
        f'<w:fldChar w:fldCharType="end"/>'
        f'</w:r>'
    )
    wrapper = etree.fromstring(f'<root xmlns:w="{_W_NS}">{runs_xml}</root>')
    for run in list(wrapper):
        paragraph._p.append(run)


class TestEqField:
    def test_simple_fraction_becomes_inline_math(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("分数 ")
        _embed_eq_field(para, r"eq \f(1,2)")
        para.add_run(" 是二分之一")
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "分数" in joined
        assert "是二分之一" in joined
        assert "$<math" in joined and "</math>$" in joined
        assert "<mfrac>" in joined
        assert "<mn>1</mn>" in joined
        assert "<mn>2</mn>" in joined

    def test_piecewise_function_from_problem_15(self, tmp_path: Path) -> None:
        # The actual EQ field text from ``周练习6-5.4学生版.docx`` problem
        # 15 — the original regression that motivated this whole path.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("f(x)＝")
        _embed_eq_field(
            para,
            r"eq \b\lc\{(\a\vs4\al\co1(sin x，x≥0，,x＋2，x&lt;0，))",
        )
        para.add_run("则不等式 f(x)>")
        _embed_eq_field(para, r"eq \f(1,2)")
        para.add_run(" 的解集是____.")
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        # Surrounding Chinese text survived.
        assert "f(x)＝" in joined
        assert "解集" in joined
        # Two inline math spans — the piecewise function and the 1/2.
        assert joined.count("$<math") == 2
        # Piecewise: left brace, no right brace, mtable with 2 rows.
        assert '<mo fence="true">{</mo>' in joined
        assert '<mo fence="true">}</mo>' not in joined
        assert "<mtable" in joined
        # The fraction.
        assert "<mfrac>" in joined

    def test_cached_result_is_dropped(self, tmp_path: Path) -> None:
        # When a field has a ``separate`` + cached result, the result
        # text must NOT show up alongside the converted math — Word
        # writes a visual fallback like "x²" there, which would be
        # mistaken for paragraph text if we kept it.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _embed_eq_field_with_result(
            para, r"eq \f(1,2)", "CACHED_RESULT_TEXT"
        )
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "CACHED_RESULT_TEXT" not in joined
        assert "<mfrac>" in joined

    def test_non_eq_field_is_skipped_silently(self, tmp_path: Path) -> None:
        # HYPERLINK / PAGE / TOC and the rest of Word's non-equation
        # fields must not throw, must not produce math, and must let
        # surrounding text through unchanged.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("前")
        _embed_eq_field_with_result(
            para, r"HYPERLINK \"http://example.com\"", "click here"
        )
        para.add_run("后")
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "前" in joined
        assert "后" in joined
        assert "$<math" not in joined
        # The cached result is dropped (we don't try to display
        # hyperlink text — that's a separate feature).
        assert "click here" not in joined

    def test_fld_simple_form_also_supported(self, tmp_path: Path) -> None:
        # Word's ``<w:fldSimple w:instr="eq \\f(1,2)">`` shortcut form
        # is rarer but still used by some emitters.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("简 ")
        fld_xml = (
            f'<w:fldSimple xmlns:w="{_W_NS}" w:instr="eq \\f(1,2)">'
            f'<w:r><w:t>cached</w:t></w:r>'
            f'</w:fldSimple>'
        )
        para._p.append(etree.fromstring(fld_xml))
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "$<math" in joined
        assert "<mfrac>" in joined


# ---------------------------------------------------------------------------
# mathtype_fallback — LibreOffice escape hatch
# ---------------------------------------------------------------------------


class TestMathTypeFallback:
    def test_invalid_fallback_value_rejected(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("x")
        doc.save(path)
        with pytest.raises(ValueError):
            parse_docx(path, mathtype_fallback="bogus")

    def test_libreoffice_mode_without_converter_raises(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        from brailix.core.errors import ParseError

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: None,
        )
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("x")
        doc.save(path)
        with pytest.raises(ParseError) as exc:
            parse_docx(path, mathtype_fallback="libreoffice")
        assert "LibreOffice" in str(exc.value)

    def test_libreoffice_mode_invokes_converter(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # When mathtype_fallback="libreoffice" is set, we should
        # delegate the entire parse to the LibreOffice path even if
        # the document has no OLE objects.
        calls: list[list[str]] = []

        def fake_run(cmd, *, check, capture_output, timeout):
            calls.append(cmd)
            # Simulate LibreOffice writing the converted file.
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            stem = Path(cmd[-1]).stem
            (out_dir / f"{stem}.docx").write_bytes(
                Path(cmd[-1]).read_bytes()
            )

            class Result:
                returncode = 0
            return Result()

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: "soffice",
        )
        monkeypatch.setattr("brailix.input.docx.subprocess.run", fake_run)

        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("回归测试")
        doc.save(path)

        result = parse_docx(path, mathtype_fallback="libreoffice")
        assert len(calls) == 1
        assert "--convert-to" in calls[0]
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "回归测试" in joined

    def test_auto_mode_skips_libreoffice_when_native_succeeds(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # If the native MTEF adapter parses everything cleanly,
        # mathtype_fallback="auto" must NOT call LibreOffice.
        calls: list[list[str]] = []

        def fake_run(*args, **kwargs):
            calls.append(args)
            raise AssertionError("LibreOffice should not be invoked")

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: "soffice",
        )
        monkeypatch.setattr("brailix.input.docx.subprocess.run", fake_run)

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("前 ")
        _embed_ole_equation(
            doc, para, _eqnolehdr_wrapped(_mtef_sample_payload())
        )
        doc.save(path)

        result = parse_docx(path, mathtype_fallback="auto")
        assert calls == []
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "<msup>" in joined

    def test_recovery_needed_unit_semantics(self) -> None:
        # Unit semantics of the auto-retry decision: silent loss (fewer
        # spans than equation OLEs) and all-merror both trigger; a
        # healthy span count with at least one success doesn't.
        from brailix.input.docx import _mtef_recovery_needed
        from brailix.ir.document import DocumentIR

        def doc_with(text: str) -> DocumentIR:
            return DocumentIR(blocks=[Paragraph(text=text)])

        ok = "$<math><mi>x</mi></math>$"
        bad = "$<math><merror><mtext>?</mtext></merror></math>$"

        # One equation OLE, zero spans — it vanished silently → retry.
        assert _mtef_recovery_needed(doc_with("no math here"), 1) is True
        # Two equation OLEs, only one span — one vanished → retry.
        assert _mtef_recovery_needed(doc_with(f"前 {ok}"), 2) is True
        # Every span is a soft failure → retry.
        assert _mtef_recovery_needed(doc_with(f"前 {bad}"), 1) is True
        # As many spans as equations, at least one decoded → no retry.
        assert _mtef_recovery_needed(doc_with(f"前 {ok} 后 {bad}"), 2) is False

    def test_auto_mode_retries_when_all_mtef_failed(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # When every OLE blob produces merror, "auto" should re-parse
        # via LibreOffice.
        calls: list[list[str]] = []

        def fake_run(cmd, *, check, capture_output, timeout):
            calls.append(cmd)
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            stem = Path(cmd[-1]).stem
            # Simulate LibreOffice replacing the OLE equation with a
            # native OMML one by rewriting the file to have a simple
            # OMML expression and no OLE.
            new = _make_docx(out_dir, name=f"{stem}.docx")
            _, conv = new
            para = conv.add_paragraph("修复后 ")
            para._p.append(_omml_fragment(
                '<m:sSup>'
                '<m:e><m:r><m:t>y</m:t></m:r></m:e>'
                '<m:sup><m:r><m:t>3</m:t></m:r></m:sup>'
                '</m:sSup>'
            ))
            conv.save(out_dir / f"{stem}.docx")

            class Result:
                returncode = 0
            return Result()

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: "soffice",
        )
        monkeypatch.setattr("brailix.input.docx.subprocess.run", fake_run)

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("前 ")
        # Garbled MTEF — adapter will emit merror.
        _embed_ole_equation(doc, para, bytes([5, 1, 0, 11, 0, 0xFF, 0xFF]))
        doc.save(path)

        result = parse_docx(path, mathtype_fallback="auto")
        assert len(calls) == 1
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "修复后" in joined
        # The fallback path swapped the bad OLE for a valid OMML.
        assert "<msup>" in joined
        assert "merror" not in joined

    def test_auto_mode_retries_when_equation_ole_vanishes_silently(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # An equation OLE whose blob is neither a CFB container nor raw
        # MTEF produces NO span at all (silent drop — corrupt container,
        # third-party writer, or olefile missing).  "auto" must treat
        # that as a failure to recover, not as "no equations here" —
        # the old heuristic ("no inline math → skip") lost the whole
        # formula silently.
        calls: list[list[str]] = []

        def fake_run(cmd, *, check, capture_output, timeout):
            calls.append(cmd)
            out_dir = Path(cmd[cmd.index("--outdir") + 1])
            stem = Path(cmd[-1]).stem
            new = _make_docx(out_dir, name=f"{stem}.docx")
            _, conv = new
            para = conv.add_paragraph("修复后 ")
            para._p.append(_omml_fragment(
                '<m:sSup>'
                '<m:e><m:r><m:t>y</m:t></m:r></m:e>'
                '<m:sup><m:r><m:t>3</m:t></m:r></m:sup>'
                '</m:sSup>'
            ))
            conv.save(out_dir / f"{stem}.docx")

            class Result:
                returncode = 0
            return Result()

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: "soffice",
        )
        monkeypatch.setattr("brailix.input.docx.subprocess.run", fake_run)

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("前 ")
        # First byte 0x00: not an EQNOLEFILEHDR, not a known MTEF
        # prelude, not a CFB container → _extract_mtef_payload returns
        # None and the walker emits nothing for this object.
        _embed_ole_equation(doc, para, b"\x00not-mtef-not-cfb")
        doc.save(path)

        result = parse_docx(path, mathtype_fallback="auto")
        assert len(calls) == 1
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "修复后" in joined

    def test_auto_mode_ignores_non_equation_ole(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # A non-formula OLE (chart / Excel sheet) must NOT trigger the
        # LibreOffice round-trip — there is no equation to recover, and
        # its blob never decodes, so without the ProgID gate this would
        # look exactly like the silent-loss case above.
        def fake_run(*args, **kwargs):
            raise AssertionError("LibreOffice should not be invoked")

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: "soffice",
        )
        monkeypatch.setattr("brailix.input.docx.subprocess.run", fake_run)

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("图表 ")
        _embed_ole_equation(
            doc, para, b"\x00chart-bytes", progid="Excel.Sheet.12"
        )
        doc.save(path)

        result = parse_docx(path, mathtype_fallback="auto")
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "图表" in joined

    def test_inner_dollar_is_escaped_in_inline_math(
        self, tmp_path: Path
    ) -> None:
        # A literal ``$`` inside a Word formula must not break the
        # ``$...$`` inline wrapping — the span used to terminate at the
        # inner dollar, corrupting the formula and leaking raw XML
        # fragments into the prose.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("前 ")
        para._p.append(_omml_fragment("<m:r><m:t>a$b</m:t></m:r>"))
        doc.save(path)

        result = parse_docx(path)
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        # The wrappers are the only raw dollars; the inner one is the
        # XML character reference.
        assert joined.count("$") == 2
        assert "&#36;" in joined
        start = joined.find("$<math")
        end = joined.find("</math>$")
        assert 0 <= start < end
        # The reference round-trips to a real dollar when the span's
        # MathML is re-parsed.
        import xml.etree.ElementTree as ET

        inner = joined[start + 1 : end + len("</math>")]
        tree = ET.fromstring(inner)
        assert "$" in "".join(tree.itertext())

    def test_auto_mode_swallows_libreoffice_unavailable(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # If LibreOffice isn't around, "auto" keeps the native result
        # (including merror) rather than raising — that's the point
        # of "auto" vs "libreoffice".
        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: None,
        )

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph("前 ")
        _embed_ole_equation(doc, para, bytes([5, 1, 0, 11, 0, 0xFF, 0xFF]))
        doc.save(path)

        result = parse_docx(path, mathtype_fallback="auto")
        paragraphs = [b for b in result.blocks if isinstance(b, Paragraph)]
        joined = "\n".join(p.text or "" for p in paragraphs)
        assert "merror" in joined  # native fallback kept


# ---------------------------------------------------------------------------
# parse_doc legacy path
# ---------------------------------------------------------------------------


class TestParseDocLegacy:
    def test_missing_converter_raises_helpful_error(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        # When no LibreOffice is on PATH, parse_doc must raise
        # ParseError with a "convert to .docx first" hint — not
        # silently fail or attempt to read the binary blob.
        from brailix.core.errors import ParseError

        monkeypatch.setattr(
            "brailix.input.docx._resolve_doc_converter",
            lambda override: None,
        )
        # Create a dummy .doc file so the FileNotFoundError path is
        # not hit before the converter check.
        path = tmp_path / "legacy.doc"
        path.write_bytes(b"\xd0\xcf\x11\xe0")  # OLE magic bytes
        with pytest.raises(ParseError) as exc:
            parse_doc(path)
        assert ".doc" in str(exc.value)
        assert "LibreOffice" in str(exc.value) or "soffice" in str(exc.value)


# ---------------------------------------------------------------------------
# parse_file suffix dispatch
# ---------------------------------------------------------------------------


class TestParseFileDispatch:
    def test_docx_routes_through_parse_docx(self, tmp_path: Path) -> None:
        # End-to-end via parse_file: ``.docx`` suffix dispatches to the
        # docx adapter (not the plain reader, which would choke on
        # binary content).
        from brailix.input import parse_file

        path, doc = _make_docx(tmp_path)
        doc.add_heading("章节", level=1)
        doc.add_paragraph("正文。")
        doc.save(path)

        result = parse_file(path)
        headings = [b for b in result.blocks if isinstance(b, Heading)]
        assert len(headings) == 1
        assert headings[0].text == "章节"

    def test_docm_suffix_also_routes_to_docx(self, tmp_path: Path) -> None:
        # ``.docm`` (macro-enabled) is the same OOXML container with
        # macros embedded; parse_docx reads it identically.
        from brailix.input import parse_file

        path = tmp_path / "macros.docm"
        doc = Document()
        doc.add_paragraph("hi")
        doc.save(path)

        result = parse_file(path)
        # Just confirm the parser ran (no exception).
        assert result.blocks  # not empty


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorPaths:
    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            parse_docx(tmp_path / "nope.docx")

    def test_not_a_zip_raises_parse_error(self, tmp_path: Path) -> None:
        from brailix.core.errors import ParseError

        bogus = tmp_path / "fake.docx"
        bogus.write_bytes(b"not a real zip archive")
        with pytest.raises(ParseError):
            parse_docx(bogus)


# ---------------------------------------------------------------------------
# Run-level vertical alignment (Ctrl+Shift+= / Ctrl+= scripts) → inline math
# ---------------------------------------------------------------------------


def _vert_run_xml(text: str, vert: str | None) -> str:
    """One ``<w:r>`` with an optional ``<w:vertAlign>`` (the property Word
    sets for super/subscript)."""
    rpr = f'<w:rPr><w:vertAlign w:val="{vert}"/></w:rPr>' if vert else ""
    return (
        f'<w:r xmlns:w="{_W_NS}">{rpr}'
        f'<w:t xml:space="preserve">{text}</w:t></w:r>'
    )


def _append_script_runs(paragraph, runs: list[tuple[str, str | None]]) -> None:
    """Append ``(text, vert)`` runs to ``paragraph``; ``vert`` is
    ``"superscript"`` / ``"subscript"`` / ``None``."""
    runs_xml = "".join(_vert_run_xml(t, v) for t, v in runs)
    wrapper = etree.fromstring(f'<root xmlns:w="{_W_NS}">{runs_xml}</root>')
    for run in list(wrapper):
        paragraph._p.append(run)


def _para_text(result) -> str:
    return "\n".join(
        p.text or "" for p in result.blocks if isinstance(p, Paragraph)
    )


class TestVertAlignScripts:
    """Scripts set via Ctrl+Shift+= / Ctrl+= become inline math. Always on —
    they used to be flattened to bare text, losing the formula."""

    def test_superscript_becomes_msup(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(para, [("x", None), ("2", "superscript")])
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "$<math" in text and "</math>$" in text
        assert "<msup>" in text
        assert "<mi>x</mi>" in text and "<mn>2</mn>" in text

    def test_subscript_becomes_msub(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("H", None), ("2", "subscript"), ("O", None)]
        )
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "<msub>" in text
        assert "<mi>H</mi>" in text and "<mn>2</mn>" in text
        assert "<mi>O</mi>" in text
        # chemistry detection is off by default → plain math, no chem tag.
        assert "data-bk-chem" not in text

    def test_prose_around_script_stays_prose(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        para.add_run("面积是 ")
        _append_script_runs(para, [("x", None), ("2", "superscript")])
        para.add_run(" 平方米")
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "面积是" in text and "平方米" in text
        assert "$<math" in text
        # The surrounding Chinese must not be swallowed into the math island.
        island = text[text.index("$<math"):text.index("</math>$")]
        assert "面积" not in island and "平方" not in island

    def test_unit_superscript(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)  # m² — square metre
        para = doc.add_paragraph()
        _append_script_runs(para, [("m", None), ("2", "superscript")])
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "<msup>" in text and "<mi>m</mi>" in text

    def test_subscript_then_superscript_is_msubsup(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("x", None), ("1", "subscript"), ("2", "superscript")]
        )
        doc.save(path)
        assert "<msubsup>" in _para_text(parse_docx(path))

    def test_negative_exponent_uses_canonical_minus(self, tmp_path: Path) -> None:
        # 10⁻³ — the hyphen-minus is canonicalised to U+2212 so the math
        # backend's symbol table matches (a raw '-' is MATH_UNKNOWN_SYMBOL).
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("10", None), ("-", "superscript"), ("3", "superscript")]
        )
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "<msup>" in text and "−" in text and "<mn>3</mn>" in text

    def test_plain_text_without_vertalign_unchanged(self, tmp_path: Path) -> None:
        # Regression: ordinary "H2O" (no vertAlign) must NOT become math.
        path, doc = _make_docx(tmp_path)
        doc.add_paragraph("H2O 是水")
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "$<math" not in text and "H2O" in text

    def test_super_run_without_text_is_safe(self, tmp_path: Path) -> None:
        # A superscript run carrying no <w:t> (e.g. an auto footnote mark)
        # produces no math and doesn't crash.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        para.add_run("见正文")
        empty = (
            f'<w:r xmlns:w="{_W_NS}"><w:rPr>'
            f'<w:vertAlign w:val="superscript"/></w:rPr></w:r>'
        )
        wrapper = etree.fromstring(f'<root xmlns:w="{_W_NS}">{empty}</root>')
        for run in list(wrapper):
            para._p.append(run)
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "见正文" in text and "$<math" not in text

    def test_cluster_adjacent_to_omml_no_double_dollar(self, tmp_path: Path) -> None:
        # A script cluster directly followed by an OMML island must keep a
        # separator so the segmenter's inline-math regex (which rejects $$)
        # still sees both.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(para, [("a", None), ("2", "superscript")])
        para._p.append(_omml_fragment("<m:r><m:t>z</m:t></m:r>"))
        doc.save(path)
        assert "$$" not in _para_text(parse_docx(path))

    def test_end_to_end_braille_superscript(self, tmp_path: Path) -> None:
        from brailix.pipeline import Pipeline

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(para, [("x", None), ("2", "superscript")])
        doc.save(path)
        result = Pipeline(profile="cn_current").translate_document(parse_docx(path))
        # ⠰⠭⠌⠆ : latin-x prefix, superscript marker, lowered 2.
        assert "⠰⠭⠌⠆" in result.render("unicode")


class TestVertAlignChemistry:
    """Conservative chemistry reading of script clusters — opt-in via
    ``chem_detection`` (the ``input.docx.detect_chemistry`` profile feature),
    off by default because a lone single-letter subscript coincides with an
    element symbol."""

    def test_off_by_default_h2o_is_math(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("H", None), ("2", "subscript"), ("O", None)]
        )
        doc.save(path)
        text = _para_text(parse_docx(path))
        assert "data-bk-chem" not in text and "<msub>" in text

    def test_on_h2o_tagged_chem(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("H", None), ("2", "subscript"), ("O", None)]
        )
        doc.save(path)
        text = _para_text(parse_docx(path, chem_detection=True))
        assert 'data-bk-chem="1"' in text

    def test_single_letter_variable_stays_math(self, tmp_path: Path) -> None:
        # V₁ — vanadium IS an element, but a lone single-letter subscript is
        # almost always a maths/physics variable, so it stays math.
        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(para, [("V", None), ("1", "subscript")])
        doc.save(path)
        text = _para_text(parse_docx(path, chem_detection=True))
        assert "data-bk-chem" not in text and "<msub>" in text

    def test_lowercase_base_stays_math(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)  # x² — x isn't an element symbol
        para = doc.add_paragraph()
        _append_script_runs(para, [("x", None), ("2", "superscript")])
        doc.save(path)
        text = _para_text(parse_docx(path, chem_detection=True))
        assert "data-bk-chem" not in text and "<msup>" in text

    def test_charge_is_chem(self, tmp_path: Path) -> None:
        path, doc = _make_docx(tmp_path)  # Ca²⁺
        para = doc.add_paragraph()
        _append_script_runs(
            para,
            [("Ca", None), ("2", "superscript"), ("+", "superscript")],
        )
        doc.save(path)
        text = _para_text(parse_docx(path, chem_detection=True))
        assert 'data-bk-chem="1"' in text

    def test_end_to_end_chem_braille_h2o(self, tmp_path: Path) -> None:
        from brailix.pipeline import Pipeline

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("H", None), ("2", "subscript"), ("O", None)]
        )
        doc.save(path)
        doc_ir = parse_docx(path, chem_detection=True)
        result = Pipeline(profile="cn_current").translate_document(doc_ir)
        # ⠸⠓⠆⠕ : chemical-formula indicator ⠸, H, lowered 2, O.
        assert "⠸⠓⠆⠕" in result.render("unicode")

    def test_profile_feature_enables_chem(self, tmp_path: Path) -> None:
        # cn_current ships input.docx.detect_chemistry=true, so the Pipeline
        # reads H₂O as chemistry through translate_file without the caller
        # passing chem_detection at all.
        from brailix.pipeline import Pipeline

        path, doc = _make_docx(tmp_path)
        para = doc.add_paragraph()
        _append_script_runs(
            para, [("H", None), ("2", "subscript"), ("O", None)]
        )
        doc.save(path)
        result = Pipeline(profile="cn_current").translate_file(path)
        assert "⠸⠓⠆⠕" in result.render("unicode")
