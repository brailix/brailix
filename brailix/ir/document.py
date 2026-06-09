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
from brailix.ir.inline import InlineNode
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
            default = f.default
            if (
                value is None
                or value == default
                # default_factory fields have default == MISSING, so skip
                # any empty sequence (e.g. List.items / table rows) too.
                or (isinstance(value, (list, tuple)) and not value)
            ):
                continue
            d[f.name] = value
        if self.text is not None:
            d["text"] = self.text
        if self.children:
            d["children"] = [c.to_dict() for c in self.children]
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        return d


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
        d = Block.to_dict(self)
        if self.ordered:
            d["ordered"] = True
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
    formula text is written in (latex / mathml / asciimath / plain)."""

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
    source: str = "plain"  # musicxml / mxl / jianpu / midi / abc / plain


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
    if key == "span" and isinstance(value, (list, tuple)) and len(value) == 2:
        return Span(int(value[0]), int(value[1]))
    if key == "children" and isinstance(value, list):
        return [inline_from_dict(v) for v in value]
    if key == "items" and isinstance(value, list):
        return [_typed_child(cls, key, v, ListItem) for v in value]
    if key == "cells" and isinstance(value, list):
        return [_typed_child(cls, key, v, TableCell) for v in value]
    if key == "rows" and isinstance(value, list):
        return [_typed_child(cls, key, v, TableRow) for v in value]
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
