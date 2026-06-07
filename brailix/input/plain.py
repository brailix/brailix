"""Plain-text input adapter: wrap a string as a :class:`DocumentIR`,
splitting on blank lines into one :class:`Paragraph` per paragraph.

Paragraphs are separated by blank lines (a line that is empty or
whitespace-only), the same convention :mod:`brailix.input.markdown`
uses. A single newline *inside* a paragraph stays in the block text and
renders as a word-boundary space (the segmenter categorises ``\\n`` as
whitespace), so soft-wrapped source lines join into one paragraph.

Splitting into blocks (rather than one monolithic block holding the
whole file) lets a front-end compile, cache, and re-render a large
document one paragraph at a time instead of recompiling the whole thing
on every edit — the incremental-compilation pattern (see ARCHITECTURE
§9.1). Each block carries an exact :class:`Span` back into the source so
per-cell proofread mapping stays aligned.
"""

from __future__ import annotations

import re

from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.core.span import Span
from brailix.ir.document import Block, DocumentIR, Paragraph

# A blank line — two or more newlines, ignoring spaces/tabs on the blank
# line(s) — separates paragraphs. A single newline does NOT match, so it
# stays inside a paragraph (rendered as a space). The trailing ``\s*``
# folds any further run of blank lines into one separator so consecutive
# blank lines don't produce empty paragraphs.
_BLANK_LINE = re.compile(r"\n[ \t]*\n\s*")


def _paragraph_blocks(text: str) -> list[Block]:
    """Split ``text`` on blank lines into one :class:`Paragraph` per chunk.

    Each chunk is the verbatim source slice with surrounding whitespace
    trimmed; the :class:`Span` is adjusted so ``text[span.start:span.end]``
    still equals the block's ``text`` (exact per-character provenance for
    proofread mapping). Whitespace-only chunks are dropped. Typed
    ``list[Block]`` (the Paragraphs' static supertype) so it drops
    straight into :attr:`DocumentIR.blocks` without a variance cast.
    """
    blocks: list[Block] = []
    pos = 0
    for sep in _BLANK_LINE.finditer(text):
        _add_chunk(blocks, text, pos, sep.start())
        pos = sep.end()
    _add_chunk(blocks, text, pos, len(text))
    return blocks


def _add_chunk(blocks: list[Block], text: str, start: int, end: int) -> None:
    raw = text[start:end]
    stripped = raw.strip()
    if not stripped:
        return
    lead = len(raw) - len(raw.lstrip())
    s = start + lead
    blocks.append(Paragraph(text=stripped, span=Span(s, s + len(stripped))))


def parse_plain(
    text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
) -> DocumentIR:
    """Wrap ``text`` as a :class:`DocumentIR`, one :class:`Paragraph` per
    blank-line-separated paragraph.

    Empty or whitespace-only input falls back to a single (empty) block
    so downstream tooling always has a block to anchor to; the span is
    ``None`` for genuinely empty input (nothing to point at).

    ``language`` and ``profile`` are stuffed into ``metadata`` so
    downstream renderers / proofread tools can see what the document was
    parsed for. They don't gate translation — that's :class:`Pipeline`'s
    job.
    """
    blocks: list[Block] = _paragraph_blocks(text)
    if not blocks:
        # Empty or whitespace-only: keep the historical single-block shape
        # (span=None for the empty case) so callers that always expect at
        # least one block — and the proofread/anchor layer — still work.
        blocks = [Paragraph(text=text, span=Span(0, len(text)) if text else None)]
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=blocks,
    )
