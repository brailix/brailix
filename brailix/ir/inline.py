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
            if (
                value is None
                or value == f.default
                # ``default_factory=list`` fields have ``f.default`` == MISSING
                # (so the ``== f.default`` check misses an empty list); skip
                # any empty sequence so they don't bloat the JSON.
                or (isinstance(value, (list, tuple)) and not value)
            ):
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
    """Drop any ``{namespace}local`` Clark-notation prefix from every tag
    in ``elem`` (in place) and return it.

    The IR round-trip serializes a math / score tree with ``ET.tostring``
    and re-parses it with ``ET.fromstring``; if the producer left an
    ``xmlns`` attribute on the root, the reparse rewrites every tag to
    Clark notation and the backend — which dispatches on bare local names —
    fails to match, yielding blank cells + spurious warnings. Stripping at
    the IR boundary keeps the round-trip lossless no matter how the tree was
    built. Kept local (not imported from ``frontend._xml``) so the IR layer
    takes no dependency on the frontend package.
    """
    if elem.tag.startswith("{"):
        close = elem.tag.find("}")
        if close != -1:
            elem.tag = elem.tag[close + 1:]
    for child in elem:
        _strip_xml_namespace(child)
    return elem


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


def _deserialize_value(key: str, value: Any) -> Any:
    if key == "span" and isinstance(value, (list, tuple)) and len(value) == 2:
        return Span(int(value[0]), int(value[1]))
    if key == "parts" and isinstance(value, list):
        return [from_dict(v) for v in value]
    if key == "number" and isinstance(value, dict):
        return from_dict(value)
    if key == "math":
        if value is None:
            return None
        if isinstance(value, str):
            return _strip_xml_namespace(ET.fromstring(value))
        if isinstance(value, ET.Element):
            return value
        raise ValueError(
            "MathInline.math must be None, a MathML string, or an "
            "ET.Element; got " + type(value).__name__
        )
    if key == "score":
        if value is None:
            return None
        if isinstance(value, str):
            return _strip_xml_namespace(ET.fromstring(value))
        if isinstance(value, ET.Element):
            return value
        raise ValueError(
            "MusicInline.score must be None, a MusicXML string, or an "
            "ET.Element; got " + type(value).__name__
        )
    return value
