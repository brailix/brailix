"""Document IR: block-level structure.

A :class:`DocumentIR` is the top-level container produced by the Input
layer. Each :class:`Block` represents a structural unit (paragraph,
heading, list item, table cell, ...). Block ``inlines`` are inline
nodes from :mod:`brailix.ir.inline`; until those are populated the
block can carry raw text via ``text``.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from dataclasses import fields as _fields
from typing import Any as _Any
from typing import ClassVar as _ClassVar

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

# Fields the generic ``to_dict`` scalar loop never emits, and why each:
#
# * ``id`` / ``text`` / ``inlines`` / ``span`` / ``blocks`` — each has its own
#   pass, in a fixed order the payload's readability depends on;
# * ``frontend_fingerprint`` / ``tree_text`` — populate provenance rather than
#   document content: which configuration built the inlines, and which text
#   the parsed tree came from. Both are in-memory only.
_PAYLOAD_EXCLUDED = frozenset(
    {"id", "inlines", "blocks", "text", "span", "frontend_fingerprint", "tree_text"}
)


@_dataclass(slots=True)
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
    in row.blocks)``, so ``row_text[node.span]`` slices the node's surface
    exactly. The pipeline establishes this on every populate pass (see
    :meth:`brailix.pipeline.frontend_driver.FrontendDriver._populate_row`), so it also holds
    after an edit shifts one column's width. Consequently a cell's ``span``
    is *not* a source-document span the way every other block's is — a table
    has no per-cell document coordinate to begin with, since the Markdown
    adapter rebuilds cell offsets from the de-syntaxed text rather than
    slicing the source row.
    """

    type: _ClassVar[str] = "block"
    # The block class every entry of :attr:`blocks` must be — ``ListItem`` for
    # a :class:`List`, ``TableRow`` for a :class:`Table` — or ``None`` for a
    # block that holds no nested blocks at all, which is most of them.
    #
    # One declaration, enforced in both directions: :meth:`to_dict` refuses an
    # entry that is not the declared class and :func:`_deserialize_block_value`
    # refuses the same on the way back, so a tree that serializes is a tree
    # that reloads. Declaring it on the class rather than per field is what
    # there is left to declare, now that every block's nested blocks live in
    # one field with one name.
    child_type: _ClassVar[_BlockClass | None] = None
    id: str | None = None
    # A block's two kinds of content, one field each.
    #
    # ``inlines`` are the typed tokens a leaf block's text became — words,
    # numbers, punctuation, an inline formula. ``blocks`` are nested blocks: a
    # list's items, a table's rows, a row's cells.
    #
    # They used to be ``children`` (inline only, despite the name) plus a
    # per-class structural field — ``List.items``, ``Table.rows``,
    # ``TableRow.cells`` — which meant the document was two trees wearing one
    # name. Everything that walked it had to know which of four field names a
    # given block kept its children under: the serializer needed a declaration
    # table to find them, the deserializer a matching one to rebuild them,
    # ``structure_key`` had to recurse through whichever were declared, and
    # every consumer that traverses blocks — the frontend driver, the block
    # backend, the surface reconstruction, the front-end's leaf collector —
    # spelled out the same three-way branch. One name for one relationship
    # deletes all of that.
    inlines: list[InlineNode] = _field(default_factory=list)
    blocks: list[Block] = _field(default_factory=list)
    text: str | None = None  # used before Frontend has built inlines
    span: Span | None = None
    # Horizontal alignment carried over from the source document, when it
    # declares one the braille layout can honour: ``"center"`` or
    # ``"right"``.  ``None`` (the default) means flush-left / unspecified —
    # the layout's own per-block-type defaults apply (e.g. a level-1 heading
    # still centres).  Source alignments braille has no convention for
    # (justified / distributed) normalise to ``None`` at the input layer, so
    # only values the renderer acts on ever reach the IR.
    align: str | None = None
    # Provenance stamp for populated ``inlines``: the compilation
    # fingerprint (:attr:`brailix.pipeline.Pipeline.fingerprint`) of the
    # pipeline whose frontend built them.  ``populate_block`` re-runs the
    # frontend when this differs from the current pipeline's fingerprint, so
    # semantic IR built under one configuration (resolver, user dictionary,
    # profile content, ...) is never silently reused under another.  ``None``
    # means "not populated by a pipeline" — hand-built content keeps the
    # documented "used as-is" contract.  In-memory only: excluded from
    # equality, ``to_dict`` and ``structure_key`` (it is cache provenance,
    # not document content or structural identity).
    frontend_fingerprint: str | None = _field(
        default=None, compare=False, repr=False
    )

    def to_dict(self) -> dict[str, _Any]:
        d: dict[str, _Any] = {"type": self.type}
        if self.id is not None:
            d["id"] = self.id
        for f in _fields(self):
            if f.name in _PAYLOAD_EXCLUDED:
                continue
            value = getattr(self, f.name)
            # Omit defaults / empties (shared with inline to_dict).
            if _serde.is_omittable(value, f.default):
                continue
            # A raw IR object is never emitted by this loop: it is not
            # JSON-native, and ``inlines`` / ``blocks`` have their own passes
            # below. Nested IR anywhere else is refused rather than skipped —
            # skipping is what this used to do, and a new block type with its
            # own nested field then saved successfully, produced valid JSON,
            # and came back from a reload without the field.
            if _is_ir_payload(value):
                raise TypeError(
                    f"{type(self).__name__}.{f.name} holds nested IR that no "
                    f"serializer emits. A block's nested blocks belong in "
                    f"``blocks`` (with ``child_type`` naming what they are) "
                    f"and its inline tokens in ``inlines``; those are the two "
                    f"this loader knows how to rebuild."
                )
            # A domain tree (MathML / MusicXML / SVG) is not JSON-native
            # either, but it *is* this loop's business: it rides the payload
            # as XML text, the way it always has — it just used to do so from
            # a carrier inline node one level down.
            if isinstance(value, _ET.Element):
                d[f.name] = _serde.serialize_xml_tree(value)
                continue
            d[f.name] = value
        if self.text is not None:
            d["text"] = self.text
        if self.inlines:
            for c in self.inlines:
                if not isinstance(c, InlineNode):
                    raise TypeError(
                        f"{type(self).__name__}.inlines expects InlineNode "
                        f"entries; got {type(c).__name__}. A nested block "
                        f"belongs in ``blocks`` — block_from_dict rebuilds "
                        f"``inlines`` via the inline registry and cannot "
                        f"round-trip a block tag."
                    )
            d["inlines"] = [c.to_dict() for c in self.inlines]
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        if self.blocks:
            expected = self.child_type
            if expected is None:
                raise TypeError(
                    f"{type(self).__name__} holds nested blocks but declares "
                    f"no ``child_type``, so nothing says what they must be — "
                    f"and block_from_dict would refuse to rebuild them. "
                    f"Declare it, or put the content in ``inlines``."
                )
            entries = []
            for child in self.blocks:
                # The same check ``_typed_child`` makes on the way back, made
                # here too — otherwise the declaration is enforced in one
                # direction only, and a Paragraph among a List's items writes
                # a payload that reloading rejects. Refusing it here means the
                # tree that serializes is a tree that reloads.
                if not isinstance(child, expected):
                    raise TypeError(
                        f"{type(self).__name__}.blocks expects "
                        f"{expected.__name__}; got {type(child).__name__}. "
                        f"block_from_dict enforces the same declaration when "
                        f"rebuilding, so this would write a payload that "
                        f"cannot be read back."
                    )
                entries.append(child.to_dict())
            d["blocks"] = entries
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
        ``align``, math / music ``source``, ...) and the shape of ``blocks``
        (its length plus, recursively, the structure of each nested block, so
        a ``Table``'s per-row column counts are captured, not just its row
        count) is captured automatically — a new scalar on any subclass is
        covered without editing this method or the cache key.  What is left
        out, and why, is :data:`_STRUCTURE_KEY_EXCLUDED`.
        """
        parts = [self.type]
        for f in _fields(self):
            if f.name in _STRUCTURE_KEY_EXCLUDED:
                continue
            value = getattr(self, f.name)
            if isinstance(value, (list, tuple)):
                # ``blocks``: its length matters, plus the shape of each nested
                # block — a Table's row count alone can't tell a 2-column grid
                # from a 1-then-3 one, so recurse (element *text* stays the
                # surface hash's job).
                parts.append(f"{f.name}#{len(value)}")
                parts.extend(
                    elem.structure_key()
                    for elem in value
                    if isinstance(elem, Block)
                )
            else:
                parts.append(f"{f.name}={value!r}")
        return "|".join(parts)


# Fields :meth:`Block.structure_key` leaves out. ``blocks`` is the one the
# payload omits that this one keeps — the shape of the nested tree IS the
# structure — so it comes back in. ``tree`` is the *parsed form* of ``text``
# under ``source``,
# and both of those are in the key already (``text`` via the surface hash,
# ``source`` through the loop). It has to be left out rather than merely being
# redundant: ``repr`` of an ``ET.Element`` carries its memory address, so
# folding it in would mint a different key for every parse of the same formula
# and no cache would ever hit.
_STRUCTURE_KEY_EXCLUDED = (_PAYLOAD_EXCLUDED | {"tree"}) - {"blocks"}


def _is_ir_payload(value: _Any) -> bool:
    """True if ``value`` is an IR node (:class:`Block` / :class:`InlineNode`)
    or a sequence containing one.

    These are the values the generic :meth:`Block.to_dict` scalar loop must not
    emit raw. ``inlines`` and ``blocks`` have their own passes; anything else
    holding IR is a field nobody taught the loader about. Keeping the scalar
    loop to JSON-native values is what makes "a payload that serialises is a
    payload that reloads" true rather than aspirational.
    """
    if isinstance(value, (Block, InlineNode)):
        return True
    if isinstance(value, (list, tuple)):
        return any(isinstance(v, (Block, InlineNode)) for v in value)
    return False


# ---------------------------------------------------------------------------
# Concrete blocks
# ---------------------------------------------------------------------------


@_dataclass(slots=True)
class Heading(Block):
    type: _ClassVar[str] = "heading"
    level: int = 1


@_dataclass(slots=True)
class Paragraph(Block):
    type: _ClassVar[str] = "paragraph"


@_dataclass(slots=True)
class ListItem(Block):
    type: _ClassVar[str] = "list_item"


@_dataclass(slots=True)
class List(Block):
    """An ordered or unordered list. Its items are its ``blocks``."""

    type: _ClassVar[str] = "list"
    child_type: _ClassVar[_BlockClass | None] = ListItem
    ordered: bool = False


@_dataclass(slots=True)
class TableCell(Block):
    """One table cell.

    Span note: a cell and everything under it carries **row-local** offsets,
    not the leaf-local ones every other block uses — see the coordinate
    contract on :class:`Block`. The backend renders a whole
    :class:`TableRow` as one braille block, so the row's joined text is the
    coordinate system its provenance has to resolve against.
    """

    type: _ClassVar[str] = "table_cell"


@_dataclass(slots=True)
class TableRow(Block):
    """One table row. Its cells are its ``blocks``."""

    type: _ClassVar[str] = "table_row"
    child_type: _ClassVar[_BlockClass | None] = TableCell


@_dataclass(slots=True)
class Table(Block):
    """A table. Its rows are its ``blocks``."""

    type: _ClassVar[str] = "table"
    child_type: _ClassVar[_BlockClass | None] = TableRow


@_dataclass(slots=True)
class Quote(Block):
    type: _ClassVar[str] = "quote"


@_dataclass(slots=True)
class Footnote(Block):
    type: _ClassVar[str] = "footnote"
    ref: str | None = None


@_dataclass(slots=True)
class CodeBlock(Block):
    type: _ClassVar[str] = "code_block"
    language: str | None = None


@_dataclass(slots=True)
class EmbeddedBlock(Block):
    """A block whose content is a **domain tree**, not prose.

    Math, music and tactile graphics all arrive the same way: raw source text
    in ``text``, the format it is written in in ``source``, and — once that
    vertical's frontend has run — the normalised tree in ``tree``. MathML,
    MusicXML and SVG *are* the IR for their domains; there is no dataclass
    model of a formula or a score, so the tree rides as an
    :class:`ET.Element`.

    Each of them used to hold that tree one level down, in
    ``inlines=[<a carrier inline node>]`` — a node whose only job was to
    move the tree from the frontend to the backend. It cost three near-identical
    inline types, a fourth entry in every dispatch table, and (for graphics,
    whose carrier has no braille to give at all) a special case in the block
    backend telling it *not* to translate that child. The block owns its tree
    now, and the carrier types are gone.

    The concrete subclasses stay separate rather than collapsing into one
    tagged class, because their ``type`` tags are read downstream: the layout
    pass distinguishes ``score`` from ``music_block``, and every braille block
    carries its source block's tag. What they share is this shape, and sharing
    it is what lets the backend route them through one branch keyed on
    :attr:`domain`.

    ``domain`` names the vertical (``"math"`` / ``"music"`` / ``"graphic"``) —
    the routing key for the backend and the parsed-tree cache. ``tree_format``
    is the human name of the XML dialect, used only in the "that isn't
    well-formed MathML" diagnostic.
    """

    domain: _ClassVar[str] = ""
    tree_format: _ClassVar[str] = "XML"
    source: str = "plain"
    tree: _ET.Element | None = None
    # The ``text`` :attr:`tree` was parsed from — the staleness record for a
    # populated block, and the reason a block edited after population
    # re-parses instead of compiling its old content. A text block gets this
    # for free (its inlines carry the surfaces, so the driver reconstructs
    # what they describe and compares); a tree cannot be compared back to
    # source text, and while the tree hung off a carrier inline node the
    # carrier's ``surface`` was standing in for this by accident. In-memory
    # only, like ``frontend_fingerprint`` and for the same reason: it is
    # populate provenance, not document content, so it stays out of equality,
    # ``to_dict`` and ``structure_key``.
    tree_text: str | None = _field(default=None, compare=False, repr=False)


@_dataclass(slots=True)
class MathBlock(EmbeddedBlock):
    """Display-mode math block. ``source`` is the source format the raw
    formula text is written in (latex / mathml / plain); ``tree`` the
    normalised MathML."""

    type: _ClassVar[str] = "math_block"
    domain: _ClassVar[str] = "math"
    tree_format: _ClassVar[str] = "MathML"


@_dataclass(slots=True)
class ScoreBlock(EmbeddedBlock):
    """Full score (metadata + parts + measures), as normalised MusicXML in
    ``tree``."""

    type: _ClassVar[str] = "score"
    domain: _ClassVar[str] = "music"
    tree_format: _ClassVar[str] = "MusicXML"
    source: str = "plain"  # musicxml / mxl / midi / abc / plain


@_dataclass(slots=True)
class MusicBlock(EmbeddedBlock):
    """Display-mode single-passage music block, analogue of
    :class:`MathBlock`. Parsed in ``"block"`` mode rather than
    ``"score"`` mode — the one thing that distinguishes it from
    :class:`ScoreBlock`."""

    type: _ClassVar[str] = "music_block"
    domain: _ClassVar[str] = "music"
    tree_format: _ClassVar[str] = "MusicXML"


@_dataclass(slots=True)
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

    type: _ClassVar[str] = "image_alt"
    target: str | None = None


@_dataclass(slots=True)
class GraphicBlock(EmbeddedBlock):
    """A tactile graphic. ``source`` is the format the raw graphic in
    ``text`` is written in (``svg`` / ``primitives`` / ``figure`` / ``image``);
    ``tree`` the normalised SVG, which *is* the graphics IR — there is no
    separate vector model.

    A tactile graphic does **not** translate to braille cells; it compiles to a
    :class:`~brailix.ir.tactile.TactileRaster`. It does still go through the
    *block-level* backend expansion like every other block, which emits an
    **empty** ``"graphic"`` braille block: the figure holds its place in the
    block flow while its dots ride on the raster, attached to the compiled
    block beside those empty cells."""

    type: _ClassVar[str] = "graphic"
    domain: _ClassVar[str] = "graphic"
    tree_format: _ClassVar[str] = "SVG"
    source: str = "svg"  # svg / primitives / figure / image


# ---------------------------------------------------------------------------
# Document root
# ---------------------------------------------------------------------------

# The serialization versions :meth:`DocumentIR.from_dict` can load without
# losing content, and the one it writes. Adding an entry here is a claim that
# this release reads that format faithfully — so a version whose payload needs
# reshaping joins the set together with the migration that reshapes it, never
# on its own. ``tests/schemas/document-ir.schema.json`` pins the same set on
# the schema side (a test compares them), so a reader validating against the
# schema and a caller going through ``from_dict`` agree on what is loadable.
#
# ``2.0`` reshapes the block payload: a math / music / graphic block carries
# its parsed tree itself (``"tree"``) instead of in a one-element ``inlines``
# list holding a carrier node. ``1.0`` is **not** in the set, and there is no
# migration, because nothing reads a document-IR payload back: the library
# writes one as an export (``TranslationResult.to_dict``) and a ``.blx``
# project stores its source plus overrides and recompiles the IR on open. A
# 1.0 payload from outside is therefore refused by name rather than
# half-understood — which is exactly what this check is for.
_DEFAULT_IR_VERSION = "2.0"
_SUPPORTED_IR_VERSIONS: frozenset[str] = frozenset({_DEFAULT_IR_VERSION})


def _check_ir_version(version: object, action: str) -> None:
    """Refuse a ``version`` this release cannot round-trip, as a
    :class:`ValueError`. ``action`` names what the failing side does with the
    set ("loads" / "writes and reads"), so each entry point keeps its own
    diagnostic.

    Shared by the two entry points that take a version — construction and
    :meth:`DocumentIR.from_dict` — because when they were written separately
    both checked only the *value*, and the value check alone is not safe on
    unvalidated input: ``version not in _SUPPORTED_IR_VERSIONS`` asks the
    frozenset to **hash** whatever arrived, so a payload whose ``"version"``
    is a JSON array or object (both legal JSON, both reachable from a ``.blx``
    file or an API caller) left the boundary as ``TypeError: unhashable type:
    'list'`` — an implementation detail escaping a boundary that documents
    :class:`ValueError` as its one malformed-payload failure, past every
    caller written to catch that. So the type test has to come first, and it
    has to come first in *both* places.
    """
    if not isinstance(version, str):
        raise ValueError(
            f"document IR version must be a string, got "
            f"{type(version).__name__}"
        )
    if version not in _SUPPORTED_IR_VERSIONS:
        raise ValueError(
            f"unsupported document IR version {version!r}; this release "
            f"{action} {sorted(_SUPPORTED_IR_VERSIONS)}"
        )


@_dataclass(slots=True)
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
    itself, in its own encoding).

    ``version`` names the serialization format, and both directions hold it to
    the set this release can load faithfully (:data:`_SUPPORTED_IR_VERSIONS`):
    :meth:`from_dict` refuses a payload carrying anything else, and
    construction refuses it here — so a document that serialises is a document
    that reloads, the same invariant :attr:`Block.child_type` keeps for
    nested block fields. Checking it at construction rather than in
    :meth:`to_dict` puts the error where the wrong value was chosen, the way
    :class:`~brailix.ir.tactile.TactileRaster` validates its metrics."""

    version: str = _DEFAULT_IR_VERSION
    metadata: dict[str, _Any] = _field(default_factory=dict)
    blocks: list[Block] = _field(default_factory=list)
    assets: dict[str, bytes] = _field(default_factory=dict)

    def __post_init__(self) -> None:
        _check_ir_version(self.version, "writes and reads")

    def to_dict(self) -> dict[str, _Any]:
        return {
            "version": self.version,
            "type": "document",
            "metadata": dict(self.metadata),
            "blocks": [b.to_dict() for b in self.blocks],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, _Any]) -> DocumentIR:
        """Rebuild a document from :meth:`to_dict`'s payload.

        Two things about the payload are checked before any block is read,
        because getting either wrong produces a document that looks fine and
        is not:

        * the root ``type`` must be ``"document"``. :meth:`to_dict` writes it
          and the JSON Schema declares it a constant, but nothing else reads
          it back, so without this check a *block* payload — or any object
          whose shape happens to parse — loads as a document without complaint.
        * ``version`` must be a string, and one this release knows
          (:data:`_SUPPORTED_IR_VERSIONS`). Storing an unknown version
          verbatim and echoing it back out of :meth:`to_dict` — while the
          fields that version added are dropped by
          :func:`block_from_dict` (which skips fields the dataclass does not
          declare — deliberate forward tolerance *within* a known version) —
          yields not a document degraded to 1.0 but a file still claiming to
          be a 2.0 document with its 2.0 content gone. Refusing
          the load says so; a future version arrives with an explicit
          migration, not by silently half-reading.

        Raises :class:`ValueError` — the same failure type as every other
        malformed-payload rejection at this boundary — for **any** shape of
        either, including a ``version`` that is not a string at all
        (:func:`_check_ir_version`). A payload is arbitrary decoded JSON, so
        "the field is present" says nothing about its type; the construction
        below would catch a bad version too, but checking here keeps the
        refusal ahead of reading every block of a document that cannot load.
        """
        _serde.require_payload_type(payload, "document", "document")
        version = payload.get("version", _DEFAULT_IR_VERSION)
        _check_ir_version(version, "loads")
        return cls(
            version=version,
            metadata=_serde.payload_mapping(payload, "metadata", "document"),
            blocks=[
                block_from_dict(b)
                for b in _serde.payload_list(payload, "blocks", "document")
            ],
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


def block_from_dict(payload: dict[str, _Any]) -> Block:
    _serde.require_payload_object(payload, "block")
    type_name = payload.get("type")
    if type_name is None:
        raise ValueError("missing 'type' in block payload")
    cls = block_for(type_name)
    valid = {f.name for f in _fields(cls)}
    kwargs: dict[str, _Any] = {}
    for key, value in payload.items():
        if key == "type":
            continue
        if key not in valid:
            continue
        # Convert first, then hold the CONVERTED value to the field's own
        # declared type: a payload is arbitrary decoded JSON, so a field's
        # presence says nothing about its shape. Without this a
        # ``{"type": "math_block", "source": []}`` built cleanly and raised
        # ``unhashable type: 'list'`` at the adapter registry much later, and
        # ``{"type": "list", "ordered": "false"}`` built a list that was
        # ordered because a non-empty string is truthy.
        kwargs[key] = _serde.check_wire_value(
            cls,
            key,
            _deserialize_block_value(cls, key, value),
            f"{cls.__name__} block",
        )
    return cls(**kwargs)


def _deserialize_block_value(cls: type[Block], key: str, value: _Any) -> _Any:
    """Reconstruct a block-side value from its serialized form.

    ``blocks`` carries typed sub-blocks, and which type is the owning class's
    own declaration (:attr:`Block.child_type`: a List wants ListItems, a
    TableRow wants TableCells, a Table wants TableRows). Each entry's type tag
    is validated so a round-trip can't silently smuggle a Paragraph into a
    row's cells. Mismatches raise :class:`TypeError` naming the offending
    entry's tag, so the serializer / authoring tool can be fixed at the source.

    Read from the declaration rather than from a list of field names: the two
    directions then cannot disagree about what a block's nested blocks are,
    which is how a field could be emitted by one side and unknown to the
    other.
    """
    if key == "span":
        return None if value is None else Span.from_tuple(value)
    if key == "inlines" and isinstance(value, list):
        return [inline_from_dict(v) for v in value]
    if key == "blocks" and isinstance(value, list):
        expected = cls.child_type
        if expected is None:
            raise TypeError(
                f"{cls.__name__} declares no ``child_type``, so a payload "
                f"giving it nested blocks says nothing about what they are. "
                f"Only a container block ({{List, Table, TableRow}}) carries "
                f"``blocks``."
            )
        return [_typed_child(cls, v, expected) for v in value]
    if key == "tree":
        return _serde.deserialize_xml_tree(
            value,
            label=f"{cls.__name__}.tree",
            fmt=getattr(cls, "tree_format", "XML"),
        )
    _serde.reject_unhandled_nested_payload(key, value)
    return value


def _typed_child(
    parent_cls: type[Block], payload: _Any, expected: type[Block]
) -> Block:
    """Deserialize ``payload`` and verify it's an instance of ``expected``.

    Raises :class:`TypeError` rather than silently accepting a mismatched
    child class. Without this, round-trip JSON could carry a Paragraph among a
    TableRow's cells, and the resulting Block tree would type-check at the
    dataclass level but break every downstream consumer that reads them.

    The check itself is :func:`brailix.ir._serde.typed_child`, shared with the
    inline side; this binds it to the block family and its wording.
    """
    return _serde.typed_child(
        payload,
        expected=expected,
        factory=block_from_dict,
        label=f"{parent_cls.__name__}.blocks",
        kind="block",
    )
