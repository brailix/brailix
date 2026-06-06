"""Chinese frontend subsystem.

Three public callables, all consumed by the orchestrator
(:class:`brailix.Pipeline`):

* :func:`tokenize` — text → ``list[ChineseToken]`` via the analyzer
  adapter selected by ``ctx.options["zh_analyzer"]``.  The pluggable
  surface; ``"auto"`` lazily picks ``hanlp`` → ``jieba`` → ``char``.
* :func:`shift_token_spans` — promote per-segment span coordinates
  into doc coordinates.  Pure helper; no adapter choice.
* :func:`tokens_to_inline` — convert :class:`ChineseToken` →
  :class:`InlineNode` and materialise the Chinese-braille
  "write a word's characters together, separate words with a space"
  rule by inserting zero-width :class:`Space` markers at word
  boundaries.  Pure helper; no adapter choice.

ARCHITECTURE.md §3 names the "IRBuilder" step that follows
ZhAnalyzer + PinyinResolver in the data flow.  The Chinese slice
of that step lives here rather than in the orchestrator so
:mod:`brailix.pipeline` doesn't contain Chinese-specific
typesetting knowledge.  §7.1 keeps zh and pinyin independent
subsystems — :func:`tokens_to_inline` deliberately doesn't invoke
pinyin; the orchestrator chains the steps.
"""

from __future__ import annotations

from brailix.core.context import FrontendContext
from brailix.core.span import Span
from brailix.ir.inline import (
    ChineseToken,
    Connector,
    HanziChar,
    HanziMarker,
    InlineNode,
    LatinAcronym,
    LatinWord,
    MathInline,
    Number,
    Space,
    Word,
)

_DEFAULT_ANALYZER: str = "auto"


def tokenize(
    text: str, ctx: FrontendContext | None = None
) -> list[ChineseToken]:
    """Tokenize a Chinese text run into :class:`ChineseToken`.

    The analyzer is selected by ``ctx.options["zh_analyzer"]``; when
    absent the default is ``"auto"`` which lazily picks
    ``hanlp`` → ``jieba`` → ``char`` depending on what's installed.
    """
    name = _DEFAULT_ANALYZER
    if ctx is not None and ctx.options:
        name = ctx.options.get("zh_analyzer", _DEFAULT_ANALYZER)

    # Lazy import: keeps registry-registration order independent of
    # import order at the top of ``frontend/__init__.py``.
    from brailix.frontend.zh.analyzer.registry import analyzer_registry

    return analyzer_registry.get(name).analyze(text, ctx)


def list_analyzers() -> list[str]:
    """Return the names of every registered Chinese-analyzer adapter.

    Sorted, and independent of which third-party libraries are actually
    installed: registration records only a lazy loader callable, so a
    name like ``"hanlp"`` appears even on a bare install (selecting it
    raises :class:`~brailix.core.errors.MissingExtraError` only when the
    adapter is loaded).  Front-ends populate an analyzer picker from this
    instead of duplicating the adapter set — the registry stays the
    single source of truth.
    """
    from brailix.frontend.zh.analyzer.registry import analyzer_registry

    return analyzer_registry.names()


def shift_token_spans(
    tokens: list[ChineseToken], base: int
) -> list[ChineseToken]:
    """Promote token-local spans into document coordinates.

    Adapters analyze a single :class:`Segment` and emit tokens whose
    spans are relative to that segment's text.  Before the IRBuilder
    step (:func:`tokens_to_inline`) we lift those spans by
    ``base = segment.span.start`` so downstream IR carries
    doc-absolute coordinates.

    Returns a fresh list of new :class:`ChineseToken` instances —
    inputs are not mutated, so callers can keep the originals if they
    need an unshifted copy.  ``base == 0`` is a fast path that
    returns the input list unchanged (no allocation).

    Tokens without a span get one constructed from ``len(surface)`` —
    this matches what every shipped adapter actually produces but
    guards against future adapters that omit spans.
    """
    if base == 0:
        return tokens
    out: list[ChineseToken] = []
    for t in tokens:
        local_span = t.span if t.span else Span(0, len(t.surface))
        out.append(
            ChineseToken(
                surface=t.surface,
                pos=t.pos,
                span=Span(base + local_span.start, base + local_span.end),
                pinyin=t.pinyin,
                confidence=t.confidence,
            )
        )
    return out


def tokens_to_inline(tokens: list[ChineseToken]) -> list[InlineNode]:
    """Convert Chinese tokens to :class:`InlineNode` with word-boundary spaces.

    Two responsibilities:

    1. **Type dispatch** — single-character tokens become
       :class:`HanziChar`; multi-character tokens become :class:`Word`.
       Pinyin / POS / confidence carry across; the inline schema
       preserves them where the type supports them.
    2. **Word-boundary spacing** — Chinese braille writes characters
       within a word without gaps and separates adjacent words with
       one blank cell (write a word together, separate words with a
       space).  We materialize that rule by inserting a zero-surface
       :class:`Space` between every
       consecutive pair of tokens; the Backend renders each marker
       as a real blank cell.  The Space's span is collapsed to the
       word boundary (start == end) so it never overlaps real text
       positions used by source / braille highlights.

    Inputs of length 0 or 1 are returned without any Space insertion
    — a single-word segment has no boundaries to mark.

    No pinyin lookup happens here.  Per ARCHITECTURE §7.1 / §12, this
    helper deliberately doesn't import :mod:`brailix.frontend.zh.pinyin`;
    the orchestrator (:class:`brailix.Pipeline`) chains
    :func:`tokenize` → :func:`pinyin.annotate` → :func:`tokens_to_inline`
    so the two subsystems remain swap-independent.
    """
    if not tokens:
        return []
    nodes: list[InlineNode] = []
    for t in tokens:
        local_span = t.span if t.span else Span(0, len(t.surface))
        if len(t.surface) == 1:
            nodes.append(
                HanziChar(surface=t.surface, span=local_span, reading=t.pinyin)
            )
        else:
            nodes.append(
                Word(
                    surface=t.surface,
                    span=local_span,
                    reading=t.pinyin,
                    pos=t.pos,
                    confidence=t.confidence,
                )
            )
    if len(nodes) < 2:
        return nodes
    spaced: list[InlineNode] = [nodes[0]]
    # strict=False matches the historical zip behavior; nodes[1:] is
    # by construction shorter than nodes, so the partial pairing is
    # the intent here (we're walking adjacent pairs, not zipping two
    # equal-length lists).
    for prev, cur in zip(nodes, nodes[1:], strict=False):
        boundary = prev.span.end if prev.span else 0
        spaced.append(Space(surface="", span=Span(boundary, boundary)))
        spaced.append(cur)
    return spaced


_CHINESE_NODE_TYPES: tuple[type[InlineNode], ...] = (Word, HanziChar, HanziMarker)
_FOREIGN_NODE_TYPES: tuple[type[InlineNode], ...] = (LatinWord, LatinAcronym, MathInline)
# A foreign *letter* run (Latin / Greek — both flow through these two
# IR types per the Normalizer) can bind to a hanzi as one compound word;
# a MathInline ($...$) never does, so it's excluded from the compound
# check and always takes the space path below.
_FOREIGN_LETTER_TYPES: tuple[type[InlineNode], ...] = (LatinWord, LatinAcronym)


def insert_cross_kind_boundary_spaces(
    children: list[InlineNode],
    compounds: frozenset[str] = frozenset(),
) -> list[InlineNode]:
    """Insert a synthetic separator at Chinese ↔ Latin/Greek/Math boundaries.

    The National Common Braille (NCB) "segment-and-join-words" rule
    extends across IR-node kinds: a Chinese run (Word / HanziChar /
    HanziMarker) adjacent to a Latin / Greek / Math fragment (LatinWord /
    LatinAcronym / MathInline) needs a marker between them.
    :func:`tokens_to_inline` handles the within-Chinese case (Word↔Word
    inside a single ``hanzi_text`` segment); this helper covers the
    cross-segment case the orchestrator assembles by concatenating
    per-segment outputs.

    Two outcomes at a letter↔hanzi boundary, decided by the compound
    lexicon (``profile.zh_compounds``, passed in by the caller):

    * **Compound word** (``x轴`` / ``T恤`` / ``维生素C``) — the letter and
      the hanzi are *one word*, joined with a :class:`Connector`
      (connector ⠤), no gap.
    * **Two words** (``已知 α`` / ``使用 CPU``) — separated with a
      :class:`Space` (one blank cell).

    MathInline ↔ Chinese always takes the Space path (a formula is never
    a compound word).

    **Number → Chinese** (``10页`` / ``3个``) takes a third path: a
    :class:`Connector` (connector ⠤). The digit cells (number sign +
    a–j dot patterns) collide with the following hanzi's leading cell —
    页's ⠑ is the 5 pattern, 个's ⠛ is the 7 — so without the joiner the
    hanzi reads as a digit continuation (``10页`` → "105"). The reverse
    Chinese → Number (``第3``) is left alone: the number sign already
    delimits where the number starts. Number ↔ Latin/Math stays out of
    scope. (Date markers 年/月/日 are bundled inside a
    :class:`~brailix.ir.inline.Date` node and handled in
    :func:`brailix.backend.number.translate_date`, where 年 is the lone
    exception that skips the connector.)

    Idempotent: if a Space already sits between the two nodes (either
    user-typed or previously inserted), the boundary check fails on both
    flanking pairs, so no second separator is added.

    Both synthesised nodes carry ``surface=""`` and a zero-width span at
    the boundary, mirroring :func:`tokens_to_inline`'s convention so
    proofread tooling treats every synthetic separator uniformly.
    """
    if len(children) < 2:
        return children
    out: list[InlineNode] = [children[0]]
    for prev, cur in zip(children, children[1:], strict=False):
        boundary = prev.span.end if prev.span else 0
        span = Span(boundary, boundary)
        if _is_cross_kind_boundary(prev, cur):
            if _is_letter_hanzi_compound(prev, cur, compounds):
                out.append(Connector(surface="", span=span))
            else:
                out.append(Space(surface="", span=span))
        elif _is_number_hanzi_join(prev, cur):
            out.append(Connector(surface="", span=span))
        out.append(cur)
    return out


def _is_cross_kind_boundary(prev: InlineNode, cur: InlineNode) -> bool:
    if isinstance(prev, _CHINESE_NODE_TYPES) and isinstance(cur, _FOREIGN_NODE_TYPES):
        return True
    if isinstance(prev, _FOREIGN_NODE_TYPES) and isinstance(cur, _CHINESE_NODE_TYPES):
        return True
    return False


def _is_number_hanzi_join(prev: InlineNode, cur: InlineNode) -> bool:
    """Whether ``prev``/``cur`` are a number run directly followed by a
    hanzi run (``10页`` / ``3个``) that needs a connector ⠤ between them.

    In National Common Braille the digit cells (number sign + a–j dot
    patterns) frequently collide with the following hanzi's leading cell
    — 页's ⠑ is 5, 个's ⠛ is 7, 日's ⠚ is 0 — so without a connector the
    hanzi is read as a digit continuation. The rule applies only in the
    Number → Chinese direction; the reverse (``第3``) has its own number
    sign at the front as a delimiter and needs none. Number ↔ Latin/Math
    is still out of scope.

    Source-adjacency guard: when both spans are known and don't touch,
    something (a user-typed space, punctuation) sits between them as its
    own node — they're not one written unit, so leave the boundary to its
    own path. Missing spans (hand-built fixtures) fall back to list
    adjacency alone, mirroring :func:`_is_letter_hanzi_compound`."""
    if not isinstance(prev, Number) or not isinstance(cur, _CHINESE_NODE_TYPES):
        return False
    if prev.span and cur.span and prev.span.end != cur.span.start:
        return False
    return True


def _is_letter_hanzi_compound(
    prev: InlineNode, cur: InlineNode, compounds: frozenset[str]
) -> bool:
    """Whether ``prev``/``cur`` are a foreign-letter run and a hanzi run
    that together form one compound word (→ connector instead of a space).

    Requires exactly one letter side (LatinWord / LatinAcronym) and one
    Chinese side, the two source-adjacent (no gap — a user-typed space
    would sit between them as its own node and break this pair), and the
    document-order concatenation of their surfaces present in the
    compound lexicon. MathInline never qualifies (it isn't in
    ``_FOREIGN_LETTER_TYPES``)."""
    if isinstance(prev, _FOREIGN_LETTER_TYPES) and isinstance(cur, _CHINESE_NODE_TYPES):
        pass
    elif isinstance(prev, _CHINESE_NODE_TYPES) and isinstance(cur, _FOREIGN_LETTER_TYPES):
        pass
    else:
        return False
    # Source-adjacency guard: if both spans are known and they don't
    # touch, the two runs aren't really one written token — leave them
    # to the space path. Missing spans (hand-built fixtures) skip the
    # guard and rely on the lexicon hit alone.
    if prev.span and cur.span and prev.span.end != cur.span.start:
        return False
    # children are in document order, so prev.surface precedes cur.surface
    # in the source — the concatenation is the written compound surface.
    return (prev.surface + cur.surface) in compounds


__all__ = (
    "tokenize",
    "list_analyzers",
    "shift_token_spans",
    "tokens_to_inline",
    "insert_cross_kind_boundary_spaces",
)
