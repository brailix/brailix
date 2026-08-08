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
      ├── Date              # holds an internal ``components`` structure
      ├── Punct
      ├── LatinWord         # Latin / Greek letter run (all-caps included)
      ├── CodeInline
      ├── PhoneticInline    # IPA transcription; ``surface`` holds the raw phoneme run
      ├── MathInline        # ``math`` field holds the normalised MathML ET.Element tree
      ├── Space
      ├── Connector         # synthetic connector ⠤: letter↔hanzi compound (x轴 / T恤)
      └── Unknown           # fallback, never lets the pipeline crash

A composite node's internals are **value objects, not nodes**:
:class:`DateComponent` is a plain record :class:`Date` holds, the way
:class:`~brailix.core.span.Span` is a record every node holds. Only something a
consumer dispatches on independently earns a place in the list above.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from dataclasses import fields as _fields
from typing import Any as _Any
from typing import ClassVar as _ClassVar

from brailix.core.span import Span, merge_spans
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


@_dataclass(frozen=True, slots=True)
class DateComponent:
    """One ``<digits><marker>`` unit of a :class:`Date`: ``2026年``, ``5月``.

    A **value object**, not an :class:`InlineNode`, and the difference is the
    point. The three markers used to be a public ``HanziMarker`` node type, so
    a date was a node whose ``parts`` was a ``list[InlineNode]`` that could in
    principle hold anything, and the marker paid the full price of nodehood —
    an entry in the inline registry, a wire tag, a slot in the public facade, a
    recursive typed-child deserializer for ``parts`` — for a thing that has no
    independent existence. There is no such thing as a loose 年: it is only
    ever the second half of a date component, and nothing dispatches on it. So
    it is a **field** of the unit it belongs to, and a date's structure is
    fixed by its own declaration rather than by a runtime check on a list of
    arbitrary nodes.

    ``digits`` and ``marker`` keep separate spans because they are separate
    runs of source text, and the backend needs both: the digits go through the
    number-sign + digit pipeline, the marker through the profile language's
    ``translate_date_marker``. ``reading`` is the marker's — the digits have no
    reading. ``marker`` is ``None`` for a trailing bare-number component; the
    digits may be empty for a marker with nothing in front of it. Both halves
    being optional is what lets the component stay the whole model of a date's
    structure instead of one shape among several.
    """

    digits: str = ""
    digits_span: Span | None = None
    marker: str | None = None
    marker_span: Span | None = None
    # The *marker*'s reading (年 → nián), filled by the frontend that built the
    # date; the digits' pronunciation is the digit table's business.
    reading: str | None = None

    @property
    def surface(self) -> str:
        """The source text this component was written as (``"2026年"``)."""
        return f"{self.digits}{self.marker or ''}"

    @property
    def span(self) -> Span | None:
        """The component's extent, or ``None`` when neither half is located.

        Named ``span`` so a component answers the same question an inline node
        does: the backend's traceability post-condition
        (:func:`brailix.backend.dispatch._enforce_source_spans`) is applied at
        the ``translate_date_marker`` boundary too, and it reads this.
        """
        return merge_spans(
            s for s in (self.digits_span, self.marker_span) if s is not None
        )

    def to_dict(self) -> dict[str, _Any]:
        d: dict[str, _Any] = {"digits": self.digits}
        if self.digits_span is not None:
            d["digits_span"] = list(self.digits_span.to_tuple())
        if self.marker is not None:
            d["marker"] = self.marker
        if self.marker_span is not None:
            d["marker_span"] = list(self.marker_span.to_tuple())
        if self.reading is not None:
            d["reading"] = self.reading
        return d


@_dataclass(slots=True)
class Date(InlineNode):
    """A date expression like ``2026年5月17日``, as a list of
    :class:`DateComponent`\\ s (``2026年`` / ``5月`` / ``17日``).

    The node stays a node — the backend does make date-specific decisions the
    parts could not express on their own (the blank between components, the
    number→marker connector, routing the marker through the language backend)
    — but what it holds is records, not more nodes.
    """

    type: _ClassVar[str] = "date"
    components: list[DateComponent] = _field(default_factory=list)


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
    """Inline math — a ``$...$`` fragment inside a run of prose.

    ``tree`` carries the normalised MathML as an :class:`ET.Element` once the
    math frontend has run; until then it stays ``None`` and only the raw
    surface + source format are recorded. Same field name as an
    :class:`~brailix.ir.document.EmbeddedBlock`'s, because it is the same
    thing: one rule for where a parsed domain tree lives.

    The MathML tree itself is the math IR — there is no separate IR
    dataclass.

    The **only** inline node that carries a domain tree, and it earns it: a
    formula genuinely appears inside a sentence, between a Word and a Punct,
    and the dispatcher routes it there like any other token. There were three
    such types — ``MusicInline`` and ``GraphicInline`` beside it — but neither
    of those was ever produced by a frontend at all: they existed only to
    carry a *block*'s tree in a one-element ``children`` list. The block owns
    its tree now (:class:`~brailix.ir.document.EmbeddedBlock`), so there is no
    carrier left to be.
    """

    type: _ClassVar[str] = "math_inline"
    source: str = "plain"  # latex / mathml / plain
    tree: _ET.Element | None = None


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
        Date,
        Punct,
        LatinWord,
        CodeInline,
        PhoneticInline,
        MathInline,
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
    produced it" — but no file this library reads carries inline IR. A
    front-end that persists a project keeps the source text and its overrides
    and recompiles the IR on open; the proofread JSON is a write-only export.
    The two tags therefore
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

    Composite types like :class:`Date` rebuild their ``components`` records
    on the way through.
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


def _serialize_value(value: _Any) -> _Any:
    if isinstance(value, (InlineNode, DateComponent)):
        return value.to_dict()
    if isinstance(value, list):
        return [_serialize_value(v) for v in value]
    if isinstance(value, Span):
        return list(value.to_tuple())
    # MathInline.tree is an ``ET.Element`` — serialized as MathML text. JSON
    # consumers see a plain string; reading code goes back through the shared
    # loader (see ``_deserialize_value``).
    if isinstance(value, _ET.Element):
        return _serde.serialize_xml_tree(value)
    return value


_DATE_COMPONENT_FIELDS: frozenset[str] = frozenset(
    f.name for f in _fields(DateComponent)
)


def _date_component(payload: _Any) -> DateComponent:
    """Rebuild one :class:`DateComponent` from its serialized form.

    A flat record, so this is a flat loader: no registry lookup, no type tag,
    no recursion. That is the whole difference from what ``Date.parts`` needed
    while the markers were nodes — there, each entry's own payload chose which
    class to build, so a ``{"type": "date", "parts": ["17日"]}`` could put a
    bare string (or a Table, or a formula) where every consumer walks date
    parts, and a typed-child check had to say no at runtime. Here the shape is
    the declaration's, and the only thing a payload gets to choose is the
    values.

    An already-built component passes through, so a caller assembling a date in
    code can hand the record over rather than its dict form — the same courtesy
    :func:`brailix.ir._serde.typed_child` extends on the node side. Each field
    is held to its declared type (:func:`~brailix.ir._serde.check_wire_value`),
    and unknown keys are ignored, matching the forward tolerance
    :func:`from_dict` gives every node.
    """
    if isinstance(payload, DateComponent):
        return payload
    mapping = _serde.require_payload_object(payload, "date component")
    kwargs: dict[str, _Any] = {}
    for key, value in mapping.items():
        if key not in _DATE_COMPONENT_FIELDS:
            continue
        converted = (
            (None if value is None else Span.from_tuple(value))
            if key.endswith("_span")
            else value
        )
        kwargs[key] = _serde.check_wire_value(
            DateComponent, key, converted, "date component"
        )
    return DateComponent(**kwargs)


def _deserialize_value(key: str, value: _Any) -> _Any:
    if key == "span":
        return None if value is None else Span.from_tuple(value)
    if key == "components" and isinstance(value, list):
        return [_date_component(v) for v in value]
    if key == "tree":
        return _serde.deserialize_xml_tree(
            value, label="MathInline.tree", fmt="MathML"
        )
    _serde.reject_unhandled_nested_payload(key, value)
    return value
