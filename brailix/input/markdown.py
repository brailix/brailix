r"""Markdown input adapter: parse a common Markdown subset into
:class:`DocumentIR`.

The parser is intentionally small and dependency-free so the core
library stays pure-stdlib. A real CommonMark-compliant parser belongs
in an optional ``markdown`` extras adapter (planned: a thin shim over
``markdown-it-py``); the goal here is to cover the structural blocks
brailix needs to render — not to handle Markdown's full grammar.

Supported block syntax (per line, top of block detected by leading
pattern):

* ``# heading`` / ``## heading`` ... ``###### heading`` → :class:`Heading`
  (level 1..6). Trailing ``#`` decorations are stripped.
* ``- item`` / ``* item`` / ``+ item`` → unordered :class:`List` with
  :class:`ListItem`\ s. Items run until the next blank line or a
  non-item line.
* ``1. item`` / ``2) item`` → ordered :class:`List`. The numeric
  value is preserved but rendering uses sequence position (per CommonMark).
* ``> quote`` (one ``>`` per quoted line) → :class:`Quote`.
* Fenced code: ``\`\`\`lang`` ... ``\`\`\``` → :class:`CodeBlock` (the
  ``lang`` token, if present, is stored on the block).
* ``$$display math$$`` → :class:`MathBlock` (``source="latex"``).
* ``| col1 | col2 |`` lines on consecutive rows → :class:`Table`. A
  separator row (``| --- | --- |``) is recognised but doesn't change
  rendering — alignment is a future enhancement.
* Any other line → joined into a :class:`Paragraph` until a blank line
  ends it.

Inline formatting (bold / italic / inline math / inline code) is
**not parsed** here — those would clash with braille conventions and
should be handled by the renderer / front-end if needed. ``$...$``
inline math survives as part of paragraph text and is picked up by
the frontend's segmenter.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.core.span import Span
from brailix.ir.document import (
    Block,
    CodeBlock,
    DocumentIR,
    Heading,
    List,
    ListItem,
    MathBlock,
    Paragraph,
    Quote,
    Table,
    TableCell,
    TableRow,
)

# ---------------------------------------------------------------------------
# Line-level patterns
# ---------------------------------------------------------------------------


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")
_UNORDERED_RE = re.compile(r"^([-*+])\s+(.*)$")
_ORDERED_RE = re.compile(r"^(\d+)[.)]\s+(.*)$")
_QUOTE_RE = re.compile(r"^>\s?(.*)$")
_FENCE_RE = re.compile(r"^```\s*(\S*)\s*$")
_DOLLAR_FENCE = "$$"
_TABLE_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_CHARS = re.compile(r"^[\s\-|:]+$")


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_markdown(
    text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
) -> DocumentIR:
    """Parse ``text`` as Markdown into a :class:`DocumentIR`.

    The result has block-level structure populated; inline content is
    left as raw :attr:`Block.text`. The Pipeline's frontend tokenises
    that text per language / domain rules — Markdown's grammar
    deliberately does **not** reach inside paragraphs.
    """
    blocks: list[Block] = list(_iter_blocks(text))
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=blocks,
    )


# ---------------------------------------------------------------------------
# Block recogniser
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _LineCursor:
    """Tiny cursor over the source lines.

    Tracks source offsets so each emitted :class:`Block` carries a
    :class:`Span` back into the Markdown original — useful for
    proofread tools that want to map a generated cell back to its
    Markdown line.
    """

    text: str
    lines: list[str]
    line_offsets: list[int]
    i: int = 0

    @classmethod
    def from_text(cls, text: str) -> _LineCursor:
        lines = text.split("\n")
        offsets: list[int] = []
        pos = 0
        for line in lines:
            offsets.append(pos)
            pos += len(line) + 1  # +1 for the consumed '\n'
        return cls(text=text, lines=lines, line_offsets=offsets)

    def peek(self) -> str | None:
        return self.lines[self.i] if self.i < len(self.lines) else None

    def consume(self) -> str:
        line = self.lines[self.i]
        self.i += 1
        return line

    def span_of(self, start_line: int, end_line: int) -> Span:
        """Span covering ``lines[start_line:end_line]`` in the source."""
        start = self.line_offsets[start_line]
        if end_line >= len(self.line_offsets):
            end = len(self.text)
        else:
            end = self.line_offsets[end_line] - 1  # strip the trailing \n
            if end < start:
                end = start
        return Span(start, end)


def _iter_blocks(text: str) -> Iterable[Block]:
    """Yield blocks from ``text`` until exhausted.

    Each branch consumes from the cursor in line-blocks; ordering
    matters because some prefixes shadow others (a ``>`` line that
    starts with a ``#`` is still a quote, not a heading).
    """
    cur = _LineCursor.from_text(text)
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None:
            break
        stripped = line.strip()

        if not stripped:
            cur.consume()
            continue

        # Fenced code block.
        m_fence = _FENCE_RE.match(stripped)
        if m_fence:
            yield _consume_fenced_code(cur, m_fence.group(1) or None)
            continue

        # $$ display math fence.
        if stripped.startswith(_DOLLAR_FENCE):
            math_block = _consume_dollar_math(cur)
            if math_block is not None:
                yield math_block
                continue
            # Not a fence; fall through to paragraph below.

        # Heading.
        m_h = _HEADING_RE.match(stripped)
        if m_h:
            start = cur.i
            cur.consume()
            level = len(m_h.group(1))
            yield Heading(
                level=level,
                text=m_h.group(2),
                span=cur.span_of(start, start + 1),
            )
            continue

        # Quote — one or more consecutive ``>`` lines.
        if _QUOTE_RE.match(stripped):
            yield _consume_quote(cur)
            continue

        # Ordered / unordered list.
        if _UNORDERED_RE.match(line) or _ORDERED_RE.match(line):
            yield _consume_list(cur)
            continue

        # Table.
        if _TABLE_RE.match(line):
            table_block = _consume_table(cur)
            if table_block is not None:
                yield table_block
                continue
            # Not a real table (single line, no separator) → paragraph.

        # Default: paragraph until blank / next-block boundary.
        yield _consume_paragraph(cur)


# ---------------------------------------------------------------------------
# Per-block consumers
# ---------------------------------------------------------------------------


def _consume_fenced_code(cur: _LineCursor, language: str | None) -> CodeBlock:
    r"""Eat the opening ``\`\`\``` fence, content lines, and the closing
    fence (if present). Missing closing fence is tolerated — we stop
    at EOF so a runaway fence doesn't swallow the rest of the doc.
    """
    start = cur.i
    cur.consume()  # opening fence
    body: list[str] = []
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None:
            break
        if _FENCE_RE.match(line.strip()):
            cur.consume()  # closing fence
            break
        body.append(line)
        cur.consume()
    return CodeBlock(
        language=language,
        text="\n".join(body),
        span=cur.span_of(start, cur.i),
    )


def _consume_dollar_math(cur: _LineCursor) -> MathBlock | None:
    """Eat a ``$$ ... $$`` fence pair.

    Returns ``None`` (without consuming) if the start line lacks a
    matching closing line — the caller then treats it as a normal
    paragraph. This keeps short-form ``$$x = y$$`` on one line working
    as a single math block.
    """
    start = cur.i
    first = cur.lines[start]
    stripped = first.strip()
    # Single-line case: ``$$x = y$$``
    if stripped.endswith(_DOLLAR_FENCE) and len(stripped) > 4:
        body = stripped[2:-2].strip()
        cur.consume()
        return MathBlock(
            source=_infer_math_source(body),
            text=body,
            span=cur.span_of(start, start + 1),
        )
    # Multi-line case: opening line is exactly ``$$`` (possibly with
    # leading content after it).
    body_lines: list[str] = []
    leading_after_fence = stripped[2:].lstrip()
    if leading_after_fence:
        body_lines.append(leading_after_fence)
    saw_close = False
    end_line = start + 1
    while end_line < len(cur.lines):
        line = cur.lines[end_line]
        stripped_line = line.strip()
        if stripped_line.endswith(_DOLLAR_FENCE):
            # Closing fence — strip the trailing ``$$`` and keep any
            # preceding content on this line.
            head = stripped_line[:-2].rstrip()
            if head:
                body_lines.append(head)
            saw_close = True
            end_line += 1
            break
        body_lines.append(line)
        end_line += 1
    if not saw_close:
        return None
    # Advance the cursor past the consumed range.
    cur.i = end_line
    joined_body = "\n".join(body_lines).strip()
    return MathBlock(
        source=_infer_math_source(joined_body),
        text=joined_body,
        span=cur.span_of(start, end_line),
    )


def _infer_math_source(body: str) -> str:
    """Pick the math source dialect for a ``$$...$$`` body.

    Mirrors the inline detector in :mod:`brailix.frontend.normalize`:
    a body that starts with ``<math`` is MathML (typically synthesised
    by the Word import path); anything else is LaTeX. The discriminator
    is structural — LaTeX grammar can't begin with an XML element, so
    a single-prefix check is robust enough without parsing.
    """
    return "mathml" if body.lstrip().startswith("<math") else "latex"


def _consume_quote(cur: _LineCursor) -> Quote:
    """Eat ``>`` lines until a non-quote line or blank line."""
    start = cur.i
    body: list[str] = []
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None or not line.strip():
            break
        m = _QUOTE_RE.match(line.strip())
        if not m:
            break
        body.append(m.group(1))
        cur.consume()
    return Quote(
        text="\n".join(body),
        span=cur.span_of(start, cur.i),
    )


def _consume_list(cur: _LineCursor) -> List:
    """Eat consecutive list-item lines into one :class:`List`.

    Detects ordered vs unordered from the first item. The list ends
    at the first blank line or non-item line.
    """
    start = cur.i
    first_line = cur.peek() or ""
    ordered = _ORDERED_RE.match(first_line) is not None
    items: list[ListItem] = []
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None or not line.strip():
            break
        m_unord = _UNORDERED_RE.match(line)
        m_ord = _ORDERED_RE.match(line)
        # Pick the match that fits the list's flavour. Each guard above
        # has already ruled out the cases where the chosen match is
        # None; restating the check here makes the type narrowing
        # visible to mypy.
        if ordered:
            if m_ord is None:
                break
            content = m_ord.group(2)
        else:
            if m_unord is None:
                break
            content = m_unord.group(2)
        item_start = cur.i
        cur.consume()
        items.append(
            ListItem(
                text=content,
                span=cur.span_of(item_start, cur.i),
            )
        )
    return List(
        ordered=ordered,
        items=items,
        span=cur.span_of(start, cur.i),
    )


def _consume_table(cur: _LineCursor) -> Table | None:
    """Eat a contiguous run of ``| ... |`` lines into a :class:`Table`.

    A separator line (``| --- | --- |``) between header and body is
    detected and dropped — V1 doesn't model header semantics, but
    skipping the separator avoids it landing in the output as a row
    of dashes.
    """
    start = cur.i
    rows: list[TableRow] = []
    consumed: list[str] = []
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None or not _TABLE_RE.match(line):
            break
        consumed.append(line)
        cur.consume()
    if len(consumed) < 1:
        return None
    for line in consumed:
        # Inside vertical bars: split on |, strip each. Trailing /
        # leading empties from the wrapping pipes are dropped.
        inner = line.strip()
        if inner.startswith("|"):
            inner = inner[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        parts = [p.strip() for p in inner.split("|")]
        if all(_TABLE_SEP_CHARS.match(p) for p in parts):
            # Header / body separator — skip.
            continue
        cells = [TableCell(text=p) for p in parts]
        rows.append(TableRow(cells=cells))
    if not rows:
        return None
    return Table(rows=rows, span=cur.span_of(start, cur.i))


def _consume_paragraph(cur: _LineCursor) -> Paragraph:
    """Eat lines until blank / next-block boundary; join with spaces.

    Paragraph break heuristic: stop on blank line, on a fenced code
    fence, on a heading line, on a quote line, or on a list item.
    Multi-line paragraphs join with ``" "`` (CommonMark soft-break);
    note that downstream braille rendering treats whitespace
    uniformly so this is largely cosmetic.

    The first line is consumed unconditionally: the caller routed
    us here because no other consumer claimed the line, so refusing
    to make progress here would loop forever (e.g. an unterminated
    ``$$ no close`` line that ``_consume_dollar_math`` rejected).
    """
    start = cur.i
    parts: list[str] = []
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None:
            break
        if not line.strip():
            break
        stripped = line.strip()
        if parts and (
            _HEADING_RE.match(stripped)
            or _UNORDERED_RE.match(line)
            or _ORDERED_RE.match(line)
            or _QUOTE_RE.match(stripped)
            or _FENCE_RE.match(stripped)
            or stripped.startswith(_DOLLAR_FENCE)
        ):
            break
        parts.append(line)
        cur.consume()
    body = " ".join(p.strip() for p in parts)
    return Paragraph(text=body, span=cur.span_of(start, cur.i))
