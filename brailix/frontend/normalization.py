"""Default normalizer: convert raw Segments into typed InlineNodes.

The normalizer is the bridge between the dumb character-class
Segmenter and the rest of the frontend. It recognizes structural
patterns and turns them into proper InlineNodes:

* ``2026年5月17日`` → :class:`Date` with year/month/day parts
* bare ``2026`` → :class:`Number`
* protected math_inline segments → atomic InlineNodes
* punctuation / space / latin segments → atomic InlineNodes

Anything left over (notably ``hanzi_text``) stays a Segment so the
downstream ChineseAnalyzer can tokenize it.

The normalizer never crashes on weird input; unrecognized patterns
fall through to :class:`Unknown`.

Deliberately **not** composites: ``3.5kg`` and ``12%``. Each had a node type of
its own and neither changed a single braille cell by having one. The blank
that sets a quantity off from adjacent Chinese comes from the latin↔hanzi
boundary rule, which a non-unit run like ``3.5foo`` gets too, so ``3.5kg`` is a
:class:`Number` beside a :class:`LatinWord` and there is no table here of which
latin runs count as units. A percentage's blank was the last one with no
second source, and it now has one: ``%`` carries ``space_after`` in the
punctuation table, where the rest of its orthography already lived. Both are
pinned in ``tests/golden/data/numbers.json``.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core import inline_math
from brailix.core.context import FrontendContext
from brailix.core.protocols import Normalizer
from brailix.core.registry import Registry
from brailix.core.span import Span
from brailix.frontend._language_pick import pick_by_language
from brailix.ir.inline import (
    Date,
    HanziMarker,
    InlineNode,
    LatinWord,
    MathInline,
    Number,
    PhoneticInline,
    Punct,
    Segment,
    Space,
    Unknown,
)

if _TYPE_CHECKING:
    from collections.abc import Iterable

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# ASCII characters routed through the math backend that have no HTML5
# entity (so the symbols table — keyed by entity name resolved to a
# Unicode char — can't store them directly). We rewrite ``<mo>`` text
# to the canonical Unicode math char before the backend looks it up;
# the :class:`MathInline`'s ``surface`` stays as the original char so
# proofread tooling still highlights what the author actually typed.
_MATH_OP_CANONICAL: dict[str, str] = {
    "-": "−",  # U+002D hyphen-minus → U+2212 (matches `minus;` entity)
}

# ---------------------------------------------------------------------------
# Output type alias
# ---------------------------------------------------------------------------

NormalizedItem = InlineNode | Segment


# ---------------------------------------------------------------------------
# Normalizer
# ---------------------------------------------------------------------------


@_dataclass(slots=True)
class DefaultNormalizer:
    """Built-in normalizer with no third-party dependencies."""

    name: str = "default"

    def normalize(
        self,
        segments: Iterable[Segment],
        ctx: FrontendContext | None = None,
    ) -> list[NormalizedItem]:
        segs: list[Segment] = list(segments)
        out: list[NormalizedItem] = []
        i = 0
        while i < len(segs):
            seg = segs[i]
            # Composite patterns (multi-segment) — try longest first.
            # The variable is annotated as the broadest union so reusing
            # it for the date / dash probes type-checks; each probe
            # returns its own concrete InlineNode subtype.
            consumed: tuple[InlineNode, int] | None = _try_date(segs, i)
            if consumed is not None:
                node, next_i = consumed
                out.append(node)
                i = next_i
                continue
            consumed = _try_dash(segs, i)
            if consumed is not None:
                node, next_i = consumed
                out.append(node)
                i = next_i
                continue

            # Atomic conversions.
            atomic = _try_atomic(seg)
            if atomic is not None:
                out.append(atomic)
            else:
                # hanzi_text and unknown pass through unchanged.
                out.append(seg)
            i += 1
        return out


# ---------------------------------------------------------------------------
# Composite-pattern matchers
# ---------------------------------------------------------------------------


# Canonical pinyin for the structural hanzi markers.  Only 年/月/日 are
# currently emitted (by ``_try_date``); 号/时/点/分/秒 are reserved for a
# future clock matcher (the backend already speaks ``reading``, so they
# need no further wiring).  Filling this here keeps the backend language-
# agnostic — it just reads the IR.  These are *fixed* readings (always
# 年→nián, 月→yuè), not context-sensitive polyphone resolution; the latter
# is still the PinyinResolver's job and is intentionally not done here.
_MARKER_PINYIN: dict[str, str] = {
    "年": "nian2",
    "月": "yue4",
    "日": "ri4",
    "号": "hao4",
    "时": "shi2",
    "点": "dian3",
    "分": "fen1",
    "秒": "miao3",
}


def _marker(char: str, span: Span | None) -> HanziMarker:
    """Build a HanziMarker pre-annotated with its canonical reading.

    ``span`` is widened to ``Span | None`` to match the source segment's
    own optional span; :class:`HanziMarker` already permits a missing
    span at the dataclass level, so no extra handling is needed here.
    """
    return HanziMarker(surface=char, span=span, reading=_MARKER_PINYIN.get(char))


def _span_range(
    start_span: Span | None, end_span: Span | None
) -> Span | None:
    """Return ``Span(start_span.start, end_span.end)`` when both endpoints
    are present, else ``None``.

    Composite InlineNodes (Date) derive their own
    span from their first and last constituent segments. Segments built
    by the default segmenter always carry spans, but hand-built test
    fixtures occasionally pass ``span=None``; this helper propagates
    that absence rather than fabricating a misleading 0-length span.
    """
    if start_span is None or end_span is None:
        return None
    return Span(start_span.start, end_span.end)


def _try_date(segs: list[Segment], i: int) -> tuple[Date, int] | None:
    """Match ``<digit>年(<digit>月)?(<digit>日)?`` starting at ``i``.

    May split a hanzi_text segment in place to peel off the year/month
    /day marker char from any text that follows it.
    """
    if segs[i].type != "digit_run":
        return None
    if not _peel_marker_if_starts_with(segs, i + 1, "年"):
        return None

    parts: list[InlineNode] = [
        Number(surface=segs[i].surface, span=segs[i].span, role="year"),
        _marker("年", segs[i + 1].span),
    ]
    end_idx = i + 2
    end_span = segs[i + 1].span

    # Optional month
    if (
        end_idx + 1 < len(segs)
        and segs[end_idx].type == "digit_run"
        and _peel_marker_if_starts_with(segs, end_idx + 1, "月")
    ):
        parts.append(Number(surface=segs[end_idx].surface, span=segs[end_idx].span, role="month"))
        parts.append(_marker("月", segs[end_idx + 1].span))
        end_span = segs[end_idx + 1].span
        end_idx += 2

    # Optional day
    if (
        end_idx + 1 < len(segs)
        and segs[end_idx].type == "digit_run"
        and _peel_marker_if_starts_with(segs, end_idx + 1, "日")
    ):
        parts.append(Number(surface=segs[end_idx].surface, span=segs[end_idx].span, role="day"))
        parts.append(_marker("日", segs[end_idx + 1].span))
        end_span = segs[end_idx + 1].span
        end_idx += 2

    surface = "".join(p.surface for p in parts)
    span = _span_range(segs[i].span, end_span)
    return Date(surface=surface, span=span, parts=parts), end_idx


def _try_dash(segs: list[Segment], i: int) -> tuple[Punct, int] | None:
    """Merge two consecutive em-dashes ``——`` into one :class:`Punct`.

    The Chinese em-dash is written as a pair of em-dashes ``——`` and
    occupies two braille cells ⠠⠤; a lone ``—`` is the English dash (one
    cell ⠤). The
    Segmenter emits punctuation one char at a time, so this matcher folds
    an adjacent ``—`` ``—`` pair into a single ``Punct(surface="——")`` —
    the punctuation table maps that two-char key to ⠠⠤, while a leftover
    single ``—`` falls through to the atomic path and stays ⠤.

    Only an exact pair is consumed; a third ``—`` is left for the next
    iteration (``———`` → ``——`` + ``—`` → ⠠⠤ then ⠤).
    """
    if segs[i].type != "punct" or segs[i].surface != "—":
        return None
    if i + 1 >= len(segs):
        return None
    nxt = segs[i + 1]
    if nxt.type != "punct" or nxt.surface != "—":
        return None
    span = _span_range(segs[i].span, nxt.span)
    return Punct(surface="——", span=span), i + 2


# ---------------------------------------------------------------------------
# Atomic conversion
# ---------------------------------------------------------------------------


def _try_atomic(seg: Segment) -> InlineNode | None:
    """Convert a stand-alone segment into the corresponding InlineNode,
    or return None to pass it through unchanged."""
    if seg.type == "digit_run":
        return Number(surface=seg.surface, span=seg.span)
    if seg.type == "punct":
        return Punct(surface=seg.surface, span=seg.span)
    if seg.type == "space":
        return Space(surface=seg.surface, span=seg.span)
    if seg.type == "math_inline":
        # The MathParser fills the ``math`` field later (FrontendDriver.attach_math).
        # A *tagged* island is deferred inline math the input layer extracted
        # but did not convert (Word OMML / EQ field); decode it back to its
        # raw payload + source dialect so the right adapter runs later. This
        # is what lets the input layer defer instead of importing the math
        # frontend (see brailix.core.inline_math / ARCHITECTURE#arch-layers).
        if inline_math.is_tagged(seg.surface):
            source, payload = inline_math.unwrap(seg.surface)
            return MathInline(surface=payload, span=seg.span, source=source)
        # Otherwise it's a plain user-typed fragment. Most inline math arrives
        # as LaTeX (``$x^2$``); a bare leading ``<math`` (after the opening
        # ``$``) marks MathML — a sufficient discriminator since the LaTeX
        # grammar can't begin with an XML element. (MTEF / script-cluster
        # paths still emit this eager ``$<math>...$`` form.)
        inner = seg.surface
        if inner.startswith("$") and inner.endswith("$"):
            inner = inner[1:-1]
        source = "mathml" if inner.lstrip().startswith("<math") else "latex"
        return MathInline(surface=seg.surface, span=seg.span, source=source)
    if seg.type == "phonetic_inline":
        # A protected ``/.../`` / ``[...]`` IPA region from the segmenter.
        # Strip the one-char delimiters so the node carries the bare
        # phoneme run; the span narrows to the inner content so each
        # braille cell maps back onto the phoneme the author typed (the
        # delimiters produce no cells). The segmenter only emits this type
        # for a well-formed paired region, so surface is always ≥2 chars.
        inner = seg.surface[1:-1]
        span = Span(seg.span.start + 1, seg.span.end - 1) if seg.span else None
        return PhoneticInline(surface=inner, span=span)
    if seg.type == "math_op":
        # Single half-width math operator (`(`, `)`, `+`, `=`, ...) in
        # prose. Build the trivial MathML tree directly so the math
        # frontend's parser is never invoked — FrontendDriver.attach_math
        # short-circuits when ``MathInline.math`` is already filled.
        # Bare tags only (no ``xmlns`` attribute): the backend dispatches
        # on local names, and the IR round-trip (ET.tostring -> fromstring)
        # must stay namespace-free or the reparse Clark-notates every tag
        # and dispatch falls through.  Adapter-sourced trees are likewise
        # namespace-stripped, so math_op stays consistent with them.
        math = _ET.Element("math")
        mo = _ET.SubElement(math, "mo")
        mo.text = _MATH_OP_CANONICAL.get(seg.surface, seg.surface)
        return MathInline(
            surface=seg.surface, span=seg.span, source="mathml", math=math
        )
    if seg.type in ("latin_text", "greek_text"):
        # Greek runs flow through the Latin IR path: the backend's
        # translate_latin emits one ``profile.letter()`` lookup on the
        # first character (which picks the correct script-class prefix
        # — Greek upper/lower-case sign ⠸/⠨ for Greek, Latin
        # upper/lower-case sign ⠠/⠰ for Latin) then bare letter cells
        # for the rest. The segmenter
        # already split Latin and Greek into separate runs so each gets
        # its own prefix.
        # All-caps runs are not a separate node: the backend decides the
        # doubled capital sign from ``surface`` itself, and its test is in
        # fact stricter than this one was (it also requires ASCII letters),
        # so the type never matched the behaviour it looked like it drove.
        return LatinWord(surface=seg.surface, span=seg.span)
    if seg.type == "unknown":
        return Unknown(surface=seg.surface, span=seg.span)
    return None


# ---------------------------------------------------------------------------
# Marker peeling helper
# ---------------------------------------------------------------------------


def _peel_marker_if_starts_with(segs: list[Segment], idx: int, marker: str) -> bool:
    """If ``segs[idx]`` is a hanzi_text segment starting with ``marker``,
    rewrite it in place so the marker becomes its own segment and any
    remainder is reinserted as the following segment. Returns whether
    the marker was peeled.

    This is the trick that lets ``2026年5月17日去了`` produce a clean
    ``[2026][年][5][月][17][日][去了]`` view for date detection
    without requiring the Segmenter to know about date markers.
    """
    if idx >= len(segs):
        return False
    seg = segs[idx]
    if seg.type != "hanzi_text" or not seg.surface.startswith(marker):
        return False
    if seg.span is None:
        return False  # shouldn't happen with default Segmenter, but be safe
    marker_span = Span(seg.span.start, seg.span.start + len(marker))
    segs[idx] = Segment(type="hanzi_text", surface=marker, span=marker_span)
    rest = seg.surface[len(marker) :]
    if rest:
        remainder = Segment(
            type="hanzi_text",
            surface=rest,
            span=Span(marker_span.end, seg.span.end),
        )
        segs.insert(idx + 1, remainder)
    return True


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

normalizer_registry: Registry[Normalizer] = Registry("normalizer", protocol=Normalizer)

# What :func:`normalize` falls back to when the context names no normalizer —
# the delegating adapter that picks by the document's language.
AUTO_NORMALIZER = "auto"

# The built-in, language-neutral normalizer's registered name, kept distinct
# from the name above for the same reason as
# :data:`~brailix.frontend.segmentation.BUILTIN_SEGMENTER`.
BUILTIN_NORMALIZER = "default"

normalizer_registry.register(BUILTIN_NORMALIZER, DefaultNormalizer)


@_dataclass(slots=True)
class AutoNormalizer:
    """Delegating normalizer: uses the active language's, else the built-in.

    Counterpart to :class:`~brailix.frontend.segmentation.AutoSegmenter`; see it
    for why the choice is made per call rather than cached.
    """

    name: str = "auto"

    def normalize(
        self,
        segments: Iterable[Segment],
        ctx: FrontendContext | None = None,
    ) -> list[NormalizedItem]:
        picked = pick_by_language(normalizer_registry, ctx, BUILTIN_NORMALIZER)
        return normalizer_registry.get(picked).normalize(segments, ctx)


normalizer_registry.register(AUTO_NORMALIZER, AutoNormalizer)


def normalize(
    segments: list[Segment],
    ctx: FrontendContext | None = None,
):
    """Run the active normalizer over ``segments``.

    The adapter is chosen by ``ctx.options["normalizer"]``, defaulting to
    :data:`AUTO_NORMALIZER`. Returns whatever
    the adapter returns — a list of
    :class:`~brailix.ir.inline.InlineNode` (and possibly
    :class:`Segment`) entries.
    """
    name = AUTO_NORMALIZER
    if ctx is not None and ctx.options:
        name = ctx.options.get("normalizer", AUTO_NORMALIZER)
    return normalizer_registry.get(name).normalize(segments, ctx)


# The registry (promised on the **extension surface** — see :mod:`brailix`)
# and the subsystem entry point the orchestrator calls, mirroring
# :mod:`brailix.frontend.segmentation`. Everything else here — the concrete
# normalizers, ``NormalizedItem``, the unit and marker tables, and the dozen
# IR node types this module imports to build with — is internal.
__all__ = ("normalizer_registry", "normalize")
