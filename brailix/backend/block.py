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
sees the expanded form and never has to look inside a block
again.

This module is **purely backend** — it never reaches back into the
Frontend. What a block carries by the time it arrives is
:meth:`brailix.pipeline.frontend_driver.FrontendDriver.populate_block`'s doing:
a code block's verbatim text as one :class:`CodeInline`, an embedded block's
parsed domain tree on the block itself. :func:`expand_block` dispatches
inline nodes through :func:`~brailix.backend.dispatch.translate_node` and a domain
tree through :func:`~brailix.backend.dispatch.translate_embedded`.
"""

from __future__ import annotations

from dataclasses import replace as _replace

from brailix.backend import number as number_backend
from brailix.backend.dispatch import translate_embedded, translate_node
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
    EmbeddedBlock,
    Footnote,
    ImageAlt,
    List,
    Table,
)
from brailix.ir.inline import InlineNode, Number

# ---------------------------------------------------------------------------
# Module entry point
# ---------------------------------------------------------------------------


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
    if isinstance(block, EmbeddedBlock):
        # Math, music and graphics: the content is the parsed tree, not
        # inlines, so the tree goes to its domain's translator rather than
        # through the inline dispatcher. (A figure's translator returns no
        # cells at all — its dots ride on the raster attached to the compiled
        # block; see ``dispatch._no_cells``.)
        return [
            BrailleBlock(
                block_type=block.type,
                id=block.id,
                align=block.align,
                cells=translate_embedded(block, ctx, profile),
            )
        ]
    if isinstance(block, ImageAlt):
        # An image that hasn't been turned into a tactile graphic. Its alt
        # text still translates as prose (the simple path below), but the
        # picture itself is absent from the braille — flag it so the user can
        # decide, image by image, whether to convert it into a graphic-image
        # fence.
        # The warnings panel is the
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
    # translate the block's inline nodes and stamp the block type.
    # Pipeline is responsible for populating them before we get here.
    # Footnote optionally gets a reference marker prepended.
    cells: list[BrailleCell] = []
    if isinstance(block, Footnote) and block.ref:
        cells.extend(_footnote_ref_cells(block.ref, profile))
    cells.extend(_translate_inlines(block.inlines, ctx, profile))
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


def _translate_inlines(
    children: list[InlineNode],
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    """Run the inline dispatcher over each node and concatenate.

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
    try:
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
    finally:
        # Clear in a ``finally`` so a mid-loop dispatch failure can't leave
        # these traversal keys behind on a shared ``ctx.options`` for an
        # unrelated caller to trip over.
        ctx.options.pop("_next_inline_sibling", None)
        ctx.options.pop("_english_run_active", None)
    return _drop_separator_before_attached_punct(
        _collapse_adjacent_blanks(out), profile
    )


def _is_typed_blank(cell: BrailleCell) -> bool:
    """Whether a blank cell stands for a space character in the source.

    The provenance convention every synthesised separator follows — the
    frontend's word boundaries, the punctuation table's auto-spaces, a date's
    component gap: no ``source_text`` and a zero-width span at the boundary it
    was emitted for. A space the author typed carries the character and the
    span it occupies.

    Both spacing passes below turn on this distinction, and for the same
    reason: a synthesised separator is one rule's opinion about a boundary and
    another rule may overrule it, whereas a typed space is content and neither
    pass is allowed to edit the source.
    """
    span = cell.source_span
    return bool(cell.source_text) or (span is not None and not span.is_empty())


def _collapse_adjacent_blanks(cells: list[BrailleCell]) -> list[BrailleCell]:
    """Merge a run of consecutive word-separator blanks into one.

    A blank between two words is a *separator*, and a separator repeated is
    still one separator — but nothing upstream is in a position to know that.
    The spacing a document ends up with is decided independently in several
    places: the punctuation table's ``space_before`` / ``space_after`` for a
    mark, the boundary pass for a hanzi↔letter or composite↔hanzi seam, the
    source's own typed space. Each is right about its own rule and none can
    see the others, so two of them agreeing on "put a blank here" writes two
    blanks — which reads as a word gap where there is none.

    Collapsing here is what lets those rules be stated *generously*: a
    profile can declare the orthographic spacing a mark takes without first
    proving no other rule already supplies it.

    Judged by **role**, never by ``dots == ()``. The layout control sentinels
    (``line_break`` / ``hang_open`` / ``hang_close`` / ``cases_*``) and an
    unknown placeholder are dots-empty too, and they are backend→renderer wire
    protocol, not spacing — merging them would silently drop a matrix row
    break. The same rule
    :func:`brailix.backend.math.utils._last_is_blank` states for the math
    emitters.

    Deliberately **not** trimming a leading or trailing blank. A blank at the
    edge of a block is not obviously redundant: for a degenerate formula
    (``$$``, ``$ $``) the blank *is* the whole output, and an abbreviation's
    own ``space_after`` puts one at the end of ``i.e.`` — thirteen golden
    cases turn on that. Whether an edge blank should survive is an
    orthographic decision per case, not a de-duplication.

    And only a **synthesised** separator is ever dropped. A blank the author
    typed is content: ``选项是(   )`` — the fill-in blank of a multiple-choice
    item — is three spaces wide because the writer made it three wide, and
    merging them rewrites the question. The two are told apart by provenance,
    the convention the frontend already follows: a synthesised separator
    carries ``surface=""`` and a zero-width span at the boundary, a typed
    space carries the character and the span it occupies.

    So within a run of adjacent blanks: if any came from the source, those are
    what survive and the synthesised ones beside them go; if the run is all
    synthesised, one survives — the **first**, so the separator keeps the
    coordinate of the boundary it was emitted for and a proofreader clicking
    it lands where the rule pointed.
    """
    if len(cells) < 2:
        return cells
    out: list[BrailleCell] = []
    i = 0
    while i < len(cells):
        if cells[i].role != "space":
            out.append(cells[i])
            i += 1
            continue
        j = i
        while j < len(cells) and cells[j].role == "space":
            j += 1
        run = cells[i:j]
        typed = [c for c in run if _is_typed_blank(c)]
        out.extend(typed if typed else run[:1])
        i = j
    return out


def _drop_separator_before_attached_punct(
    cells: list[BrailleCell], profile: BrailleProfile
) -> list[BrailleCell]:
    """Drop a synthesised separator standing in front of a punctuation mark
    that is written against the word before it.

    The companion to :func:`_collapse_adjacent_blanks`, and the other half of
    what lets the punctuation table state its spacing generously. That pass
    settles two rules that *agree*; this one settles a rule against the table
    entry of whatever comes next. ``space_after`` says "a word ends here",
    which is true of ``50%`` and of ``i.e.`` — but when the next thing is not
    a word, it is a mark belonging to the word just written and there is no
    gap: ``50%，`` is ⠨⠐, ``（注）。`` is ⠠⠆⠐⠆.

    A mark's entry declares the spacing on **both** its sides, so a missing
    ``space_before`` is not silence. In a table whose own note says there is
    no blanket "add one space" default, it is the statement that this mark is
    written attached — which makes it a veto over a separator some other rule
    supplied, not merely a request declined. Every closing and every sentence
    mark in the shipped tables is in that class; the marks that open something
    (``（`` ``“`` ``《``) carry ``space_before`` and keep the blank.

    Only a **synthesised** separator is dropped, the same line
    :func:`_collapse_adjacent_blanks` draws: the space in ``50% ，`` is one the
    author typed, and neither pass edits the source. And only a ``punct`` cell
    can veto — the dots-empty layout sentinels and the unknown placeholder are
    not punctuation, and the marker cells (list bullet, footnote ref) are
    print structure that :func:`expand_block` places outside this pass
    entirely, along with the table column separators.
    """
    if len(cells) < 2:
        return cells
    out: list[BrailleCell] = []
    for i, cell in enumerate(cells):
        if (
            cell.role == "space"
            and not _is_typed_blank(cell)
            and i + 1 < len(cells)
            and _punct_attaches_left(cells[i + 1], profile)
        ):
            continue
        out.append(cell)
    return out


def _punct_attaches_left(cell: BrailleCell, profile: BrailleProfile) -> bool:
    """Whether ``cell`` is a punctuation mark whose table entry asks for no
    blank in front of it — one written against the preceding word.

    Looked up by the cell's own ``source_text``, which for a punctuation cell
    is the mark as the table keys it (including the two-char ``——``), so a
    multi-cell mark answers the same whichever of its cells is asked. A cell
    with no ``source_text`` names no table entry and so vetoes nothing —
    declining to drop a blank is the answer that changes nothing.
    """
    if cell.role != "punct" or not cell.source_text:
        return False
    space_before, _ = profile.punctuation_spaces(cell.source_text)
    return not space_before


# ---- List -----------------------------------------------------------------


def _expand_list(
    block: List, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleBlock]:
    """One :class:`BrailleBlock` per :class:`ListItem`, marker prepended."""
    blocks: list[BrailleBlock] = []
    for i, item in enumerate(block.blocks, start=1):
        cells: list[BrailleCell] = []
        cells.extend(_list_marker_cells(i, block.ordered, ctx, profile))
        cells.extend(_translate_inlines(item.inlines, ctx, profile))
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
    convention as the number sign. (Deriving them from the item's
    block-level span instead mixes document coordinates into an
    otherwise leaf-local cell sequence, and an ordinal digit then
    claims item-text characters it never came from.)
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
    return [_replace(c, source_span=edge) for c in cells]


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
    for row in block.blocks:
        cells: list[BrailleCell] = []
        for j, table_cell in enumerate(row.blocks):
            if j > 0:
                # Column separator: trace to the column's leading edge.
                edge = (
                    Span(table_cell.span.start, table_cell.span.start)
                    if table_cell.span
                    else None
                )
                cells.append(blank_cell(edge))
                cells.append(blank_cell(edge))
            cells.extend(_translate_inlines(table_cell.inlines, ctx, profile))
        blocks.append(
            BrailleBlock(
                block_type="table_row",
                id=row.id,
                cells=cells,
            )
        )
    return blocks


# ---- Footnote -------------------------------------------------------------


def _footnote_ref_cells(ref: str, profile: BrailleProfile) -> list[BrailleCell]:
    """Render a footnote ref (``"1"``, ``"a"``, ``"*"``) as a marker.

    V1 just spells the ref characters out via the profile's punct /
    letter tables and follows them with a blank cell so the body text
    has clear separation. Unknown chars produce an unknown cell.

    Like a list marker (:func:`_list_marker_cells`), the ref is synthesised
    print structure rather than a character of the footnote's ``text``, so
    every cell it produces anchors to the body text's LEADING EDGE in
    leaf-local coordinates: ``Span(0, 0)``. Walking
    ``Footnote.span`` instead — a *document* coordinate, and one that
    describes the footnote body rather than the ref — makes each ref
    character claim a body character it never came from, offset by wherever
    the footnote sits in the
    source. Giving the ref its own precise positions would need a coordinate
    contract for ``ref`` itself; ``Block.span`` cannot supply one.
    """
    if not ref:
        return []
    edge = Span(0, 0)

    def sp(_i: int) -> Span:
        return edge

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
    # Trailing separator blank — the same leading-edge anchor as the rest of
    # the marker run: it separates the marker from the body, and belongs to
    # neither.
    cells.append(blank_cell(edge))
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
