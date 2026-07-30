"""Document IR: block-level structure.

A :class:`DocumentIR` is the top-level container produced by the Input
layer. Each :class:`Block` represents a structural unit (paragraph,
heading, list item, table cell, ...). Block ``children`` are inline
nodes from :mod:`brailix.ir.inline`; until those are populated the
block can carry raw text via ``text``.
"""

from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

from brailix.core.span import Span
from brailix.ir import _serde
from brailix.ir.inline import InlineNode
from brailix.ir.inline import from_dict as inline_from_dict

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


# ``Block.type`` — the block's tag — shadows the builtin inside every block
# class body, so a ``type[Block]`` annotation written in there would name the
# tag (a ``str``) instead. Aliased out here, at module scope, where ``type`` is
# still the builtin.
type _BlockClass = type[Block]


@dataclass(slots=True)
class Block:
    """Abstract base for every block type.

    **Span coordinate contract.** Two coordinate systems meet here, one
    per level:

    * ``Block.span`` locates the block in its **source document**. When
      ``text`` is a verbatim slice of that source, the span is
      content-exact: ``source[span.start:span.end] == block.text`` (the
      *exact-slice contract*). The plain-text adapter guarantees it for
      every block; the Markdown adapter for headings, list items and
      single-line paragraphs (marker prefixes like ``# `` / ``- `` are
      *outside* the span). Blocks whose ``text`` is derived rather than
      sliced — a multi-line paragraph joined with spaces, a quote with
      its ``> `` markers stripped, fence bodies, table cells — carry a
      line-range span: the block is *located*, but per-character
      rebasing is not implied. Formats with no character-level source
      coordinate (``.docx``) synthesise spans.
    * Inline node spans and braille-cell ``source_span``\\ s are
      **leaf-local**: offsets into the owning leaf block's ``text``,
      starting at 0 per block. Under the exact-slice contract,
      ``block.span.start + leaf_local`` is the exact source position.

    **The one exception: a table row is the leaf.** The backend flattens a
    :class:`TableRow` into a single braille block, joining its cells with two
    blank cells, so the row — not the cell — is the unit a consumer resolves
    against. Both a :class:`TableCell`'s own ``span`` and the spans inside it
    are therefore **row-local**: offsets into ``"  ".join(cell.text for cell
    in row.cells)``, so ``row_text[node.span]`` slices the node's surface
    exactly. The pipeline establishes this on every populate pass (see
    :meth:`brailix.pipeline.frontend_driver.FrontendDriver._populate_row`), so it also holds
    after an edit shifts one column's width. Consequently a cell's ``span``
    is *not* a source-document span the way every other block's is — a table
    has no per-cell document coordinate to begin with, since the Markdown
    adapter rebuilds cell offsets from the de-syntaxed text rather than
    slicing the source row.
    """

    type: ClassVar[str] = "block"
    # Fields holding nested *blocks*, declared as ``{field name: the Block
    # subclass its entries must be}`` — ``List.items`` → :class:`ListItem`,
    # ``Table.rows`` → :class:`TableRow`. One declaration drives both
    # directions, and type-checks the entries in both: :meth:`to_dict` emits
    # the field, :func:`_deserialize_block_value` rebuilds it, and each side
    # refuses an entry that is not the declared class — so a tree that
    # serializes is a tree that reloads.
    #
    # It exists because the two directions used to be written separately, and
    # only one of them failed when a subclass forgot: the deserializer rejected
    # an unregistered nested payload loudly, while the serializer *skipped* the
    # field — so a new block type with a structural field saved successfully,
    # produced valid JSON, and came back from a reload without the field. Now
    # the base loop refuses to serialize nested IR nobody declared, so the
    # omission surfaces where the tree is built rather than after a round trip.
    structural_fields: ClassVar[dict[str, _BlockClass]] = {}
    id: str | None = None
    children: list[InlineNode] = field(default_factory=list)
    text: str | None = None  # used before Frontend has built children
    span: Span | None = None
    # Horizontal alignment carried over from the source document, when it
    # declares one the braille layout can honour: ``"center"`` or
    # ``"right"``.  ``None`` (the default) means flush-left / unspecified —
    # the layout's own per-block-type defaults apply (e.g. a level-1 heading
    # still centres).  Source alignments braille has no convention for
    # (justified / distributed) normalise to ``None`` at the input layer, so
    # only values the renderer acts on ever reach the IR.
    align: str | None = None
    # Provenance stamp for populated ``children``: the compilation
    # fingerprint (:attr:`brailix.pipeline.Pipeline.fingerprint`) of the
    # pipeline whose frontend built them.  ``populate_block`` re-runs the
    # frontend when this differs from the current pipeline's fingerprint, so
    # semantic IR built under one configuration (resolver, user dictionary,
    # profile content, ...) is never silently reused under another.  ``None``
    # means "not populated by a pipeline" — hand-built children keep the
    # documented "used as-is" contract.  In-memory only: excluded from
    # equality, ``to_dict`` and ``structure_key`` (it is cache provenance,
    # not document content or structural identity).
    frontend_fingerprint: str | None = field(
        default=None, compare=False, repr=False
    )

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.id is not None:
            d["id"] = self.id
        for f in fields(self):
            if f.name in ("id", "children", "text", "span", "frontend_fingerprint"):
                continue
            value = getattr(self, f.name)
            # Omit defaults / empties (shared with inline to_dict).
            if _serde.is_omittable(value, f.default):
                continue
            # A raw IR object is never emitted by this loop: it is not
            # JSON-native, and the nested-block fields have their own pass
            # below (in declaration order, after ``span``, which is also where
            # the subclass overrides that preceded it put them). Undeclared
            # nested IR is refused rather than skipped — see
            # :attr:`structural_fields`.
            if _is_ir_payload(value):
                if f.name in self.structural_fields:
                    continue
                raise TypeError(
                    f"{type(self).__name__}.{f.name} holds nested IR that no "
                    f"serializer emits. Declare it in ``structural_fields`` "
                    f"({{'{f.name}': <the Block subclass its entries are>}}) so "
                    f"to_dict writes it and block_from_dict rebuilds it. "
                    f"Skipping it wrote a valid payload that came back from a "
                    f"reload without the field; an inline child belongs in "
                    f"``children``."
                )
            d[f.name] = value
        if self.text is not None:
            d["text"] = self.text
        if self.children:
            for c in self.children:
                if not isinstance(c, InlineNode):
                    raise TypeError(
                        f"{type(self).__name__}.children expects InlineNode "
                        f"entries; got {type(c).__name__}. Structural children "
                        f"belong in items / cells / rows, not children — "
                        f"block_from_dict rebuilds children via the inline "
                        f"registry and cannot round-trip a block tag."
                    )
            d["children"] = [c.to_dict() for c in self.children]
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        for name, expected in self.structural_fields.items():
            value = getattr(self, name)
            if not value:
                continue
            if isinstance(value, (Block, InlineNode)) or not isinstance(
                value, (list, tuple)
            ):
                raise TypeError(
                    f"{type(self).__name__}.{name} is declared in "
                    f"``structural_fields`` but holds "
                    f"{type(value).__name__}, not a list of blocks"
                )
            entries = []
            for child in value:
                # The same check ``_typed_child`` makes on the way back, made
                # here too — otherwise the declaration is enforced in one
                # direction only, and a Paragraph in ``items`` writes a payload
                # that reloading rejects. Refusing it here means the tree that
                # serializes is a tree that reloads.
                if not isinstance(child, expected):
                    raise TypeError(
                        f"{type(self).__name__}.{name} expects "
                        f"{expected.__name__}; got {type(child).__name__}. "
                        f"block_from_dict enforces the same declaration when "
                        f"rebuilding, so this would write a payload that "
                        f"cannot be read back."
                    )
                entries.append(child.to_dict())
            d[name] = entries
        return d

    def structure_key(self) -> str:
        """Structural identity beyond the text surface, for cache keys.

        The text surface can't tell two same-text blocks of different shape
        apart — a Heading from a Paragraph, an ordered from an unordered List,
        two Tables of different size — and they render under different layout
        rules, so a cache keyed on the surface alone would serve one block's
        compiled braille for the other.  This supplies the missing dimension.

        :func:`brailix.pipeline.block_hash` folds it in **itself**, so a bare
        ``block_hash`` is already structurally safe and a front-end must not
        compose this key in a second time (it would only re-salt what is
        already there).  Callers add their own salt for dimensions the compiler
        knows nothing about — a proofreading front-end's override list, say.

        Derived generically from :func:`~dataclasses.fields` so every
        layout-affecting scalar (heading ``level``, list ``ordered``,
        ``align``, math / music ``source``, ...) and the shape of structural
        containers (``items`` / ``rows`` / ``cells`` — their length plus,
        recursively, the structure of any nested block, so a ``Table``'s
        per-row column counts are captured, not just its row count) is
        captured automatically — a new structural field on any subclass is
        covered without editing this method or the cache key.  ``children``
        and ``text`` are excluded (the surface hash covers them); ``id`` and
        ``span`` too (an edit elsewhere shifts ``span`` but must not
        invalidate this block's cache entry); ``frontend_fingerprint`` too —
        it is populate provenance, not structure, and the configuration it
        names is :func:`~brailix.pipeline.block_hash`'s ``fingerprint``
        dimension, so folding it here would make a populated block hash
        apart from its identically-configured unpopulated twin.
        """
        parts = [self.type]
        for f in fields(self):
            if f.name in ("id", "children", "text", "span", "frontend_fingerprint"):
                continue
            value = getattr(self, f.name)
            if isinstance(value, (list, tuple)):
                # Structural container: its length matters, plus the shape of
                # any Block element — a Table's row count alone can't tell a
                # 2-column grid from a 1-then-3 one, so recurse into nested
                # blocks (element *text* stays the surface hash's job).
                parts.append(f"{f.name}#{len(value)}")
                parts.extend(
                    elem.structure_key()
                    for elem in value
                    if isinstance(elem, Block)
                )
            else:
                parts.append(f"{f.name}={value!r}")
        return "|".join(parts)


def _is_ir_payload(value: Any) -> bool:
    """True if ``value`` is an IR node (:class:`Block` / :class:`InlineNode`)
    or a sequence containing one.

    These are the fields the generic :meth:`Block.to_dict` scalar loop must not
    emit raw: the nested-block containers (``List.items`` / ``Table.rows`` /
    ``TableRow.cells``) are emitted by the ``structural_fields`` pass, and
    inline ``children`` go through a dedicated path. Keeping the scalar loop to
    JSON-native values is what makes "a payload that serialises is a payload
    that reloads" true rather than aspirational — anything nested has to be
    declared, and undeclared nested IR raises there instead of vanishing.
    """
    if isinstance(value, (Block, InlineNode)):
        return True
    if isinstance(value, (list, tuple)):
        return any(isinstance(v, (Block, InlineNode)) for v in value)
    return False


# ---------------------------------------------------------------------------
# Concrete blocks
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Heading(Block):
    type: ClassVar[str] = "heading"
    level: int = 1


@dataclass(slots=True)
class Paragraph(Block):
    type: ClassVar[str] = "paragraph"


@dataclass(slots=True)
class ListItem(Block):
    type: ClassVar[str] = "list_item"


@dataclass(slots=True)
class List(Block):
    """An ordered or unordered list. ``items`` is the same as ``children``
    but typed as :class:`ListItem` by convention."""

    type: ClassVar[str] = "list"
    # ``ordered`` (a plain bool) rides the base scalar loop; ``items`` is
    # nested IR, so it is declared — which is what emits it *and* what rebuilds
    # it as ListItem entries.
    structural_fields: ClassVar[dict[str, _BlockClass]] = {"items": ListItem}
    ordered: bool = False
    items: list[ListItem] = field(default_factory=list)


@dataclass(slots=True)
class TableCell(Block):
    """One table cell.

    Span note: a cell and everything under it carries **row-local** offsets,
    not the leaf-local ones every other block uses — see the coordinate
    contract on :class:`Block`. The backend renders a whole
    :class:`TableRow` as one braille block, so the row's joined text is the
    coordinate system its provenance has to resolve against.
    """

    type: ClassVar[str] = "table_cell"


@dataclass(slots=True)
class TableRow(Block):
    type: ClassVar[str] = "table_row"
    structural_fields: ClassVar[dict[str, _BlockClass]] = {"cells": TableCell}
    cells: list[TableCell] = field(default_factory=list)


@dataclass(slots=True)
class Table(Block):
    type: ClassVar[str] = "table"
    structural_fields: ClassVar[dict[str, _BlockClass]] = {"rows": TableRow}
    rows: list[TableRow] = field(default_factory=list)


@dataclass(slots=True)
class Quote(Block):
    type: ClassVar[str] = "quote"


@dataclass(slots=True)
class Footnote(Block):
    type: ClassVar[str] = "footnote"
    ref: str | None = None


@dataclass(slots=True)
class CodeBlock(Block):
    type: ClassVar[str] = "code_block"
    language: str | None = None


@dataclass(slots=True)
class MathBlock(Block):
    """Display-mode math block. ``source`` is the source format the raw
    formula text is written in (latex / mathml / plain)."""

    type: ClassVar[str] = "math_block"
    source: str = "plain"


@dataclass(slots=True)
class ScoreBlock(Block):
    """Full score (metadata + parts + measures). Holds only ``source``;
    the parsed MusicXML tree is filled by ``FrontendDriver.populate_block``
    into ``children=[MusicInline(score=tree)]`` — same indirection as
    :class:`MathBlock` → :class:`~brailix.ir.inline.MathInline`."""

    type: ClassVar[str] = "score"
    source: str = "plain"  # musicxml / mxl / midi / abc / plain


@dataclass(slots=True)
class MusicBlock(Block):
    """Display-mode single-passage music block, analogue of
    :class:`MathBlock`. Same children-carrier pattern as
    :class:`ScoreBlock`."""

    type: ClassVar[str] = "music_block"
    source: str = "plain"


@dataclass(slots=True)
class ImageAlt(Block):
    """Block-level placeholder for an image that has **not** been converted
    to a tactile graphic: ``text`` carries the alt text (translated to
    braille like ordinary prose), ``target`` the image reference — a
    document-asset name (``media/image1.png``, resolved against
    :attr:`DocumentIR.assets` / the document's asset store) or a plain
    filesystem path. The backend flags each one as ``IMAGE_NOT_CONVERTED``
    so the user can decide, image by image, whether to convert it into a
    ``graphic-image`` fence. ``target``
    is ``None`` for a bare alt-text block with no locatable image."""

    type: ClassVar[str] = "image_alt"
    target: str | None = None


@dataclass(slots=True)
class GraphicBlock(Block):
    """A tactile graphic. ``source`` is the format the raw graphic in
    ``text`` is written in (``svg`` / ``primitives`` / ``figure`` / ``image``). The
    normalised SVG tree (the graphics IR) is filled by
    ``FrontendDriver.populate_block`` into ``children=[GraphicInline(svg=tree)]``
    — the same children-carrier indirection as :class:`MathBlock` →
    :class:`~brailix.ir.inline.MathInline` and :class:`ScoreBlock` →
    :class:`~brailix.ir.inline.MusicInline`.

    A tactile graphic does **not** translate to braille cells; it compiles to a
    :class:`~brailix.ir.tactile.TactileRaster`. It does still go through the
    *block-level* backend expansion like every other block, which recognises it
    and emits an **empty** ``"graphic"`` braille block: the figure holds its
    place in the block flow, and its inline child — which has no cells to give
    — is never handed to the braille node dispatcher. The dots ride on the
    raster instead, attached to the compiled block beside those empty cells."""

    type: ClassVar[str] = "graphic"
    source: str = "svg"  # svg / primitives / figure / image


# ---------------------------------------------------------------------------
# Document root
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DocumentIR:
    """Root container. ``metadata`` carries language, profile name, and
    any free-form annotations the Input layer wants to preserve.

    ``assets`` carries binary side-payloads a *binary* input container
    embedded next to its text — today the images a ``.docx`` packs under
    ``word/media/`` — keyed by an asset name (``media/image1.png``) that
    blocks reference via :attr:`ImageAlt.target` (and, once converted, a
    ``graphic-image`` fence's ``path``). It is the document-level side
    table OOXML itself uses, decoded eagerly at the input boundary per
    the ARCHITECTURE#arch-layers rule that the text IR carries no binary payload;
    :meth:`to_dict` deliberately excludes it (that is the text-IR view —
    a container format that persists a document's assets serialises them
    itself, in its own encoding)."""

    version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)
    assets: dict[str, bytes] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "type": "document",
            "metadata": dict(self.metadata),
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> DocumentIR:
        return cls(
            version=payload.get("version", "1.0"),
            metadata=dict(payload.get("metadata", {})),
            blocks=[block_from_dict(b) for b in payload.get("blocks", [])],
        )


# ---------------------------------------------------------------------------
# Registry + (de)serialization
# ---------------------------------------------------------------------------


_BLOCK_REGISTRY: dict[str, type[Block]] = {
    cls.type: cls
    for cls in (
        Heading,
        Paragraph,
        List,
        ListItem,
        Table,
        TableRow,
        TableCell,
        Quote,
        Footnote,
        CodeBlock,
        MathBlock,
        ScoreBlock,
        MusicBlock,
        ImageAlt,
        GraphicBlock,
    )
}


def block_for(type_name: str) -> type[Block]:
    try:
        return _BLOCK_REGISTRY[type_name]
    except KeyError as e:
        raise KeyError(f"unknown block type: {type_name!r}") from e


def block_from_dict(payload: dict[str, Any]) -> Block:
    type_name = payload.get("type")
    if type_name is None:
        raise ValueError("missing 'type' in block payload")
    cls = block_for(type_name)
    valid = {f.name for f in fields(cls)}
    kwargs: dict[str, Any] = {}
    for key, value in payload.items():
        if key == "type":
            continue
        if key not in valid:
            continue
        kwargs[key] = _deserialize_block_value(cls, key, value)
    return cls(**kwargs)


def _deserialize_block_value(cls: type[Block], key: str, value: Any) -> Any:
    """Reconstruct a block-side value from its serialized form.

    A nested-block field carries typed sub-blocks, and which type is the
    owning class's own declaration (:attr:`Block.structural_fields`: List wants
    ListItem, TableRow wants TableCell, Table wants TableRow). We validate the
    type tag of each child so a round-trip can't silently smuggle e.g. a
    Paragraph into a ``TableRow.cells`` list. Mismatches raise
    :class:`TypeError` with the parent field and the offending entry's type tag
    so the serializer / authoring tool can be fixed at the source.

    Read from the declaration rather than from a list of field names here: the
    two directions then cannot disagree about which fields are nested, which is
    how a field could be emitted by one side and unknown to the other.
    """
    if key == "span":
        return None if value is None else Span.from_tuple(value)
    if key == "children" and isinstance(value, list):
        return [inline_from_dict(v) for v in value]
    expected = cls.structural_fields.get(key)
    if expected is not None and isinstance(value, list):
        return [_typed_child(cls, key, v, expected) for v in value]
    _serde.reject_unhandled_nested_payload(key, value)
    return value


def _typed_child(
    parent_cls: type[Block],
    field_name: str,
    payload: Any,
    expected: type[Block],
) -> Block:
    """Deserialize ``payload`` and verify it's an instance of ``expected``.

    Raises :class:`TypeError` rather than silently accepting a mismatched
    child class. Without this, round-trip JSON could carry e.g. a
    Paragraph in a TableRow's ``cells`` list, and the resulting Block
    tree would type-check at the dataclass level but break every
    downstream consumer that introspects ``cells[i]``.

    The check itself is :func:`brailix.ir._serde.typed_child`, shared with the
    inline side; this binds it to the block family and its wording.
    """
    return _serde.typed_child(
        payload,
        expected=expected,
        factory=block_from_dict,
        label=f"{parent_cls.__name__}.{field_name}",
        kind="block",
    )
