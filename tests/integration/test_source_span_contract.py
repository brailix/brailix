"""Every BrailleIR cell must carry a source span.

ARCHITECTURE.md: "每个 BrailleIR cell 都有 source_span" — the basis of the
proofreading system's bidirectional tracking (click any braille cell → jump
to the source it came from). This holds not only for cells derived from a
source character, but also for the control / spacing cells the backend
inserts: the number sign, word / column / punctuation-spacing blanks, matrix
and equation-system row breaks (``line_break``), and the hanging-indent
brackets (``hang_open`` / ``hang_close``). Those used to share span-less
sentinel instances (``BLANK_CELL`` …) or, for the number sign, be built with
no span at all — leaving the very cell a proofreader might click with no way
back to source. This is the regression guard for the fix that routes every
such cell through the span-carrying factories in ``brailix.ir.braille``.

The invariant is unconditional — *no* role is exempt (a ``role`` white-list
would drift, since ``role`` is a display tag, not a provenance contract). If a
new emitter forgets to pass a span, one of these compiles surfaces it.
"""

from __future__ import annotations

import pytest

from brailix import Pipeline
from brailix.core.span import Span
from brailix.ir.braille import BrailleDocument
from brailix.ir.document import Footnote, MathBlock


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    # ``null`` resolver keeps the prose cases independent of the optional
    # pinyin extras; span coverage doesn't depend on the reading being right.
    return Pipeline(profile="cn_current", resolver="null")


def _missing_spans(braille_ir: BrailleDocument) -> list[tuple[str | None, str | None]]:
    # Delegate discovery to the IR's own first-class check
    # (:meth:`BrailleDocument.validate_traceability`), then map each reported
    # position back to (role, source_text) for a readable failure message that
    # names which emitter forgot a span.
    return [
        (braille_ir.blocks[bi].cells[ci].role,
         braille_ir.blocks[bi].cells[ci].source_text)
        for bi, ci in braille_ir.validate_traceability()
    ]


class TestSourceSpanContract:
    def test_prose_number_latin_punct_space_all_have_span(self, pipe: Pipeline) -> None:
        # Number sign, punctuation auto-spacing, and word-boundary blanks are
        # the prose control cells that used to arrive span-less.
        for text in [
            "123",
            "3.14 47cm 100%",
            "Hello WORLD CPU MW",
            "第1个。你好，世界；再见！",
            "a b c 2026年5月17日",
            "abc123def",
        ]:
            missing = _missing_spans(pipe.translate_text(text).braille_ir)
            assert not missing, f"{text!r} → span-less cells: {missing}"

    def test_math_matrix_fraction_chem_all_have_span(self, pipe: Pipeline) -> None:
        pytest.importorskip("latex2mathml")
        for latex in [
            r"\frac{1}{2}",
            r"123 + 45",
            r"\begin{matrix}1&2\\3&4\end{matrix}",  # HANG_OPEN/CLOSE, LINE_BREAK, BLANK
            r"\begin{cases}x=1\\y=2\end{cases}",
            r"\begin{vmatrix}a&b\\c&d\end{vmatrix}",
            r"\ce{2H2 + O2 -> 2H2O}",
        ]:
            cb = pipe.translate_block(MathBlock(text=latex, source="latex"))
            missing = _missing_spans(BrailleDocument(blocks=list(cb.braille_blocks)))
            assert not missing, f"{latex!r} → span-less cells: {missing}"

    def test_list_and_table_markers_all_have_span(self, pipe: Pipeline) -> None:
        md = (
            "- 甲项\n- 乙项\n\n"
            "1. 第一\n2. 第二\n\n"
            "| 表头甲 | 表头乙 |\n|---|---|\n| 甲1 | 乙2 |\n"
        )
        doc = pipe.parse_text(md, format="markdown")
        missing = _missing_spans(pipe.translate_document(doc).braille_ir)
        assert not missing, f"list/table → span-less cells: {missing}"

    def test_footnote_ref_all_have_span(self, pipe: Pipeline) -> None:
        for ref in ("1", "1a2", "a", "*"):
            cb = pipe.translate_block(
                Footnote(ref=ref, text="脚注内容", span=Span(0, len(ref)))
            )
            missing = _missing_spans(BrailleDocument(blocks=list(cb.braille_blocks)))
            assert not missing, f"footnote {ref!r} → span-less cells: {missing}"
