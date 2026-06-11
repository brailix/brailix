"""Intermediate representations: document, inline, math, and braille IR.

The IR layer is the stable contract between Frontend (which decides
*what something is*) and Backend (which decides *how to write it as
braille*). Concrete adapter libraries depend on the IR types, not on
each other.

This package ``__init__`` re-exports the IR data model as the stable,
**shallow** public surface. Downstream consumers (a proofreading
front-end, CLI front-ends, ...) import from ``brailix.ir`` rather than the
concrete modules (``brailix.ir.document`` / ``.inline`` / ``.braille``)
so the library can reorganise those modules without breaking callers.
"""

from __future__ import annotations

from brailix.ir.braille import (
    BLANK_CELL,
    HANG_CLOSE_CELL,
    HANG_OPEN_CELL,
    LINE_BREAK_CELL,
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    BrailleSequence,
)
from brailix.ir.document import (
    Block,
    CodeBlock,
    DocumentIR,
    Footnote,
    Heading,
    ImageAlt,
    List,
    ListItem,
    MathBlock,
    MusicBlock,
    Paragraph,
    Quote,
    ScoreBlock,
    Table,
    TableCell,
    TableRow,
)
from brailix.ir.inline import (
    ChineseToken,
    CodeInline,
    Connector,
    Date,
    HanziChar,
    HanziMarker,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    MusicInline,
    Number,
    Percent,
    Punct,
    Quantity,
    Segment,
    Space,
    Unknown,
    Word,
)

__all__ = (
    # braille
    "BLANK_CELL",
    "HANG_CLOSE_CELL",
    "HANG_OPEN_CELL",
    "LINE_BREAK_CELL",
    "BrailleBlock",
    "BrailleCell",
    "BrailleDocument",
    "BrailleSequence",
    # document (block-level)
    "Block",
    "CodeBlock",
    "DocumentIR",
    "Footnote",
    "Heading",
    "ImageAlt",
    "List",
    "ListItem",
    "MathBlock",
    "MusicBlock",
    "Paragraph",
    "Quote",
    "ScoreBlock",
    "Table",
    "TableCell",
    "TableRow",
    # inline
    "ChineseToken",
    "CodeInline",
    "Connector",
    "Date",
    "HanziChar",
    "HanziMarker",
    "InlineNode",
    "LatinAcronym",
    "LatinWord",
    "MathInline",
    "MusicInline",
    "Number",
    "Percent",
    "Punct",
    "Quantity",
    "Segment",
    "Space",
    "Unknown",
    "Word",
)
