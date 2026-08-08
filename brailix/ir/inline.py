"""Inline IR: typed tokens that live inside a block.

Every inline token carries the original surface text and a :class:`Span`,
which is what lets the renderer produce per-cell provenance for proofreading.

That span is **leaf-local** — offsets into the owning leaf block's own
``text``, starting at 0 in every block, not coordinates in the source document
(ARCHITECTURE#arch-spans; :mod:`brailix.ir.document` spells the two-level
scheme out in full, table rows included). ``Block.span`` is what locates a block in its source; where
that block upholds the exact-slice contract, ``block.span.start + leaf_local``
is the exact source position. The distinction is not wording: a consumer that
treats these offsets as document ones — an editor scrolling to a warning, a
click-to-source jump — navigates to the wrong place in every block but the
first, and formats like ``.docx`` have no document-wide character coordinate
for it to be right about in the first place.

Hierarchy — one level, every type a direct subclass. There is no inline node
that specialises another; a composite (:class:`Date`) *contains* nodes rather
than inheriting from one, which is why the backend can dispatch on the exact
type through a flat table:

    InlineNode (abstract)
      ├── Word              # prose word (any length), with its reading
      ├── Number            # numeric literal
      ├── HanziMarker       # structural hanzi inside a composite (年/月/日 in a Date)
      ├── Date              # holds an internal ``parts`` structure
      ├── Punct
      ├── LatinWord         # Latin / Greek letter run (all-caps included)
      ├── CodeInline
      ├── PhoneticInline    # IPA transcription; ``surface`` holds the raw phoneme run
      ├── MathInline        # ``math`` field holds the normalised MathML ET.Element tree
      ├── MusicInline       # ``score`` field holds the normalised MusicXML ET.Element tree
      ├── GraphicInline     # ``svg`` field holds the normalised SVG ET.Element tree (graphics IR carrier)
      ├── Space
      ├── Connector         # synthetic connector ⠤: letter↔hanzi compound (x轴 / T恤)
      └── Unknown           # fallback, never lets the pipeline crash
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from dataclasses import fields as _fields
from typing import Any as _Any
from typing import ClassVar as _ClassVar

from brailix.core._xml import safe_fromstring, strip_namespace
from brailix.core.span import Span
from brailix.ir import _serde

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@_dataclass(slots=True)
class InlineNode:
    """Abstract base for every inline token type.

    Subclasses set the class-level ``type`` attribute to a stable string
    used for serialization. The :meth:`to_dict` / :meth:`from_dict`
    helpers preserve the tag so a round-trip is lossless.
    """

    type: _ClassVar[str] = "inline"
    surface: str = ""
    span: Span | None = None

    def to_dict(self) -> dict[str, _Any]:
        d: dict[str, _Any] = {"type": self.type, "surface": self.surface}
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        for f in _fields(self):
            if f.name in ("surface", "span"):
                continue
            value = getattr(self, f.name)
            if _serde.is_omittable(value, f.default):
                continue
            d[f.name] = _serialize_value(value)
        return d


# ---------------------------------------------------------------------------
# Concrete types
# ---------------------------------------------------------------------------


@_dataclass(slots=True)
class Word(InlineNode):
    """A prose word, of any length, in whichever language produced it.

    Not Chinese-specific: the Japanese frontend turns its analysed tokens into
    ``Word`` nodes too, and ``reading`` carries whatever phonetic annotation
    that language uses — pinyin for Chinese, kana for Japanese. That is the
    point of the shared prose IR: a new language registers a frontend and a
    backend, and reuses these nodes rather than adding its own.

    **A single character is just a one-character word.** There is no separate
    single-character node, because there is nothing for one to carry that this
    does not: both language backends translate a lone character through the
    identical call, and "shorter than two characters" is what ``surface``
    says. A second type would cost a second required method on
    :class:`~brailix.core.protocols.LanguageBackend` for every language, and a
    two-type tuple in every consumer that discriminates — where writing half
    of it silently skips single characters.
    """

    type: _ClassVar[str] = "word"
    reading: str | None = None


@_dataclass(slots=True)
class Number(InlineNode):
    """A bare numeric literal."""

    type: _ClassVar[str] = "number"


@_dataclass(slots=True)
class HanziMarker(InlineNode):
    """A single hanzi that plays a structural role inside a composite
    token, e.g. 年/月/日 (year/month/day) inside a :class:`Date`."""

    type: _ClassVar[str] = "hanzi_marker"
    reading: str | None = None


@_dataclass(slots=True)
class Date(InlineNode):
    """A date expression like ``2026年5月17日``."""

    type: _ClassVar[str] = "date"
    parts: list[InlineNode] = _field(default_factory=list)


@_dataclass(slots=True)
class Punct(InlineNode):
    type: _ClassVar[str] = "punct"


@_dataclass(slots=True)
class LatinWord(InlineNode):
    type: _ClassVar[str] = "latin_word"


@_dataclass(slots=True)
class CodeInline(InlineNode):
    type: _ClassVar[str] = "code_inline"


@_dataclass(slots=True)
class PhoneticInline(InlineNode):
    """An IPA phonetic transcription region (English pronunciation).

    ``surface`` carries the raw phoneme run *without* the delimiters the
    author wrote it in (``/həˈləʊ/`` and ``[həˈləʊ]`` both store
    ``"həˈləʊ"``). There is no tree: a transcription is a flat sequence
    of phonemes, so — like :class:`CodeInline` — the node just holds the
    text and the backend (:mod:`brailix.backend.phonetic`) does the work,
    greedily matching each phoneme against the profile's phonetic table
    (longest first, so ``tʃ`` and ``eɪ`` win over ``t`` / ``e``).
    """

    type: _ClassVar[str] = "phonetic_inline"


@_dataclass(slots=True)
class MathInline(InlineNode):
    """Inline math.

    ``math`` carries the normalised MathML tree as an :class:`ET.Element`
    once the math frontend has run; until then it stays ``None`` and only
    the raw surface + source format are recorded.

    The MathML tree itself is the math IR — there is no separate IR
    dataclass.
    """

    type: _ClassVar[str] = "math_inline"
    source: str = "plain"  # latex / mathml / plain
    math: _ET.Element | None = None


@_dataclass(slots=True)
class MusicInline(InlineNode):
    """Inline music. Also the in-children carrier of :class:`ScoreBlock`
    / :class:`MusicBlock` — the
    block layer never holds the tree itself, mirroring how
    :class:`MathBlock` defers to :class:`MathInline`.

    ``score`` carries the normalised MusicXML tree as an
    :class:`ET.Element` once the music frontend has run; until then it
    stays ``None`` and only the raw surface + source format are recorded.

    The MusicXML tree itself is the music IR — there is no separate IR
    dataclass.
    """

    type: _ClassVar[str] = "music_inline"
    source: str = "plain"  # musicxml / mxl / midi / abc / plain
    score: _ET.Element | None = None


@_dataclass(slots=True)
class GraphicInline(InlineNode):
    """In-children carrier of :class:`~brailix.ir.document.GraphicBlock`,
    mirroring how :class:`MathInline` carries a :class:`MathBlock`'s tree
    and :class:`MusicInline` a score's.

    ``svg`` carries the normalised SVG tree as an :class:`ET.Element` once
    the graphics frontend has run; until then it stays ``None`` and only the
    raw surface + source format are recorded. The SVG tree itself is the
    graphics IR — there is no separate vector model, exactly as MathML is the
    math IR and MusicXML the music IR.

    Unlike :class:`MathInline` / :class:`MusicInline`, this node is **not** on
    the braille dispatch table: a tactile graphic does not translate to braille
    cells. It is rasterised to a :class:`~brailix.ir.tactile.TactileRaster` by
    :meth:`~brailix.pipeline.Pipeline.translate_graphic` via the tactile
    backend; this node is only the tree carrier between frontend and that
    backend.
    """

    type: _ClassVar[str] = "graphic_inline"
    source: str = "svg"  # svg / primitives / figure / image
    svg: _ET.Element | None = None


@_dataclass(slots=True)
class Space(InlineNode):
    type: _ClassVar[str] = "space"


@_dataclass(slots=True)
class Connector(InlineNode):
    """Synthetic connector (hyphen sign ⠤) joining a Latin/Greek letter
    to an adjacent hanzi when the two form a single compound word
    (``x轴`` / ``T恤`` / ``维生素C``).

    Distinct from :class:`Space`: a Space marks a *word boundary* (one
    blank cell, the NCB "tokenize-and-join" word-spacing rule); a
    Connector marks a *within-word* script transition in a letter+hanzi
    compound — the two characters belong to one word, so they get a
    connector instead of a gap. The frontend's
    :func:`brailix.frontend.zh.insert_cross_kind_boundary_spaces`
    decides which to emit (compound-lexicon hit → Connector, else
    Space); the backend renders this as the profile's ``connector``
    cell. Both carry an empty ``surface`` and a zero-width span at the
    boundary so proofread tooling treats the two synthetic separators
    uniformly."""

    type: _ClassVar[str] = "connector"


@_dataclass(slots=True)
class Unknown(InlineNode):
    """Last-resort fallback so the pipeline never crashes on unrecognized
    input."""

    type: _ClassVar[str] = "unknown"
    reason: str | None = None


# ---------------------------------------------------------------------------
# Registry + (de)serialization
# ---------------------------------------------------------------------------


_INLINE_REGISTRY: dict[str, type[InlineNode]] = {
    cls.type: cls
    for cls in (
        Word,
        Number,
        HanziMarker,
        Date,
        Punct,
        LatinWord,
        CodeInline,
        PhoneticInline,
        MathInline,
        MusicInline,
        GraphicInline,
        Space,
        Connector,
        Unknown,
    )
}


def inline_node_for(type_name: str) -> type[InlineNode]:
    """Look up the dataclass for an inline node type name.

    Retired tags do **not** resolve. There was a compatibility table here
    mapping ``hanzi_char`` to :class:`Word` and ``latin_acronym`` to
    :class:`LatinWord`, justified by "a project file outlives the schema that
    produced it" — but no file this library reads carries inline IR. A ``.blx``
    project stores the source text and its overrides and recompiles the IR on
    open; the proofread JSON is a write-only export. The two tags therefore
    bridged nothing, which is also why retiring ``Quantity`` and ``Percent``
    added no entries: the table had stopped describing "retired" some time
    before it was removed.
    """
    try:
        return _INLINE_REGISTRY[type_name]
    except KeyError as e:
        raise KeyError(f"unknown inline node type: {type_name!r}") from e


def from_dict(payload: dict[str, _Any]) -> InlineNode:
    """Reconstruct an :class:`InlineNode` from its dict representation.

    Composite types like :class:`Date` recursively deserialize their
    ``parts`` / ``number`` children.
    """
    _serde.require_payload_object(payload, "inline")
    type_name = payload.get("type")
    if type_name is None:
        raise ValueError("missing 'type' in inline payload")
    cls = inline_node_for(type_name)
    kwargs: dict[str, _Any] = {}
    valid_field_names = {f.name for f in _fields(cls)}
    for key, value in payload.items():
        if key == "type":
            continue
        if key not in valid_field_names:
            continue
        # Convert, then hold the converted value to the field's declared type
        # — the same wire-shape guard the block side applies, for the same
        # reason: unchecked, ``{"type": "word", "reading": []}`` builds a Word
        # whose ``reading`` is a list, and every consumer downstream believes
        # the annotation.
        kwargs[key] = _serde.check_wire_value(
            cls, key, _deserialize_value(key, value), f"{cls.__name__} node"
        )
    return cls(**kwargs)


# --- helpers ---------------------------------------------------------


def _strip_xml_namespace(elem: _ET.Element) -> _ET.Element:
    """Drop ``{namespace}`` Clark-notation prefixes (in place) and return
    ``elem`` for chaining.

    The IR round-trip serializes a math / score tree with ``ET.tostring``
    and re-parses it with ``ET.fromstring``; if the producer left an
    ``xmlns`` attribute on the root, the reparse rewrites every tag to
    Clark notation and the backend — which dispatches on bare local names —
    fails to match, yielding blank cells + spurious warnings. Stripping at
    the IR boundary keeps the *string* round-trip (``ET.tostring`` →
    ``ET.fromstring``) lossless, and a pre-parsed ``ET.Element`` gets the
    same treatment: what the deserializer stores is a bare-tag tree, whichever
    of the two legal shapes the same XML arrived in. Delegates to the shared
    :func:`brailix.core._xml.strip_namespace` (a core helper, so the IR
    layer takes no frontend dependency); this thin wrapper just returns
    ``elem`` so the deserializer can strip-and-return in one expression.
    """
    strip_namespace(elem)
    return elem


def _serialize_value(value: _Any) -> _Any:
    if isinstance(value, InlineNode):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, Span):
        return list(value.to_tuple())
    # MathInline.math is an ``ET.Element`` — serialize as a MathML
    # string. JSON consumers see a plain string; reading code goes
    # through :func:`ET.fromstring` (see ``_deserialize_value``).
    if isinstance(value, _ET.Element):
        return _ET.tostring(value, encoding="unicode")
    return value


# Maps each XML-tree field to (qualified field label, human format name) for the
# "must be None / a <fmt> string / an ET.Element" error message.
_XML_TREE_FIELDS: dict[str, tuple[str, str]] = {
    "math": ("MathInline.math", "MathML"),
    "score": ("MusicInline.score", "MusicXML"),
    "svg": ("GraphicInline.svg", "SVG"),
}


def _deserialize_xml_tree(key: str, value: _Any) -> _ET.Element | None:
    """Deserialize a MathML / MusicXML tree field (``math`` / ``score``).

    Accepts ``None`` (kept), a serialized XML string (re-parsed with the safe
    parser), or a pre-parsed :class:`ET.Element`. **Either way** the stored
    tree is namespace-stripped — both shapes, not just the string one: the
    backend dispatches on bare local names, so a
    ``{http://www.w3.org/1998/Math/MathML}mi`` matches nothing and degrades to
    a blank cell plus a misleading "unsupported element" warning. Strip one
    shape only and the *same* namespaced XML compiles to braille through one
    argument type and to nothing through the other.

    The Element is normalized in place and returned, not copied. This branch
    exists to skip a serialize / re-parse round trip for a caller that already
    holds the tree, and deep-copying a full score's tree would hand most of
    that cost back; the node aliases the caller's Element either way, so a
    copy would not be buying isolation it does not already lack. Stripping is
    idempotent, so an already-bare tree — what every in-tree frontend
    produces — is walked once and left alone.

    A wrong type — or a string that isn't well-formed XML (``ET.ParseError``
    is re-raised as :class:`ValueError`) — fails loudly at the IR boundary as
    a :class:`ValueError` instead of silently storing junk.
    """
    if value is None:
        return None
    field_label, fmt = _XML_TREE_FIELDS[key]
    if isinstance(value, str):
        try:
            parsed = safe_fromstring(value)
        except _ET.ParseError as e:
            raise ValueError(f"{field_label} is not well-formed {fmt}: {e}") from e
        return _strip_xml_namespace(parsed)
    if isinstance(value, _ET.Element):
        return _strip_xml_namespace(value)
    raise ValueError(
        f"{field_label} must be None, a {fmt} string, or an ET.Element; "
        f"got {type(value).__name__}"
    )


def _typed_inline_child(
    field_name: str, payload: _Any, expected: type[InlineNode]
) -> InlineNode:
    """Deserialize ``payload`` and verify it is an instance of ``expected``.

    ``Date.parts`` is declared ``list[InlineNode]``, but the deserializer
    dispatches on the field *name* and rebuilds whatever each entry's payload
    said it was — so ``{"type": "date", "parts": ["17日"]}`` would otherwise
    put a bare string where every consumer walks nodes.

    The check itself is :func:`brailix.ir._serde.typed_child`, shared with the
    block side; this binds it to the inline node family and its wording.
    """
    return _serde.typed_child(
        payload,
        expected=expected,
        factory=from_dict,
        label=f"inline field {field_name!r}",
        kind="node",
    )


def _deserialize_value(key: str, value: _Any) -> _Any:
    if key == "span":
        return None if value is None else Span.from_tuple(value)
    if key == "parts" and isinstance(value, list):
        # ``Date.parts`` is declared ``list[InlineNode]``, so any node type is
        # legal there — what the check adds is that each entry IS a node.
        return [_typed_inline_child(key, v, InlineNode) for v in value]
    if key in ("math", "score", "svg"):
        return _deserialize_xml_tree(key, value)
    _serde.reject_unhandled_nested_payload(key, value)
    return value
