"""Chinese frontend subsystem.

Five entry points (internal, like every path outside the facades — see
:mod:`brailix.frontend`).  Four feed the orchestrator
(:class:`brailix.Pipeline`); :func:`list_analyzers` instead serves the
CLI and any caller that enumerates the analyzer registry:

* :func:`tokenize` — text → ``list[ChineseToken]`` via the analyzer
  adapter selected by ``ctx.options["zh_analyzer"]``.  The pluggable
  surface; ``"auto"`` lazily picks ``thulac`` → ``hanlp`` → ``jieba`` → ``char``,
  skipping whatever isn't installed.  A personal segmentation dictionary
  (``ctx.options["user_seg_dict"]``) is applied on top as a post-pass.
* :func:`list_analyzers` — names of the registered analyzer adapters
  (drives the CLI ``--list-analyzers`` flag).
* :func:`shift_token_spans` — promote per-segment span coordinates
  into doc coordinates.  Pure helper; no adapter choice.
* :func:`tokens_to_inline` — convert :class:`ChineseToken` →
  :class:`InlineNode` and materialise the Chinese-braille
  "write a word's characters together, separate words with a space"
  rule by inserting zero-width :class:`Space` markers at word
  boundaries.  Pure helper; no adapter choice.
* :func:`insert_cross_kind_boundary_spaces` — insert spaces at
  hanzi↔non-hanzi boundaries.  Pure helper; no adapter choice.

ARCHITECTURE#arch-traceability names the "IRBuilder" step that follows
ZhAnalyzer + PinyinResolver in the data flow.  The Chinese slice
of that step lives here rather than in the orchestrator so
:mod:`brailix.pipeline` doesn't contain Chinese-specific
typesetting knowledge.  ARCHITECTURE#arch-mediators keeps zh and
pinyin independent
subsystems — :func:`tokens_to_inline` deliberately doesn't invoke
pinyin; the orchestrator chains the steps.
"""

from __future__ import annotations

from typing import Protocol as _Protocol
from typing import runtime_checkable as _runtime_checkable

from brailix.core.context import FrontendContext
from brailix.core.errors import FrontendContractError
from brailix.core.span import Span
from brailix.frontend.zh.analyzer._user_dict import (
    apply_user_seg_dict as _apply_user_seg_dict,
)
from brailix.frontend.zh.analyzer._user_dict import (
    normalize_seg_dict as _normalize_seg_dict,
)
from brailix.frontend.zh.tokens import ChineseToken
from brailix.ir.inline import (
    Connector,
    Date,
    HanziMarker,
    InlineNode,
    LatinWord,
    MathInline,
    Number,
    Space,
    Word,
)

# The ``auto`` adapter's registered name — what a caller who names
# nothing gets. Matches the corresponding :class:`brailix.Pipeline`
# field default, which is the library-wide declaration; not imported
# from there because the orchestrator sits ABOVE this layer. Pinned
# equal by ``tests/frontend/test_default_adapter_names.py``.
_AUTO = "auto"


@_runtime_checkable
class ChineseAnalyzer(_Protocol):
    """Tokenize a Chinese text region into words with POS tags.

    Implementations wrap external tokenizers (HanLP, jieba, THULAC, ...)
    and emit the normalized :class:`~brailix.frontend.zh.tokens.ChineseToken`
    shape so downstream code never depends on the underlying library.

    ``ctx`` may be ``None`` so callers (notably the ``auto`` delegating
    adapter) can pass through whatever they received without forcing
    a non-None context just to satisfy the type checker.

    **What the tokens have to be.** A ``Protocol`` can only promise this
    object has an ``analyze`` method, so the rest of the contract is checked
    at :func:`tokenize`, where the result crosses into the library (see
    :func:`_check_analyzer_output`): tokens come back as a ``list`` of
    :class:`~brailix.frontend.zh.tokens.ChineseToken`, spans stay inside the
    analyzed text, and consecutive spans are ordered and non-overlapping.
    Omitting spans entirely is allowed (they are synthesised from a running
    cursor); a surface that does not match the source at its span is allowed
    too, with a warning, since a normalising tokenizer legitimately produces
    one.

    Lives here rather than in :mod:`brailix.core.protocols`: a protocol whose
    signature names Chinese types is Chinese's contract, and putting it on a
    shared layer made this language's seam differ from every other one's
    (Japanese declares ``JapaneseAnalyzer`` in its own analyzer package).
    """

    name: str

    def analyze(
        self, text: str, ctx: FrontendContext | None
    ) -> list[ChineseToken]: ...


def tokenize(
    text: str, ctx: FrontendContext | None = None
) -> list[ChineseToken]:
    """Tokenize a Chinese text run into :class:`ChineseToken`.

    The analyzer is selected by ``ctx.options["zh_analyzer"]``; when
    absent the default is ``"auto"`` which lazily picks
    ``thulac`` → ``hanlp`` → ``jieba`` → ``char`` depending on what's installed.

    A personal **segmentation dictionary** on
    ``ctx.options["user_seg_dict"]`` (surface → the pieces it should become)
    is applied as a post-pass over whichever adapter ran, so a division the
    user pinned wins over every engine's opinion and composes with all of
    them. Absent / empty for the bare library — see
    :mod:`brailix.frontend.zh.analyzer._user_dict`.
    """
    name = _AUTO
    seg_dict: dict[str, tuple[str, ...]] | None = None
    if ctx is not None and ctx.options:
        name = ctx.options.get("zh_analyzer", _AUTO)
        # Injected as plain data by a front-end (a proofreading front-end).
        # Normalized here rather than trusted: the option bag is reachable
        # from a hand-edited file, and an entry whose pieces don't spell its
        # key would rewrite text the document never contained.
        raw_seg = ctx.options.get("user_seg_dict")
        if raw_seg:
            seg_dict = _normalize_seg_dict(raw_seg)

    # Lazy import: keeps registry-registration order independent of
    # import order at the top of ``frontend/__init__.py``.
    from brailix.frontend.zh.analyzer.registry import analyzer_registry

    tokens = analyzer_registry.get(name).analyze(text, ctx)
    _check_analyzer_output(tokens, text, name, ctx)
    if seg_dict:
        tokens = _apply_user_seg_dict(tokens, seg_dict)
    return tokens


def _check_analyzer_output(
    tokens: object, text: str, adapter: str, ctx: FrontendContext | None
) -> None:
    """Verify what an analyzer adapter returned before anything consumes it.

    :class:`ChineseAnalyzer` is a ``Protocol``, so the registry can prove an
    adapter *has* an ``analyze`` method and nothing about what comes back.
    Everything downstream then reads the result as fact:
    :func:`shift_token_spans` lifts the spans into document coordinates,
    :func:`tokens_to_inline` places a word-boundary :class:`Space` at each
    token's ``span.end``, the cross-kind boundary rules compare
    ``prev.span.end`` with ``cur.span.start`` to decide spacing, and every
    braille cell finally inherits those coordinates as the source it maps back
    to. A wrong span does not crash; it produces a document whose proofreading
    jumps land on the wrong characters.

    So this is the boundary where a third party's output stops being trusted.
    Four things are refused outright, because none of them can be a legitimate
    analysis of anything:

    * a result that is not a ``list`` of :class:`ChineseToken`;
    * a token whose ``surface`` / ``pos`` / ``span`` is not the declared type;
    * a span reaching past the end of the analyzed text;
    * spans that overlap or run backwards.

    ``span=None`` stays legal — :func:`_local_spans` documents synthesising
    coordinates for an adapter that omits them — and so does a ``surface`` that
    does not match the text the span points at, which is a *warning*
    (``TOKEN_SPAN_MISMATCH``): an analyzer that normalises its input produces
    exactly that, and both shipped cursor-recovery adapters (THULAC, HanLP) do
    so deliberately. The coordinates are unreliable for that token, which a
    proofreader should be told; the document is still translatable, which is
    why it is not an error.

    **What is checked is the span each token ends up with**, not only the ones
    the adapter wrote down. Skipping ``span=None`` and comparing the explicit
    ones to each other checks an ordering nothing downstream ever sees:
    :func:`_local_spans` runs *after* this and fills the gaps from a cursor, so
    a spanless token followed by an explicit span that points backwards passed
    every check here and then produced ``(0,3)``, ``(3,3)``, ``(1,2)`` — the
    exact overlap-and-run-backwards this function exists to refuse, assembled
    out of two halves that were each fine on their own. So the cursor runs
    here, in the same pass, and order and range are decided on the coordinates
    the IR will carry.

    Checked for every adapter, built-in ones included: "trust ours, check
    theirs" would make the invariant untestable through the normal path, and
    the cost is one pass over a list that was just built. The surface
    comparison uses ``str.startswith`` with an offset rather than a slice, so
    it allocates nothing per token.
    """
    if not isinstance(tokens, list):
        raise FrontendContractError(
            f"zh analyzer {adapter!r} returned {type(tokens).__name__}, not a "
            f"list of ChineseToken"
        )
    end_of_previous: int | None = None
    for i, tok in enumerate(tokens):
        if not isinstance(tok, ChineseToken):
            raise FrontendContractError(
                f"zh analyzer {adapter!r} returned {type(tok).__name__} at "
                f"index {i}, not a ChineseToken"
            )
        if not isinstance(tok.surface, str) or not (
            tok.pos is None or isinstance(tok.pos, str)
        ):
            raise FrontendContractError(
                f"zh analyzer {adapter!r} token {i} has surface "
                f"{tok.surface!r} / pos {tok.pos!r}; ChineseToken declares "
                f"surface: str and pos: str | None"
            )
        span = tok.span
        if span is not None and not isinstance(span, Span):
            raise FrontendContractError(
                f"zh analyzer {adapter!r} token {i} ({tok.surface!r}) has span "
                f"{span!r} of type {type(span).__name__}, not a Span"
            )
        # The coordinates this token will actually carry: its own, or the ones
        # :func:`_local_spans` is about to lay out from the same cursor.
        if span is not None:
            effective = span
        else:
            start = 0 if end_of_previous is None else end_of_previous
            effective = Span(start, start + len(tok.surface))
        if effective.end > len(text):
            written = "has span" if span is not None else "is laid out at"
            raise FrontendContractError(
                f"zh analyzer {adapter!r} token {i} ({tok.surface!r}) "
                f"{written} {effective}, reaching past the end of the "
                f"{len(text)}-character text it analyzed"
            )
        if end_of_previous is not None and effective.start < end_of_previous:
            raise FrontendContractError(
                f"zh analyzer {adapter!r} token {i} ({tok.surface!r}) starts at "
                f"{effective.start}, before the previous token ended at "
                f"{end_of_previous}; tokens must be ordered and non-overlapping"
            )
        end_of_previous = effective.end
        # Only an adapter's *own* span can misdescribe the source; a
        # synthesised one is this module's guess and says nothing about it.
        if span is not None and ctx is not None and (
            span.length != len(tok.surface)
            or not text.startswith(tok.surface, span.start)
        ):
            ctx.warnings.warn(
                code="TOKEN_SPAN_MISMATCH",
                message=(
                    f"zh analyzer {adapter!r} token {i} claims surface "
                    f"{tok.surface!r} at {span}, where the source reads "
                    f"{text[span.start : span.end]!r}"
                ),
                surface=tok.surface,
                span=span,
                source="frontend.zh.analyzer",
            )


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


def available_analyzers() -> list[str]:
    """:func:`list_analyzers` filtered to the ones installed right now.

    The picker counterpart. A front-end that offers every *registered* name
    lets a reader select an engine this installation does not have, and the
    consequence lands far away and looks nothing like a settings mistake:
    the selection is stored, every subsequent compile raises
    :class:`~brailix.core.errors.MissingExtraError` for every block, and what
    the reader sees is a document that has gone blank. That happened when the
    desktop bundle stopped shipping one of the engines while a stored setting
    still named it.

    Uses the registry's cheap probe (:meth:`~brailix.core.registry.Registry.available`),
    so this costs no adapter loads. An adapter that declares no probe is
    listed, because "cannot tell" is not "missing".
    """
    from brailix.frontend.zh.analyzer.registry import analyzer_registry

    return analyzer_registry.available_names()


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
    need an unshifted copy.

    Tokens without a span get one laid out from a **running cursor** (see
    :func:`_local_spans`), so a run of spanless tokens comes out ordered
    rather than all starting at zero. This matches what every shipped
    adapter actually produces but guards against future adapters that omit
    spans.

    ``base == 0`` is a fast path that returns the input list unchanged (no
    allocation) — but only when every token already carries a span. Taking it
    unconditionally would skip the synthesis this function documents: a
    spanless token stays spanless at ``base == 0`` while the same input at
    ``base == 5`` comes back with coordinates.
    """
    if base == 0 and all(t.span is not None for t in tokens):
        return tokens
    return [
        ChineseToken(
            surface=t.surface,
            pos=t.pos,
            span=Span(base + local.start, base + local.end),
            pinyin=t.pinyin,
            confidence=t.confidence,
        )
        for t, local in zip(tokens, _local_spans(tokens), strict=True)
    ]


def _local_spans(tokens: list[ChineseToken]) -> list[Span]:
    """One span per token, synthesising the missing ones from a cursor.

    A token that carries a span keeps it, exactly as its adapter wrote it.
    A token that doesn't gets ``Span(cursor, cursor + len(surface))``, where
    the cursor is the end of the previous token's span — so consecutive
    spanless tokens tile the source instead of each restarting at zero.

    The cursor is what makes that true. Giving each spanless token
    ``Span(0, len(surface))`` independently makes two in a row produce
    ``(0,2)`` then ``(0,1)`` — overlapping, non-monotonic coordinates flowing
    straight into the IR, where source↔braille navigation and warning
    highlights read them as positions in the document. Built-in adapters all
    set spans, so that only ever reaches hand-built token lists and
    third-party adapters — which is to say exactly the extension point this
    helper exists to be defensive for.

    A token whose adapter *did* give a span always wins, even where that
    contradicts the cursor: coordinates that came from a real analyzer are
    evidence about the source text, and a guess derived from surface lengths
    is not. That deference is only affordable because the result of this
    layout has already been checked: :func:`_check_analyzer_output` runs the
    same cursor over the same tokens and refuses the whole analysis if the
    coordinates that come out of it leave the text or run backwards — which is
    what mixing spanless tokens with explicit spans could otherwise produce,
    with each half looking correct in isolation.
    """
    spans: list[Span] = []
    cursor = 0
    for t in tokens:
        span = t.span if t.span is not None else Span(cursor, cursor + len(t.surface))
        spans.append(span)
        cursor = span.end
    return spans


def tokens_to_inline(tokens: list[ChineseToken]) -> list[InlineNode]:
    """Convert Chinese tokens to :class:`InlineNode` with word-boundary spaces.

    Two responsibilities:

    1. **Node construction** — every token becomes one :class:`Word`,
       whatever its length, with pinyin / POS / confidence carried across.
       (A single character is a one-character ``Word``, not a node of its
       own — see :class:`~brailix.ir.inline.Word`.)
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

    A token that arrives without a span gets one laid out from a running
    cursor (:func:`_local_spans`), so the emitted nodes stay ordered and
    non-overlapping whether or not the adapter supplied coordinates.

    No pinyin lookup happens here.  Per ARCHITECTURE#arch-mediators / #arch-boundaries, this
    helper deliberately doesn't import :mod:`brailix.frontend.zh.pinyin`;
    the orchestrator (:class:`brailix.Pipeline`) chains
    :func:`tokenize` → :func:`pinyin.annotate` → :func:`tokens_to_inline`
    so the two subsystems remain swap-independent.
    """
    if not tokens:
        return []
    nodes: list[InlineNode] = [
        Word(
            surface=t.surface,
            span=span,
            reading=t.pinyin,
            pos=t.pos,
            confidence=t.confidence,
        )
        for t, span in zip(tokens, _local_spans(tokens), strict=True)
    ]
    if len(nodes) < 2:
        return nodes
    spaced: list[InlineNode] = [nodes[0]]
    # strict=False matches the historical zip behavior; nodes[1:] is
    # by construction shorter than nodes, so the partial pairing is
    # the intent here (we're walking adjacent pairs, not zipping two
    # equal-length lists).
    for prev, cur in zip(nodes, nodes[1:], strict=False):
        # Every node above carries a span (synthesised when the token had
        # none), so the boundary is always the previous word's end.
        assert prev.span is not None
        boundary = prev.span.end
        spaced.append(Space(surface="", span=Span(boundary, boundary)))
        spaced.append(cur)
    return spaced


_CHINESE_NODE_TYPES: tuple[type[InlineNode], ...] = (Word, HanziMarker)
_FOREIGN_NODE_TYPES: tuple[type[InlineNode], ...] = (LatinWord, MathInline)
# Normalizer composites — a whole date, its own "word", set off from adjacent
# Chinese on BOTH sides with a boundary Space: 在2026年 是 在 ⟂ 2026年,
# 2026年去 是 2026年 ⟂ 去. (A bare Number is not a composite — an ordinal-bound
# number like 第3 stays tight, so the Chinese ↔ Number boundary keeps its own
# policy.) A one-member tuple for the same reason as _FOREIGN_LETTER_TYPES
# below: what is declared is the membership, not the one type in it today.
_COMPOSITE_NODE_TYPES: tuple[type[InlineNode], ...] = (Date,)
# A foreign *letter* run (Latin and Greek both flow through this one IR type
# per the Normalizer) can bind to a hanzi as one compound word; a MathInline
# ($...$) never does, so it's excluded from the compound check and always
# takes the space path below. A one-member tuple rather than a bare class
# because the membership is what is being declared: which node kinds may bind
# to a hanzi is a list that a new IR type joins, not a type this code names.
_FOREIGN_LETTER_TYPES: tuple[type[InlineNode], ...] = (LatinWord,)


def insert_cross_kind_boundary_spaces(
    children: list[InlineNode],
    compounds: frozenset[str] = frozenset(),
) -> list[InlineNode]:
    """Insert a synthetic separator at Chinese ↔ Latin/Greek/Math boundaries.

    The National Common Braille (NCB) "segment-and-join-words" rule
    extends across IR-node kinds: a Chinese run (Word / HanziMarker)
    adjacent to a Latin / Greek / Math fragment (LatinWord /
    MathInline) needs a marker between them.
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

    **Composite ↔ Chinese** (``在2026年`` / ``…日我``) takes a word-boundary
    :class:`Space` on *either* side. A Date is a whole word, set off from the
    surrounding prose; without a separator it abuts the neighbouring
    hanzi. A plain Space, not a connector. A bare :class:`Number` is
    different — an ordinal-bound number (``第3``) stays tight — so the
    Chinese ↔ Number boundary keeps its own policy and isn't spaced here.

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
        elif _is_composite_chinese_boundary(prev, cur):
            out.append(Space(surface="", span=span))
        elif _is_chinese_number_boundary(prev, cur):
            out.append(Space(surface="", span=span))
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


def _is_chinese_number_boundary(prev: InlineNode, cur: InlineNode) -> bool:
    """Chinese run directly followed by a bare :class:`Number` → a
    word-boundary :class:`Space`.

    A number is its own word, so it is set off from the preceding hanzi:
    ``有3个`` → 有 ⟂ 3个, ``去5次`` → 去 ⟂ 5次. The lone exception is the
    ordinal prefix 第, which binds to its number (``第3``, no space) — per
    spec 第 is the *only* hanzi that attaches directly to a following
    number. (This is the Chinese→Number direction; the reverse,
    Number→Chinese, takes the connector ⠤ — see
    :func:`_is_number_hanzi_join`.)

    Source-adjacency guard mirrors the other predicates: a known gap
    between the spans means a separator node already sits between them."""
    if not isinstance(prev, _CHINESE_NODE_TYPES) or not isinstance(cur, Number):
        return False
    if prev.surface and prev.surface.endswith("第"):
        return False  # ordinal prefix binds directly to its number (第3)
    if prev.span and cur.span and prev.span.end != cur.span.start:
        return False
    return True


def _is_composite_chinese_boundary(prev: InlineNode, cur: InlineNode) -> bool:
    """Whether a normalizer composite (Date) is
    directly adjacent to a Chinese run on **either** side, so a
    word-boundary :class:`Space` belongs between them.

    Such a node is a whole word, set off from the surrounding prose on both
    sides: ``在2026年`` is 在 + a date (在 ⟂ 2026年), ``2026年去`` is a date +
    去 (2026年 ⟂ 去). Without a separator the composite abuts the hanzi
    (its trailing 日, or the number sign at its head running
    straight on from the preceding syllable). A plain Space, not a
    connector: the composite isn't bound to the neighbouring word.

    (A bare :class:`Number` is different — a number bound by an ordinal
    prefix like 第3 takes no space, so the Chinese ↔ Number boundary
    keeps its own policy and isn't handled here.)

    Source-adjacency guard mirrors the other predicates: a known gap
    between the spans means a separator node already sits between them."""
    composite_then_chinese = isinstance(prev, _COMPOSITE_NODE_TYPES) and isinstance(
        cur, _CHINESE_NODE_TYPES
    )
    chinese_then_composite = isinstance(prev, _CHINESE_NODE_TYPES) and isinstance(
        cur, _COMPOSITE_NODE_TYPES
    )
    if not (composite_then_chinese or chinese_then_composite):
        return False
    if prev.span and cur.span and prev.span.end != cur.span.start:
        return False
    return True


def _is_letter_hanzi_compound(
    prev: InlineNode, cur: InlineNode, compounds: frozenset[str]
) -> bool:
    """Whether ``prev``/``cur`` are a foreign-letter run and a hanzi run
    that together form one compound word (→ connector instead of a space).

    Requires exactly one letter side (LatinWord) and one
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


# This subsystem publishes its own contract and its five entry points.
# ``ChineseToken`` is imported above because :class:`ChineseAnalyzer`'s
# signature names it, but it is NOT published here — see the same note in
# :mod:`brailix.frontend.zh.pinyin`: the mediator belongs to neither end, so
# it is published once, from :mod:`brailix.frontend.zh.tokens`.
__all__ = (
    "ChineseAnalyzer",
    "tokenize",
    "available_analyzers",
    "list_analyzers",
    "shift_token_spans",
    "tokens_to_inline",
    "insert_cross_kind_boundary_spaces",
)
