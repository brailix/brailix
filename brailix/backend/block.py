"""Block-level translation: turn one :class:`Block` into one **or
more** :class:`BrailleBlock`\\ s.

The inline dispatcher in :mod:`brailix.backend.dispatch` knows how
to translate :class:`InlineNode`\\ s; this module sits one layer up,
handling block kinds that need either:

* a **prefix marker** (list items get bullets / numbers, footnotes a
  reference indicator), or
* **expansion into multiple BrailleBlocks** so the layout pass can
  treat each line independently (lists become one
  ``block_type="list_item"`` block per item; tables become one
  ``block_type="table_row"`` block per row).

The contract: :func:`expand_block` always returns ``list[BrailleBlock]``.
Simple blocks (paragraph / heading / quote / code_block / math_block /
footnote / image_alt) return a one-element list. Composite blocks
(List, Table) return multiple elements. The renderer / layout pass
sees the expanded form and never has to look inside ``children``
again.

This module is **purely backend** — it never reaches back into the
Frontend. MathBlock and CodeBlock children are pre-populated by
:meth:`brailix.pipeline.FrontendDriver.populate_block` (MathBlock children
become :class:`MathInline` nodes with parsed MathML; CodeBlock
children become :class:`CodeInline`). expand_block then dispatches
them like any other inline children.
"""

from __future__ import annotations

from dataclasses import replace

from brailix.backend import number as number_backend
from brailix.backend.dispatch import translate_node
from brailix.backend.latin import english_run_role
from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import (
    BrailleBlock,
    BrailleCell,
    BrailleDocument,
    blank_cell,
)
from brailix.ir.document import (
    Block,
    DocumentIR,
    Footnote,
    GraphicBlock,
    ImageAlt,
    List,
    Table,
)
from brailix.ir.inline import InlineNode, Number

# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def translate_block(
    block: Block, ctx: BackendContext, profile: BrailleProfile
) -> BrailleBlock:
    """Translate one DocumentIR block into a single :class:`BrailleBlock`.

    Kept for backward compatibility with callers that don't want to
    deal with composite expansion (Lists, Tables). For those cases
    this returns the **first** expanded block — for a List that means
    only the first ``list_item`` survives. New callers should use
    :func:`expand_block` or :func:`translate_document`, which handle
    composites correctly.
    """
    block_ctx = BackendContext(
        profile=ctx.profile,
        mode=ctx.mode,
        block_type=block.type,
        warnings=ctx.warnings,
        options=dict(ctx.options),
    )
    expanded = expand_block(block, block_ctx, profile)
    return expanded[0] if expanded else BrailleBlock(block_type=block.type)


def translate_document(
    doc: DocumentIR, ctx: BackendContext, profile: BrailleProfile
) -> BrailleDocument:
    """Translate every block in ``doc``, expanding composite containers
    (List, Table) into multiple :class:`BrailleBlock`\\ s.
    """
    blocks: list[BrailleBlock] = []
    for block in doc.blocks:
        block_ctx = BackendContext(
            profile=ctx.profile,
            mode=ctx.mode,
            block_type=block.type,
            warnings=ctx.warnings,
            options=dict(ctx.options),
        )
        blocks.extend(expand_block(block, block_ctx, profile))
    return BrailleDocument(
        metadata={**doc.metadata, "profile": profile.name},
        blocks=blocks,
    )


def expand_block(
    block: Block, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleBlock]:
    """Return one or more :class:`BrailleBlock`\\ s for ``block``.

    Composite containers (List, Table) expand into multiple blocks so
    the layout pass can apply per-row / per-item indent rules without
    re-walking the source IR.
    """
    if isinstance(block, List):
        return _expand_list(block, ctx, profile)
    if isinstance(block, Table):
        return _expand_table(block, ctx, profile)
    if isinstance(block, GraphicBlock):
        # A tactile figure carries no braille cells — it rasterises to a
        # TactileRaster (attached to the CompiledBlock by
        # Pipeline.translate_block, ARCHITECTURE.md G1).  Emit an
        # empty "graphic" block so the figure holds its place in the block
        # flow WITHOUT translating its GraphicInline child, which has no
        # braille cells and would otherwise warn UNHANDLED_NODE_TYPE.
        return [
            BrailleBlock(
                block_type=block.type, id=block.id, align=block.align
            )
        ]
    if isinstance(block, ImageAlt):
        # An image that hasn't been turned into a tactile graphic. Its alt
        # text still translates as prose (the simple path below), but the
        # picture itself is absent from the braille — flag it so the user can
        # decide, image by image, whether to convert it into a graphic-image
        # fence (ARCHITECTURE.md). The warnings panel is the
        # reader's running list of not-yet-converted images; ignoring one is
        # the ordinary "ignore warning" action. Fall through (no return) to
        # translate the alt text.
        ctx.warnings.warn(
            code="IMAGE_NOT_CONVERTED",
            message=(
                "image not converted to a tactile graphic: "
                + (block.target or block.text or "(untitled)")
            ),
            surface=block.text or block.target or None,
            span=block.span,
            source="backend.block",
        )
    # All other block kinds (Paragraph, Heading, Quote, MathBlock,
    # CodeBlock, Footnote, ImageAlt) flow through the simple path —
    # translate inline children and stamp the block type. Pipeline
    # is responsible for populating children before we get here.
    # Footnote optionally gets a reference marker prepended.
    cells: list[BrailleCell] = []
    if isinstance(block, Footnote) and block.ref:
        cells.extend(_footnote_ref_cells(block.ref, block.span, profile))
    cells.extend(_translate_children(block.children, ctx, profile))
    return [
        BrailleBlock(
            block_type=block.type,
            id=block.id,
            heading_level=getattr(block, "level", None),
            align=block.align,
            cells=cells,
        )
    ]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _translate_children(
    children: list[InlineNode],
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    """Run the inline dispatcher over each child and concatenate.

    Each dispatch sees the immediately-following sibling stashed under
    ``ctx.options['_next_inline_sibling']`` so backends can peek
    across IR-node boundaries (the zh backend uses this for NCB's
    cross-syllable boundary rule).

    It also threads a running-English flag under
    ``ctx.options['_english_run_active']``: the flag is on whenever an
    earlier sibling already opened an English stretch that the current
    node hasn't broken, letting :func:`brailix.backend.latin.translate_latin`
    drop the redundant lowercase sign on a following lowercase word. The
    on/off transitions come from :func:`english_run_role` so the type
    knowledge stays in the Latin backend, not here. Both keys are cleared
    after the loop so they don't leak to unrelated callers that share the
    context. The flag starts fresh per call, so an English run never
    spans block / list-item / table-cell boundaries.
    """
    out: list[BrailleCell] = []
    english_active = False
    for i, child in enumerate(children):
        ctx.options["_next_inline_sibling"] = (
            children[i + 1] if i + 1 < len(children) else None
        )
        ctx.options["_english_run_active"] = english_active
        out.extend(translate_node(child, ctx, profile))
        role = english_run_role(child)
        if role == "letter":
            english_active = True
        elif role == "break":
            english_active = False
        # "carry" (space / punct / digits) leaves the flag unchanged.
    ctx.options.pop("_next_inline_sibling", None)
    ctx.options.pop("_english_run_active", None)
    return out


# ---- List -----------------------------------------------------------------


def _expand_list(
    block: List, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleBlock]:
    """One :class:`BrailleBlock` per :class:`ListItem`, marker prepended."""
    blocks: list[BrailleBlock] = []
    for i, item in enumerate(block.items, start=1):
        cells: list[BrailleCell] = []
        cells.extend(_list_marker_cells(i, block.ordered, ctx, profile))
        cells.extend(_translate_children(item.children, ctx, profile))
        blocks.append(
            BrailleBlock(
                block_type="list_item",
                id=item.id,
                cells=cells,
            )
        )
    return blocks


def _list_marker_cells(
    index: int,
    ordered: bool,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    """Produce the marker cells for a list item.

    The marker character (``profile.list_marker_ordered_char()`` for
    ordered, ``profile.list_marker_unordered_char()`` for unordered —
    BANA defaults ``.`` / ``·``) is looked up in the same profile
    punctuation table that drives plain-text :class:`Punct` rendering
    — including its ``space_before`` / ``space_after`` flags — so the
    braille output for a list item is indistinguishable from the
    equivalent plain text "1. foo" / "· foo". Lists have no special
    spacing logic beyond what the punct table declares for the marker
    char.

    Every marker cell — ordinal digits, dot, bullet, spacing blanks —
    is synthesised print structure, not a character of ``item.text``,
    so the whole run anchors to the item text's LEADING EDGE in
    leaf-local coordinates: ``Span(0, 0)``, the same zero-width-anchor
    convention as the number sign. (It previously inherited spans
    derived from the item's block-level span, mixing document
    coordinates into an otherwise leaf-local cell sequence — an ordinal
    digit then claimed item-text characters it never came from.)
    """
    cells: list[BrailleCell] = []
    if ordered:
        sub_ctx = _block_ctx(ctx, block_type="list_item")
        digits_node = Number(surface=str(index), span=None)
        cells.extend(
            number_backend.translate_number(digits_node, sub_ctx, profile)
        )
        cells.extend(
            _marker_punct_cells(
                profile.list_marker_ordered_char(), None, profile
            )
        )
    else:
        cells.extend(
            _marker_punct_cells(
                profile.list_marker_unordered_char(), None, profile
            )
        )
        # No profile bullet → silently fall through; the layout still
        # produces a usable line with just the content.
    edge = Span(0, 0)
    return [replace(c, source_span=edge) for c in cells]


def _marker_punct_cells(
    ch: str, span: Span | None, profile: BrailleProfile
) -> list[BrailleCell]:
    """Render ``ch`` as a list marker with the punct table's own
    cells + spacing flags (role=``list_marker`` instead of ``punct``).

    Mirrors :func:`brailix.backend.punct.translate_punct` but stamps the
    cells with the marker role so proofread tools can tell a structural
    marker apart from a literal punctuation char in the source.
    Returns ``[]`` when ``ch`` is not in the table.
    """
    punct_cells = profile.punctuation.get(ch)
    if not punct_cells:
        return []
    # The bullet / number is print structure, not a literal char inside the
    # item text; the caller (``_list_marker_cells``) re-anchors every marker
    # cell to the leaf-local leading edge, so the span here is a placeholder.
    edge = Span(span.start, span.start) if span else None
    out: list[BrailleCell] = [
        BrailleCell(dots=dots, role="list_marker", source_span=edge, source_text=ch)
        for dots in punct_cells
    ]
    space_before, space_after = profile.punctuation_spaces(ch)
    if space_before:
        out.insert(0, blank_cell(edge))
    if space_after:
        out.append(blank_cell(edge))
    return out


# ---- Table ----------------------------------------------------------------


def _expand_table(
    block: Table, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleBlock]:
    """One :class:`BrailleBlock` per :class:`TableRow`.

    Within a row the rendered cell content is separated by ``"  "``
    (two blank cells) so columns are visibly distinct. We don't try
    to align columns to a fixed width — that's a layout concern and
    requires inspecting all rows; V1 accepts the ragged-right look.
    """
    blocks: list[BrailleBlock] = []
    for row in block.rows:
        cells: list[BrailleCell] = []
        for j, table_cell in enumerate(row.cells):
            if j > 0:
                # Column separator: trace to the column's leading edge.
                edge = (
                    Span(table_cell.span.start, table_cell.span.start)
                    if table_cell.span
                    else None
                )
                cells.append(blank_cell(edge))
                cells.append(blank_cell(edge))
            cells.extend(_translate_children(table_cell.children, ctx, profile))
        blocks.append(
            BrailleBlock(
                block_type="table_row",
                id=row.id,
                cells=cells,
            )
        )
    return blocks


# ---- Footnote -------------------------------------------------------------


def _footnote_ref_cells(
    ref: str, span: Span | None, profile: BrailleProfile
) -> list[BrailleCell]:
    """Render a footnote ref (``"1"``, ``"a"``, ``"*"``) as a marker.

    V1 just spells the ref characters out via the profile's punct /
    letter tables and follows them with a blank cell so the body text
    has clear separation. Unknown chars produce an unknown cell.
    """
    if not ref:
        return []
    base = span.start if span else 0

    def sp(i: int) -> Span | None:
        # Each ref char traces to its own position in the footnote's span.
        return Span(base + i, base + i + 1) if span else None

    cells: list[BrailleCell] = []
    # Track whether the previous emitted cell was part of a digit run so a
    # number sign is re-emitted whenever digits resume after a letter /
    # punctuation (a ref like ``1a2`` must not read its trailing ``2`` as a
    # letter); scanning "any number_sign already in cells" deduped too broadly.
    prev_was_digit = False
    for i, ch in enumerate(ref):
        letter = profile.letter(ch)
        if letter is not None:
            # Use the letter-sign-prefixed form, not the bare cell: in
            # cn_current bare_letter("a") == the digit "1" cell, so a ref
            # like "1a" kept the number latch and read "a" as another "1"
            # ("11"). The letter prefix (⠰ / ⠠ …) both disambiguates the
            # letter from a digit and breaks the number run. (The prefix
            # repeats per letter here; footnote refs are short — sharing
            # one sign across a multi-letter run is a later refinement.)
            cells.extend(
                BrailleCell(
                    dots=dots, role="footnote_ref", source_span=sp(i), source_text=ch
                )
                for dots in letter
            )
            prev_was_digit = False
            continue
        punct = profile.punctuation.get(ch)
        if punct:
            cells.extend(
                BrailleCell(
                    dots=dots, role="footnote_ref", source_span=sp(i), source_text=ch
                )
                for dots in punct
            )
            prev_was_digit = False
            continue
        digit = profile.digits.get(ch)
        if digit is not None:
            # Number-sign prefix at the start of each digit run — a digit
            # resuming after a letter / punct switches back into "number"
            # mode and needs the sign again.
            if profile.number_sign and not prev_was_digit:
                cells.append(
                    BrailleCell(
                        dots=profile.number_sign,
                        role="number_sign",
                        source_span=sp(i),
                    )
                )
            cells.append(
                BrailleCell(
                    dots=digit, role="footnote_ref", source_span=sp(i), source_text=ch
                )
            )
            prev_was_digit = True
            continue
        cells.append(
            BrailleCell(dots=(), role="unknown", source_span=sp(i), source_text=ch)
        )
        prev_was_digit = False
    # Trailing separator blank — trace to the ref's trailing edge.
    cells.append(
        blank_cell(Span(base + len(ref), base + len(ref)) if span else None)
    )
    return cells


# ---- BackendContext helper ------------------------------------------------


def _block_ctx(ctx: BackendContext, *, block_type: str) -> BackendContext:
    """Return a context tagged with the right ``block_type``.

    Used so list-marker translation runs see they're inside a list
    item, not a paragraph — important for any future block-aware
    formatting rules. The collector and other state are shared.
    """
    return BackendContext(
        profile=ctx.profile,
        mode=ctx.mode,
        block_type=block_type,
        warnings=ctx.warnings,
        options=dict(ctx.options),
    )
