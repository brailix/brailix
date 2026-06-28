"""Property-based / API-level golden tests that don't fit the JSON corpus.

Most golden tests live in ``data/*.json`` and run through
:mod:`test_goldens` — they pin an exact ``input → braille`` mapping.
The tests in this file are different: they check **properties** of the
pipeline (spacing rules, state isolation, custom ``data-bk-span``
attributes) rather than locking a single output string. Splitting them
out keeps the JSON corpus a pure data file that non-coders can edit.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix import Pipeline
from brailix.backend.block import translate_document
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import WarningCollector
from brailix.core.span import Span
from brailix.ir.document import DocumentIR, Paragraph
from brailix.ir.inline import MathInline
from brailix.renderer import renderer_registry

# ---------------------------------------------------------------------------
# Word-spacing property: blank cell between adjacent tokens.
# ---------------------------------------------------------------------------


def test_word_spacing_inserts_blank(pipe):
    """The renderer must inject one blank cell between adjacent tokens
    even if the source had no separator (write each word as a run, with a
    space between words)."""
    rendered = pipe.translate_text("我在").render()
    # We just need to confirm a blank (U+2800) appears between the two
    # tokens — exact spelling is locked by zh_text.json.
    assert "⠀" in rendered, rendered


# ---------------------------------------------------------------------------
# Running English: the lowercase sign ⠰ is dropped on a following lowercase
# word, and Chinese breaks the run so the next English word re-announces.
# ---------------------------------------------------------------------------


def test_chinese_breaks_running_english_re_signs(pipe):
    """A Chinese word breaks the running-English context: the lowercase
    sign ⠰ opens "hello", is dropped on the mid-run "world", then
    re-appears on "foo" only because 你 reset the run in between."""
    rendered = pipe.translate_text("hello world 你 foo").render()
    # Exactly two lowercase signs ⠰ (U+2830): opening hello, re-opening
    # foo. "world" mid-run carries none, and 你's pinyin uses no ⠰.
    assert rendered.count("⠰") == 2, rendered


# ---------------------------------------------------------------------------
# data-bk-span: feed raw MathML directly through the backend.
# ---------------------------------------------------------------------------
#
# The math backend honours ``data-bk-span="start,end"`` on any element to
# override the source-span of every cell emitted while that element is
# being rendered. This is the entry-point that allows external producers
# (XHTML, EPUB, Word) to pre-mark MathML with precise source provenance.
# The pipeline still renders the same braille whether the attribute is
# present or absent.


def test_data_bk_span_direct_mathml_renders_same_string():
    """Round-tripping MathML directly through the backend (bypassing the
    LaTeX adapter) gives the same braille as the LaTeX equivalent."""
    profile = load_profile("cn_current")
    warnings = WarningCollector()
    backend_ctx = BackendContext(profile="cn_current", warnings=warnings)

    # Build a MathInline carrying a hand-written MathML tree with a
    # data-bk-span override on the inner <mi>.
    tree = ET.fromstring('<math><mi data-bk-span="0,1">x</mi></math>')
    node = MathInline(surface="x", span=Span(0, 1), source="mathml", math=tree)
    doc = DocumentIR(
        metadata={"language": "zh-CN", "profile": "cn_current"},
        blocks=[Paragraph(children=[node], span=Span(0, 1))],
    )
    braille_doc = translate_document(doc, backend_ctx, profile)
    rendered = renderer_registry.get("unicode").render(braille_doc)
    assert rendered == "⠰⠭", rendered


def test_data_bk_span_overrides_cell_provenance():
    """Cells emitted inside a data-bk-span subtree must carry that span."""
    profile = load_profile("cn_current")
    warnings = WarningCollector()
    backend_ctx = BackendContext(profile="cn_current", warnings=warnings)
    tree = ET.fromstring('<math><mi data-bk-span="5,6">x</mi></math>')
    node = MathInline(surface="x", span=Span(0, 1), source="mathml", math=tree)
    doc = DocumentIR(
        metadata={"language": "zh-CN", "profile": "cn_current"},
        blocks=[Paragraph(children=[node], span=Span(0, 1))],
    )
    braille_doc = translate_document(doc, backend_ctx, profile)
    cells = braille_doc.blocks[0].cells
    assert cells, "expected at least one cell"
    assert all(c.source_span == Span(5, 6) for c in cells), (
        f"expected all cells to inherit span (5,6); got "
        f"{[c.source_span for c in cells]!r}"
    )


def test_data_bk_span_malformed_is_silently_ignored():
    """A malformed data-bk-span attribute is ignored — translation proceeds
    using the surrounding fallback span instead."""
    profile = load_profile("cn_current")
    warnings = WarningCollector()
    backend_ctx = BackendContext(profile="cn_current", warnings=warnings)
    tree = ET.fromstring('<math><mi data-bk-span="not,a,span">x</mi></math>')
    node = MathInline(surface="x", span=Span(0, 1), source="mathml", math=tree)
    doc = DocumentIR(
        metadata={"language": "zh-CN", "profile": "cn_current"},
        blocks=[Paragraph(children=[node], span=Span(0, 1))],
    )
    braille_doc = translate_document(doc, backend_ctx, profile)
    rendered = renderer_registry.get("unicode").render(braille_doc)
    assert rendered == "⠰⠭"


# ---------------------------------------------------------------------------
# Pipeline reuse: same pipe with multiple calls must not leak state.
# ---------------------------------------------------------------------------


def test_pipeline_reuse_isolates_calls():
    """Two translate_text calls back-to-back must produce independent
    results (no number_sign / capital_indicator state leak)."""
    p = Pipeline(profile="cn_current")
    a = p.translate_text(r"$1$").render()
    b = p.translate_text(r"$2$").render()
    assert a == "⠼⠁", a
    assert b == "⠼⠃", b
