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

# What this facade holds is the **data model**: the block / inline / braille
# types a consumer builds, walks and serialises. Two deliberate exclusions,
# both on that same criterion rather than on "is it a product":
#
# * The layout control sentinels (``LINE_BREAK_CELL``, ``HANG_OPEN_CELL`` /
#   ``HANG_CLOSE_CELL``, ``CASES_OPEN_CELL`` / ``CASES_CLOSE_CELL``) are the
#   backend→renderer wire protocol for a matrix row break / hanging indent,
#   not part of the data model, and a renderer identifies them by ``role``
#   (``"line_break"``, ``"hang_open"``, ...) rather than by identity — see
#   ``brailix.backend.math.utils``, which spells out "judge by role". They
#   stay in ``brailix.ir.braille`` where the backend, the renderers and their
#   tests already import them. ``BLANK_CELL`` does stay here: an empty cell is
#   a value a consumer legitimately builds and compares against.
# * ``TactileRaster`` (the tactile Product IR) belongs to the independent
#   graphics vertical, whose consumers (the tactile renderers, the graphic
#   editor) import it straight from ``brailix.ir.tactile``; this shallow
#   surface serves the braille main line, whose consumers want the document,
#   inline and braille types together. The graphics *document-model* node
#   types (:class:`GraphicBlock`, :class:`GraphicInline`) ARE re-exported,
#   like Math / Music, because they are first-class document IR citizens.

__all__ = (
    # braille
    "BLANK_CELL",
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
