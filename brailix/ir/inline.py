"""Inline IR: typed tokens that live inside a block.

Every inline token carries the original surface text and a
:class:`Span` back into the source document, so the renderer can
produce per-cell provenance for proofreading.

Hierarchy:

    InlineNode (abstract)
      ├── Word              # Chinese word, with pinyin
      ├── HanziChar         # single-character fallback
      ├── Number            # numeric literal
      ├── Date              # holds an internal ``parts`` structure
      ├── Quantity          # number + unit
      ├── Percent
      ├── Punct
      ├── LatinWord / LatinAcronym
      ├── CodeInline
      ├── PhoneticInline    # IPA transcription; ``surface`` holds the raw phoneme run
      ├── MathInline        # ``math`` field holds the normalised MathML ET.Element tree
      ├── MusicInline       # ``score`` field holds the normalised MusicXML ET.Element tree
      ├── Space
      ├── Connector         # synthetic connector ⠤: letter↔hanzi compound (x轴 / T恤)
      └── Unknown           # fallback, never lets the pipeline crash

Also defined here:

    Segment       — Segmenter output (chunked by region type)
    ChineseToken  — ChineseAnalyzer output (tokenization + POS + optional pinyin)
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass, field, fields
from typing import Any, ClassVar

from brailix.core._xml import safe_fromstring, strip_namespace
from brailix.core.span import Span

# ---------------------------------------------------------------------------
# Base
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class InlineNode:
    """Abstract base for every inline token type.

    Subclasses set the class-level ``type`` attribute to a stable string
    used for serialization. The :meth:`to_dict` / :meth:`from_dict`
    helpers preserve the tag so a round-trip is lossless.
    """

    type: ClassVar[str] = "inline"
    surface: str = ""
    span: Span | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "surface": self.surface}
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        for f in fields(self):
            if f.name in ("surface", "span"):
                continue
            value = getattr(self, f.name)
            if _is_omittable(value, f.default):
                continue
            d[f.name] = _serialize_value(value)
        return d


# ---------------------------------------------------------------------------
# Concrete types
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Word(InlineNode):
    """A multi-character Chinese word."""

    type: ClassVar[str] = "word"
    reading: str | None = None
    pos: str | None = None
    confidence: float | None = None


@dataclass(slots=True)
class HanziChar(InlineNode):
    """Single-character fallback when tokenization fails to bind a word."""

    type: ClassVar[str] = "hanzi_char"
    reading: str | None = None


@dataclass(slots=True)
class Number(InlineNode):
    """A bare numeric literal. ``role`` is set by structural parents
    (e.g. ``"year"`` inside a :class:`Date`)."""

    type: ClassVar[str] = "number"
    role: str | None = None


@dataclass(slots=True)
class HanziMarker(InlineNode):
    """A single hanzi that plays a structural role inside a composite
    token, e.g. 年/月/日 (year/month/day) inside a :class:`Date`."""

    type: ClassVar[str] = "hanzi_marker"
    reading: str | None = None


@dataclass(slots=True)
class Date(InlineNode):
    """A date expression like ``2026年5月17日``."""

    type: ClassVar[str] = "date"
    parts: list[InlineNode] = field(default_factory=list)


@dataclass(slots=True)
class Quantity(InlineNode):
    """A number paired with a unit, e.g. ``3.5kg``."""

    type: ClassVar[str] = "quantity"
    number: Number | None = None
    unit: str | None = None
    unit_canonical: str | None = None


@dataclass(slots=True)
class Percent(InlineNode):
    """A percentage, e.g. ``12%``."""

    type: ClassVar[str] = "percent"
    number: Number | None = None


@dataclass(slots=True)
class Punct(InlineNode):
    type: ClassVar[str] = "punct"


@dataclass(slots=True)
class LatinWord(InlineNode):
    type: ClassVar[str] = "latin_word"


@dataclass(slots=True)
class LatinAcronym(InlineNode):
    type: ClassVar[str] = "latin_acronym"


@dataclass(slots=True)
class CodeInline(InlineNode):
    type: ClassVar[str] = "code_inline"


@dataclass(slots=True)
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

    type: ClassVar[str] = "phonetic_inline"


@dataclass(slots=True)
class MathInline(InlineNode):
    """Inline math.

    ``math`` carries the normalised MathML tree as an :class:`ET.Element`
    once the math frontend has run; until then it stays ``None`` and only
    the raw surface + source format are recorded.

    The MathML tree itself is the math IR — there is no separate IR
    dataclass (see ``ARCHITECTURE.md``).
    """

    type: ClassVar[str] = "math_inline"
    source: str = "plain"  # latex / mathml / asciimath / plain
    math: ET.Element | None = None


@dataclass(slots=True)
class MusicInline(InlineNode):
    """Inline music. Also the in-children carrier of :class:`ScoreBlock`
    / :class:`MusicBlock` (see ``ARCHITECTURE.md``) — the
    block layer never holds the tree itself, mirroring how
    :class:`MathBlock` defers to :class:`MathInline`.

    ``score`` carries the normalised MusicXML tree as an
    :class:`ET.Element` once the music frontend has run; until then it
    stays ``None`` and only the raw surface + source format are recorded.

    The MusicXML tree itself is the music IR — there is no separate IR
    dataclass.
    """

    type: ClassVar[str] = "music_inline"
    source: str = "plain"  # musicxml / mxl / jianpu / midi / abc / plain
    score: ET.Element | None = None


@dataclass(slots=True)
class Space(InlineNode):
    type: ClassVar[str] = "space"


@dataclass(slots=True)
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

    type: ClassVar[str] = "connector"


@dataclass(slots=True)
class Unknown(InlineNode):
    """Last-resort fallback so the pipeline never crashes on unrecognized
    input."""

    type: ClassVar[str] = "unknown"
    reason: str | None = None


# ---------------------------------------------------------------------------
# Segment (Segmenter output) + ChineseToken (ChineseAnalyzer output)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class Segment:
    """A coarse region produced by a :class:`~brailix.core.protocols.Segmenter`.

    The Segmenter only classifies regions by type (hanzi_text, date,
    number, math_inline, latin_text, punct, ...). Deeper analysis
    (tokenization, pinyin, math parsing) happens later in the pipeline.
    """

    type: str
    surface: str
    span: Span | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"type": self.type, "surface": self.surface}
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        return d


@dataclass(slots=True)
class ChineseToken:
    """A single token emitted by a :class:`~brailix.core.protocols.ChineseAnalyzer`.

    The ``pinyin`` field is initially ``None`` and filled in by a
    :class:`~brailix.core.protocols.PinyinResolver`. The resolver
    must not change the token's surface or span.
    """

    surface: str
    pos: str | None = None
    span: Span | None = None
    pinyin: str | None = None
    confidence: float | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"surface": self.surface}
        if self.pos is not None:
            d["pos"] = self.pos
        if self.span is not None:
            d["span"] = list(self.span.to_tuple())
        if self.pinyin is not None:
            d["pinyin"] = self.pinyin
        if self.confidence is not None:
            d["confidence"] = self.confidence
        return d


# ---------------------------------------------------------------------------
# Registry + (de)serialization
# ---------------------------------------------------------------------------


_INLINE_REGISTRY: dict[str, type[InlineNode]] = {
    cls.type: cls
    for cls in (
        Word,
        HanziChar,
        Number,
        HanziMarker,
        Date,
        Quantity,
        Percent,
        Punct,
        LatinWord,
        LatinAcronym,
        CodeInline,
        PhoneticInline,
        MathInline,
        MusicInline,
        Space,
        Connector,
        Unknown,
    )
}


def inline_node_for(type_name: str) -> type[InlineNode]:
    """Look up the dataclass for an inline node type name."""
    try:
        return _INLINE_REGISTRY[type_name]
    except KeyError as e:
        raise KeyError(f"unknown inline node type: {type_name!r}") from e


def from_dict(payload: dict[str, Any]) -> InlineNode:
    """Reconstruct an :class:`InlineNode` from its dict representation.

    Composite types like :class:`Date` recursively deserialize their
    ``parts`` / ``number`` children.
    """
    type_name = payload.get("type")
    if type_name is None:
        raise ValueError("missing 'type' in inline payload")
    cls = inline_node_for(type_name)
    kwargs: dict[str, Any] = {}
    valid_field_names = {f.name for f in fields(cls)}
    for key, value in payload.items():
        if key == "type":
            continue
        if key not in valid_field_names:
            continue
        kwargs[key] = _deserialize_value(key, value)
    return cls(**kwargs)


# --- helpers ---------------------------------------------------------


def _strip_xml_namespace(elem: ET.Element) -> ET.Element:
    """Drop ``{namespace}`` Clark-notation prefixes (in place) and return
    ``elem`` for chaining.

    The IR round-trip serializes a math / score tree with ``ET.tostring``
    and re-parses it with ``ET.fromstring``; if the producer left an
    ``xmlns`` attribute on the root, the reparse rewrites every tag to
    Clark notation and the backend — which dispatches on bare local names —
    fails to match, yielding blank cells + spurious warnings. Stripping at
    the IR boundary keeps the *string* round-trip (``ET.tostring`` →
    ``ET.fromstring``) lossless. (A pre-parsed ``ET.Element`` handed straight
    to ``from_dict`` is passed through un-stripped — that path stays correct
    because the frontend normalizers already emit bare-tag trees, not because
    of this guard.) Delegates to the shared
    :func:`brailix.core._xml.strip_namespace` (a core helper, so the IR
    layer takes no frontend dependency); this thin wrapper just returns
    ``elem`` so the deserializer can strip-and-return in one expression.
    """
    strip_namespace(elem)
    return elem


def _is_omittable(value: Any, default: Any) -> bool:
    """True if ``value`` should be omitted from a ``to_dict`` payload: it is
    ``None``, equal to its field ``default``, or an empty sequence.

    (``default_factory`` list/tuple fields report ``default`` == MISSING, so the
    equality check alone misses an empty list — the explicit empty-sequence test
    covers them.) Shared by the inline and block ``to_dict`` field loops.
    """
    return (
        value is None
        or value == default
        or (isinstance(value, (list, tuple)) and not value)
    )


def _serialize_value(value: Any) -> Any:
    if isinstance(value, InlineNode):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, Span):
        return list(value.to_tuple())
    # MathInline.math is an ``ET.Element`` — serialize as a MathML
    # string. JSON consumers see a plain string; reading code goes
    # through :func:`ET.fromstring` (see ``_deserialize_value``).
    if isinstance(value, ET.Element):
        return ET.tostring(value, encoding="unicode")
    return value


# Maps each XML-tree field to (qualified field label, human format name) for the
# "must be None / a <fmt> string / an ET.Element" error message.
_XML_TREE_FIELDS: dict[str, tuple[str, str]] = {
    "math": ("MathInline.math", "MathML"),
    "score": ("MusicInline.score", "MusicXML"),
}


def _deserialize_xml_tree(key: str, value: Any) -> ET.Element | None:
    """Deserialize a MathML / MusicXML tree field (``math`` / ``score``).

    Accepts ``None`` (kept), a serialized XML string (re-parsed and namespace-
    stripped at the IR boundary), or a pre-parsed :class:`ET.Element` (passed
    through unchanged). A wrong type — or a string that isn't well-formed XML
    (``ET.ParseError`` is re-raised as :class:`ValueError`) — fails loudly at
    the IR boundary as a :class:`ValueError` instead of silently storing junk.
    """
    if value is None:
        return None
    field_label, fmt = _XML_TREE_FIELDS[key]
    if isinstance(value, str):
        try:
            parsed = safe_fromstring(value)
        except ET.ParseError as e:
            raise ValueError(f"{field_label} is not well-formed {fmt}: {e}") from e
        return _strip_xml_namespace(parsed)
    if isinstance(value, ET.Element):
        return value
    raise ValueError(
        f"{field_label} must be None, a {fmt} string, or an ET.Element; "
        f"got {type(value).__name__}"
    )


def _reject_unhandled_nested_payload(key: str, value: Any) -> None:
    """Guard the deserializer fall-through against an IR payload nobody rebuilt.

    Serialization is type-driven — :func:`_serialize_value` recurses into any
    :class:`InlineNode` automatically. Deserialization dispatches on field
    *name*, so a newly added IR-node-valued field serializes correctly yet
    would fall through here and be stored as a raw ``dict`` (or list of
    ``dict``): a silent round-trip corruption no per-type test catches.

    A serialized IR node is always a ``dict`` and a list of them a list of
    ``dict``; every scalar / span / XML-tree field deserializes from something
    else (a span is a 2-int list, ``math`` / ``score`` a string). So a
    fall-through ``dict`` or list-of-``dict`` means exactly "an IR-payload
    field nobody registered" — raise so the omission surfaces at its source
    instead of corrupting the tree. Mirrors the loud-drop guard on the
    ``to_dict`` side (:func:`brailix.ir.document._is_ir_payload`).
    """
    if isinstance(value, dict) or (
        isinstance(value, list) and any(isinstance(v, dict) for v in value)
    ):
        raise ValueError(
            f"field {key!r} carries a nested IR payload but has no "
            f"deserialization branch; register it in the deserializer — "
            f"serialization is type-driven while deserialization dispatches "
            f"on field name, so the two must be kept in sync"
        )


def _deserialize_value(key: str, value: Any) -> Any:
    if key == "span":
        return None if value is None else Span.from_tuple(value)
    if key == "parts" and isinstance(value, list):
        return [from_dict(v) for v in value]
    if key == "number" and isinstance(value, dict):
        return from_dict(value)
    if key in ("math", "score"):
        return _deserialize_xml_tree(key, value)
    _reject_unhandled_nested_payload(key, value)
    return value
