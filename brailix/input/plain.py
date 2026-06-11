"""Plain-text input adapter: wrap a string as a :class:`DocumentIR`,
one :class:`Paragraph` per source line.

Plain text has no soft-wrap convention: unlike Markdown (where a single
newline is presentation, joined into the paragraph as a space), a
newline in a ``.txt`` file is the author's layout intent — Chinese
documents conventionally put one paragraph per line, and poetry / song
lyrics put one verse per line. So **every** newline is a paragraph
boundary here, and the braille output breaks where the source breaks.
Blank lines contribute no block of their own (braille paragraphs are
distinguished by the layout's first-line indent, not by blank lines),
so runs of newlines just collapse. Markdown sources wanting the
join-on-single-newline behaviour go through
:mod:`brailix.input.markdown` instead.

Splitting into blocks (rather than one monolithic block holding the
whole file) lets a front-end compile, cache, and re-render a large
document one line at a time instead of recompiling the whole thing
on every edit — the incremental-compilation pattern (see ARCHITECTURE
§9.1). Each block carries an exact :class:`Span` back into the source so
per-cell proofread mapping stays aligned.
"""

from __future__ import annotations

from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.core.span import Span
from brailix.ir.document import Block, DocumentIR, Paragraph


def _paragraph_blocks(text: str) -> list[Block]:
    """Split ``text`` on newlines into one :class:`Paragraph` per line.

    Each line is the verbatim source slice with surrounding whitespace
    trimmed; the :class:`Span` is adjusted so ``text[span.start:span.end]``
    still equals the block's ``text`` (exact per-character provenance for
    proofread mapping). Whitespace-only lines are dropped — they separate
    paragraphs but render nothing of their own. Typed ``list[Block]``
    (the Paragraphs' static supertype) so it drops straight into
    :attr:`DocumentIR.blocks` without a variance cast.
    """
    blocks: list[Block] = []
    pos = 0
    while pos <= len(text):
        nl = text.find("\n", pos)
        end = len(text) if nl == -1 else nl
        _add_chunk(blocks, text, pos, end)
        if nl == -1:
            break
        pos = nl + 1
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
    source line.

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
