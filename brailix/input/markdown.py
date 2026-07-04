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
* Fenced graphic: ``\`\`\`graphic`` (the SVG alias) or
  ``\`\`\`graphic-<source>`` carrying a graphics source name verbatim
  (``graphic-svg`` / ``graphic-figure`` / ``graphic-primitives`` /
  ``graphic-image`` / any registered adapter) ... ``\`\`\``` →
  :class:`GraphicBlock` — an inline tactile figure whose fence body is the
  figure source (SVG / data spec / image path). Lets a chapter carry its
  figures inline so the document stays one portable file
  (ARCHITECTURE.md G2). See :func:`graphic_fence_source` /
  :func:`graphic_fence_open`, the two directions of the fence grammar.
* ``$$display math$$`` → :class:`MathBlock` (``source="latex"``).
* ``![alt](target)`` alone on a line → :class:`ImageAlt` — the
  placeholder for an image that is **not** (yet) a tactile graphic: the
  alt text translates as ordinary prose, ``target`` names the image (a
  document-asset name like ``media/image1.png``, or a filesystem path)
  so a later, explicitly user-triggered conversion can turn the line
  into a ``graphic-image`` fence (ARCHITECTURE.md).
  This is *not* graphics syntax — a figure is always a fence; see
  :func:`image_alt_line`, the compose direction. An inline ``![...]``
  inside prose stays literal text (subset rule, same as inline
  formatting below).
* ``| col1 | col2 |`` lines on consecutive rows → :class:`Table`. A
  separator row (``| --- | --- |``) is recognised but doesn't change
  rendering — alignment is a future enhancement.
* Any other line → joined into a :class:`Paragraph` until a blank line
  ends it.

A heading or paragraph may carry a trailing ``{align=center}`` /
``{align=right}`` attribute (pandoc-style), which sets
:attr:`brailix.ir.document.Block.align` and is stripped from the text.
It lets a centred / right-aligned block survive a docx→markdown→re-parse
round-trip: an importer serialises a parsed Word document back to markdown
with this marker, this parser reads it back into ``align``, and the layout
renderer then centres / right-aligns the braille. Only ``center`` /
``right`` are recognised — the placements braille has a convention for.

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

from brailix.core.span import Span
from brailix.ir.document import (
    Block,
    CodeBlock,
    DocumentIR,
    GraphicBlock,
    Heading,
    ImageAlt,
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


def is_closing_fence(line: str) -> bool:
    """Whether ``line`` is a ``` ``` ``` fence delimiter (open or close).

    The single public authority on what counts as a fence line, so a tool that
    rewrites a fence's body can judge its closing line exactly as
    :func:`_consume_fenced_code` does — an unclosed fence (tolerated by the
    parser, span runs to EOF, last line is body not ``` ``` ```) is then not
    mistaken for a closed one.
    """
    return bool(_FENCE_RE.match(line.strip()))

# Fenced graphic block (ARCHITECTURE.md G2): a self-contained
# tactile figure embedded in braille source, mirroring the ```code fence and
# the ``$$math$$`` fence — the fence body IS the figure source, so the document
# stays portable (no external file references travelling beside the .blx).
#
# The info string carries the graphics source name **structurally**:
# ``graphic-<source>`` maps to ``<source>`` verbatim (``graphic-svg`` /
# ``graphic-figure`` / ``graphic-primitives`` / ``graphic-image`` / any
# future adapter), and the bare ``graphic`` tag is the friendly alias for
# SVG (the common inline case) — the same shape as inline math's
# dialect-tagged islands, where the tag *is* the source name. Namespaced
# under ``graphic`` so a figure fence can't be mistaken for an ordinary
# ```code fence. The input layer stays registry-free (ARCHITECTURE §7.3):
# a name no adapter answers to soft-fails at compile time as
# ``GRAPHICS_ADAPTER_MISSING`` plus a blank raster, so registering a new
# graphics source needs no input-layer change at all.
_GRAPHIC_FENCE_PREFIX = "graphic-"
_GRAPHIC_FENCE_DEFAULT_SOURCE = "svg"


def graphic_fence_source(info: str | None) -> str | None:
    """Graphics source name for a fence info string, or ``None`` when the
    fence is not a graphic fence (an ordinary ```code fence).

    The parse direction of the fence grammar: ``graphic`` → ``svg`` (the
    friendly alias), ``graphic-<source>`` → ``<source>`` verbatim. The
    name is not validated here — the graphics frontend owns the adapter
    registry and soft-fails an unknown name at compile time."""
    if not info:
        return None
    if info == "graphic":
        return _GRAPHIC_FENCE_DEFAULT_SOURCE
    if info.startswith(_GRAPHIC_FENCE_PREFIX):
        return info[len(_GRAPHIC_FENCE_PREFIX) :] or None
    return None


def graphic_fence_open(source: str) -> str:
    """The opening fence line for a graphics ``source`` name — the compose
    direction of the fence grammar, inverse of :func:`graphic_fence_source`.

    Always the explicit ``\\`\\`\\`graphic-<source>`` form (unambiguous on
    round-trip; the bare ``graphic`` alias is accepted on parse only). An
    editor writing or re-tagging a figure fence uses this instead of
    spelling the tag itself, so the grammar has one owner."""
    return f"```{_GRAPHIC_FENCE_PREFIX}{source}"


# A whole line that is exactly one markdown image: ``![alt](target)``.
# The block form only — inline images inside prose are not part of the
# subset (they stay literal text). ``alt`` excludes ``]`` (no escape
# support); ``target`` is greedy so a path containing ``)`` still ends at
# the line's final paren, and may be empty (an alt-only placeholder whose
# image bytes were unrecoverable — round-trips as ``![alt]()``).
_IMAGE_RE = re.compile(r"^!\[([^\]]*)\]\((.*)\)$")


def image_alt_line(alt: str, target: str | None) -> str:
    """The ``![alt](target)`` line for an image placeholder — the compose
    direction of the image grammar, inverse of the ``_IMAGE_RE`` parse.

    A serialiser (the Word importer, the figure-conversion tool) uses this
    instead of spelling the syntax itself, so the grammar has one owner —
    the same single-ownership rule as :func:`graphic_fence_open`. ``alt``
    is flattened to the subset the parse direction can read back:
    newlines collapse to spaces and ``]`` (unescapable in this grammar)
    becomes ``)``. ``None`` / empty ``target`` emits ``![alt]()``, the
    alt-only placeholder."""
    safe_alt = " ".join((alt or "").split()).replace("]", ")")
    return f"![{safe_alt}]({target or ''})"


_TABLE_RE = re.compile(r"^\s*\|(.+)\|\s*$")
_TABLE_SEP_CHARS = re.compile(r"^[\s\-|:]+$")

# A trailing block-attribute marker carrying horizontal alignment, e.g.
# ``# 标题 {align=center}`` or ``正文 {align=right}`` (pandoc-style attribute
# syntax). It feeds :attr:`brailix.ir.document.Block.align`, mirroring the
# docx adapter's ``w:jc`` capture so a centred / right-aligned block survives
# a docx→markdown→re-parse round-trip (an importer serialises a parsed Word
# document back to markdown, then this parser rebuilds the IR). Only
# ``center`` / ``right`` — the alignments the braille layout renders;
# anything else stays literal text and yields no alignment.
_ALIGN_ATTR_RE = re.compile(r"\s*\{align=(center|right)\}\s*$", re.IGNORECASE)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def parse_markdown(
    text: str,
    *,
    language: str,
    profile: str,
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
            heading_text, align = _extract_align(m_h.group(2))
            yield Heading(
                level=level,
                text=heading_text,
                align=align,
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

        # Image placeholder — a whole line of ``![alt](target)``.
        m_img = _IMAGE_RE.match(stripped)
        if m_img:
            start = cur.i
            cur.consume()
            target = m_img.group(2).strip()
            yield ImageAlt(
                text=m_img.group(1).strip(),
                target=target or None,
                span=cur.span_of(start, start + 1),
            )
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


def safe_block_insertion_offset(source: str, pos: int) -> int:
    """Clamp ``pos`` to the nearest offset where a new top-level block can be
    inserted without splitting an existing block.

    ``pos`` is first clamped to ``0..len(source)``. If it then lands strictly
    *inside* a parsed block's span (a fenced graphic / code block, a table, a
    ``$$`` math block, a paragraph, …), it is moved to that block's ``span.end``
    — just past the block — so an inserted fence can never bisect an atomic
    block. Inserting into a ```graphic``` fence body otherwise makes the new
    fence's opening line read as the *closing* line of the enclosing fence,
    tearing the old figure apart. A ``pos`` already on a block boundary or in
    inter-block whitespace is returned unchanged.

    Pure structural query (format-independent — reads only the block spans),
    so it belongs with the markdown parser; an editor calls it before splicing
    a figure fence at the caret.
    """
    pos = max(0, min(pos, len(source)))
    for block in _iter_blocks(source):
        span = block.span
        if span is not None and span.start < pos < span.end:
            return span.end
    return pos


# ---------------------------------------------------------------------------
# Per-block consumers
# ---------------------------------------------------------------------------


def _consume_fenced_code(
    cur: _LineCursor, language: str | None
) -> CodeBlock | GraphicBlock:
    r"""Eat the opening ``\`\`\``` fence, content lines, and the closing
    fence (if present). Missing closing fence is tolerated — we stop
    at EOF so a runaway fence doesn't swallow the rest of the doc.

    A fence whose info string names a graphic format (```graphic /
    ```graphic-figure / ...) yields a :class:`GraphicBlock` instead of a
    :class:`CodeBlock` — an inline tactile figure whose body is the figure
    source (ARCHITECTURE.md G2). Any other info string is an
    ordinary code fence.
    """
    start = cur.i
    cur.consume()  # opening fence
    body: list[str] = []
    while cur.i < len(cur.lines):
        line = cur.peek()
        if line is None:
            break
        if is_closing_fence(line):
            cur.consume()  # closing fence
            break
        body.append(line)
        cur.consume()
    span = cur.span_of(start, cur.i)
    text = "\n".join(body)
    graphic_format = graphic_fence_source(language)
    if graphic_format is not None:
        return GraphicBlock(source=graphic_format, text=text, span=span)
    return CodeBlock(language=language, text=text, span=span)


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


def _extract_align(text: str) -> tuple[str, str | None]:
    """Split a trailing ``{align=center|right}`` attribute off ``text``.

    Returns ``(text_without_attr, align)``. ``align`` is ``None`` when no
    recognised marker is present, leaving ``text`` untouched — so ordinary
    prose that happens to mention braces is unaffected (only the exact
    ``{align=center}`` / ``{align=right}`` tail is consumed).
    """
    m = _ALIGN_ATTR_RE.search(text)
    if m is None:
        return text, None
    return text[: m.start()].rstrip(), m.group(1).lower()


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

    A separator line (``| --- | --- |``) is dropped when it sits where a
    GFM separator can — the header/body boundary at row index 1, or a
    leading row 0 (a lone separator then yields no body rows and falls
    back to a paragraph, as before). V1 doesn't model header semantics;
    dropping the separator just keeps it from landing in the output as a
    row of dashes. A *later* body row (index 2+) that happens to be all
    dashes — placeholder ``-`` cells — is kept as data instead.

    Cursor contract: this scans ahead without consuming and only
    advances the cursor when it returns a real :class:`Table`. A run
    that yields no body rows (e.g. a lone ``| --- |`` separator) returns
    None with the cursor untouched, so the caller re-routes those lines
    to :func:`_consume_paragraph`. Consuming first and then returning
    None stranded the cursor past EOF and crashed ``span_of`` on the
    out-of-range line index.
    """
    start = cur.i
    rows: list[TableRow] = []
    consumed: list[str] = []
    end = cur.i
    while end < len(cur.lines):
        line = cur.lines[end]
        if not _TABLE_RE.match(line):
            break
        consumed.append(line)
        end += 1
    if len(consumed) < 1:
        return None
    for idx, line in enumerate(consumed):
        # Inside vertical bars: split on |, strip each. Trailing /
        # leading empties from the wrapping pipes are dropped.
        inner = line.strip()
        if inner.startswith("|"):
            inner = inner[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        parts = [p.strip() for p in inner.split("|")]
        # A GFM separator only sits at the header/body boundary (row 1) or
        # as a leading row 0; drop an all-sep row only there, so a later
        # all-dashes body row (placeholder ``-`` cells) survives as data.
        if idx <= 1 and all(_TABLE_SEP_CHARS.match(p) for p in parts):
            continue
        cells = [TableCell(text=p) for p in parts]
        rows.append(TableRow(cells=cells))
    if not rows:
        return None
    cur.i = end  # commit the consumption now that it is a real table
    return Table(rows=rows, span=cur.span_of(start, cur.i))


def _starts_block(line: str, stripped: str) -> bool:
    """True if ``line`` opens a non-paragraph block, used to end a paragraph
    at the next block boundary.

    Mirrors the prefix dispatch in :func:`_iter_blocks` (heading / list /
    quote / fenced code / image placeholder / ``$$`` display math). List
    markers must match the raw ``line`` (they sit at column 0); the rest
    match the ``stripped`` form. Kept as one predicate so the paragraph
    terminator and the block dispatcher can't fall out of sync.

    Table is deliberately *not* here even though :func:`_iter_blocks`
    recognises one: a lone ``|`` in prose (``Ctrl|Alt``) must not end a
    paragraph. The cost is that a table directly after a paragraph line,
    with no blank line between, gets absorbed into the paragraph — a blank
    line before the table is required to get a :class:`Table` block.
    """
    return bool(
        _HEADING_RE.match(stripped)
        or _UNORDERED_RE.match(line)
        or _ORDERED_RE.match(line)
        or _QUOTE_RE.match(stripped)
        or _FENCE_RE.match(stripped)
        or _IMAGE_RE.match(stripped)
        or stripped.startswith(_DOLLAR_FENCE)
    )


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
        if parts and _starts_block(line, stripped):
            break
        parts.append(line)
        cur.consume()
    body = " ".join(p.strip() for p in parts)
    body, align = _extract_align(body)
    return Paragraph(text=body, align=align, span=cur.span_of(start, cur.i))
