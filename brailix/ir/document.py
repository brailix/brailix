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
from brailix.ir.inline import (
    InlineNode,
    _is_omittable,
    _reject_unhandled_nested_payload,
)
from brailix.ir.inline import from_dict as inline_from_dict

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Block:
    """Abstract base for every block type."""

    type: ClassVar[str] = "block"
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

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type}
        if self.id is not None:
            d["id"] = self.id
        for f in fields(self):
            if f.name in ("id", "children", "text", "span"):
                continue
            value = getattr(self, f.name)
            # Omit defaults / empties (shared with inline to_dict); and never
            # emit a raw IR object — structural fields (List.items / Table.rows
            # / TableRow.cells) are serialised by the owning subclass override,
            # so a forgotten override drops the field loudly-testably rather
            # than producing a payload that only explodes at json.dumps time.
            if _is_omittable(value, f.default) or _is_ir_payload(value):
                continue
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
        return d

    def structure_key(self) -> str:
        """Structural identity beyond the text surface, for cache keys.

        :func:`brailix.pipeline.block_hash` keys only on the text surface
        plus the profile, so two same-text blocks of different shape — a
        Heading vs a Paragraph, an ordered vs an unordered List, two Tables
        of different size — hash identically.  A block cache keyed on the
        bare text hash would then serve one block's compiled braille for the
        other.  A front-end composes ``block_hash`` with this key to keep
        its cache sound; the compiler itself stays cache-agnostic.

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
        invalidate this block's cache entry).
        """
        parts = [self.type]
        for f in fields(self):
            if f.name in ("id", "children", "text", "span"):
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

    These are the fields the generic :meth:`Block.to_dict` loop must not emit
    raw: the structural containers (``List.items`` / ``Table.rows`` /
    ``TableRow.cells``) are serialised by each subclass's ``to_dict`` override,
    and inline ``children`` go through a dedicated path. Skipping IR payloads
    keeps the base loop limited to JSON-native scalars, so a subclass that adds
    a structural field but forgets to override ``to_dict`` drops it (caught by a
    round-trip test) rather than emitting an object that only blows up later at
    ``json.dumps``.
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
    ordered: bool = False
    items: list[ListItem] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        # ``ordered`` (a plain bool) is already emitted by the base loop; only
        # ``items`` (an IR payload the base loop skips) needs an override.
        d = Block.to_dict(self)
        if self.items:
            d["items"] = [it.to_dict() for it in self.items]
        return d


@dataclass(slots=True)
class TableCell(Block):
    type: ClassVar[str] = "table_cell"


@dataclass(slots=True)
class TableRow(Block):
    type: ClassVar[str] = "table_row"
    cells: list[TableCell] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = Block.to_dict(self)
        if self.cells:
            d["cells"] = [c.to_dict() for c in self.cells]
        return d


@dataclass(slots=True)
class Table(Block):
    type: ClassVar[str] = "table"
    rows: list[TableRow] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = Block.to_dict(self)
        if self.rows:
            d["rows"] = [r.to_dict() for r in self.rows]
        return d


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
    the parsed MusicXML tree is filled by ``Pipeline._populate_block``
    into ``children=[MusicInline(score=tree)]`` — same indirection as
    :class:`MathBlock` → :class:`~brailix.ir.inline.MathInline` (see
    ``ARCHITECTURE.md``)."""

    type: ClassVar[str] = "score"
    source: str = "plain"  # musicxml / mxl / midi / abc / plain


@dataclass(slots=True)
class MusicBlock(Block):
    """Display-mode single-passage music block, analogue of
    :class:`MathBlock`. Same children-carrier pattern as
    :class:`ScoreBlock`; see ``ARCHITECTURE.md``"""

    type: ClassVar[str] = "music_block"
    source: str = "plain"


@dataclass(slots=True)
class ImageAlt(Block):
    """Block-level alt text for an image."""

    type: ClassVar[str] = "image_alt"


@dataclass(slots=True)
class GraphicBlock(Block):
    """A tactile graphic. ``source`` is the format the raw graphic in
    ``text`` is written in (``svg`` / ``primitives`` / ``figure``). The
    normalised SVG tree (the graphics IR) is filled by
    ``Pipeline._populate_block`` into ``children=[GraphicInline(svg=tree)]``
    — the same children-carrier indirection as :class:`MathBlock` →
    :class:`~brailix.ir.inline.MathInline` and :class:`ScoreBlock` →
    :class:`~brailix.ir.inline.MusicInline` (see
    ``ARCHITECTURE.md``).

    A tactile graphic does **not** translate to braille cells; it compiles
    to a :class:`~brailix.ir.tactile.TactileRaster` via
    :meth:`~brailix.pipeline.Pipeline.translate_graphic`, so this block is
    not routed through the braille backend dispatcher."""

    type: ClassVar[str] = "graphic"
    source: str = "svg"  # svg / primitives / figure


# ---------------------------------------------------------------------------
# Document root
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class DocumentIR:
    """Root container. ``metadata`` carries language, profile name, and
    any free-form annotations the Input layer wants to preserve."""

    version: str = "1.0"
    metadata: dict[str, Any] = field(default_factory=dict)
    blocks: list[Block] = field(default_factory=list)

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

    The structural fields (``items``/``cells``/``rows``) carry typed
    sub-blocks (List wants ListItem, TableRow wants TableCell, Table
    wants TableRow). We validate the type tag of each child so a
    round-trip can't silently smuggle e.g. a Paragraph into a
    ``TableRow.cells`` list. Mismatches raise :class:`TypeError` with
    the parent field and the offending entry's type tag so the
    serializer / authoring tool can be fixed at the source.
    """
    if key == "span":
        return None if value is None else Span.from_tuple(value)
    if key == "children" and isinstance(value, list):
        return [inline_from_dict(v) for v in value]
    if key == "items" and isinstance(value, list):
        return [_typed_child(cls, key, v, ListItem) for v in value]
    if key == "cells" and isinstance(value, list):
        return [_typed_child(cls, key, v, TableCell) for v in value]
    if key == "rows" and isinstance(value, list):
        return [_typed_child(cls, key, v, TableRow) for v in value]
    _reject_unhandled_nested_payload(key, value)
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
    """
    child = block_from_dict(payload) if isinstance(payload, dict) else payload
    if not isinstance(child, expected):
        actual = type(child).__name__
        raise TypeError(
            f"{parent_cls.__name__}.{field_name} expects {expected.__name__} "
            f"entries; got {actual} (block type "
            f"{payload.get('type') if isinstance(payload, dict) else type(payload).__name__!r})"
        )
    return child
