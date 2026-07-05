"""Intermediate representations: document, inline, math, braille, and
tactile IR.

The IR layer is the neutral home for the stable data contracts between
*adjacent* layers — not one contract but two kinds, and downstream code
depends on these types rather than on the layers that produce them:

* **Semantic IR** — what Frontend hands Backend once it has decided
  *what something is*: :class:`DocumentIR`, the inline token model, and
  the math / music trees (MathML / MusicXML themselves are the IR, kept
  as ``ET.Element``). Frontend fills them; Backend reads them.
* **Product IR** — what Backend hands the Renderer once it has decided
  *how to write it*, one step short of bytes: :class:`BrailleCell`
  sequences and :class:`~brailix.ir.tactile.TactileRaster`. These stay
  *intermediate*, not final: the Unicode / BRF / image bytes are the
  Renderer's derived output (a cell's glyph is recomputed from its
  ``dots`` at render time, never stored), and a cell keeps
  ``source_span`` / ``role`` so the form stays debuggable and
  round-trippable — which a finished product would not need.

Both kinds live in this neutral module for one reason: it keeps adjacent
layers independently replaceable. Backend and Renderer both import from
``brailix.ir`` and never from each other; the dividing line is the byte
stream — every intermediate representation before it belongs here, the
encoded bytes themselves live in ``brailix.renderer``.

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
    GraphicBlock,
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
    GraphicInline,
    HanziChar,
    HanziMarker,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    MusicInline,
    Number,
    Percent,
    PhoneticInline,
    Punct,
    Quantity,
    Segment,
    Space,
    Unknown,
    Word,
)

# Note: ``TactileRaster`` (the tactile Product IR named above) is deliberately
# NOT re-exported here — not because it is a "product" (``BrailleCell`` is a
# Product IR too, yet it *is* re-exported) but because this shallow top-level
# surface serves the braille main line: its consumers (proofreading / CLI
# front-ends) want the document, inline and braille types together. The
# independent graphics vertical's raster is imported straight from
# ``brailix.ir.tactile`` by the few callers that need it (the tactile
# renderers, the graphic editor). The graphics *document-model* node types
# (:class:`GraphicBlock`, :class:`GraphicInline`) are re-exported, like
# Math/Music, because they are first-class document IR citizens.

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
    "GraphicBlock",
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
    "GraphicInline",
    "HanziChar",
    "HanziMarker",
    "InlineNode",
    "LatinAcronym",
    "LatinWord",
    "MathInline",
    "MusicInline",
    "Number",
    "Percent",
    "PhoneticInline",
    "Punct",
    "Quantity",
    "Segment",
    "Space",
    "Unknown",
    "Word",
)
