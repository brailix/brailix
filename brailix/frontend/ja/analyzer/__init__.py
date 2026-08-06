"""Japanese morphological-analysis subsystem.

Three entry points (internal, like every path outside the facades — see
:mod:`brailix.frontend`). :func:`analyze` and :func:`tokens_to_inline` feed the
Japanese :class:`~brailix.core.protocols.LanguageFrontend` (``_JaFrontend`` in
:mod:`brailix.frontend`); :func:`list_analyzers` instead serves the CLI's
analyzer picker:

* :func:`analyze` — text → ``list[JapaneseToken]`` via the analyzer
  adapter selected by ``ctx.options["ja_analyzer"]``. ``"auto"`` lazily
  picks the best installed engine (janome → fugashi → sudachi), falling
  back to the dependency-free ``kana`` analyzer.
* :func:`tokens_to_inline` — convert :class:`JapaneseToken` →
  :class:`~brailix.ir.inline.InlineNode`. A token with a reading becomes
  one :class:`~brailix.ir.inline.Word` (the reading rides ``Word.reading``
  the way pinyin does for Chinese); a token with no reading (a kanji the
  ``kana`` fallback can't read) becomes per-character placeholder
  one-character :class:`~brailix.ir.inline.Word` nodes (the backend emits
  a ``MISSING_READING`` cell). A blank cell precedes each 自立語 (bunsetsu
  head) for word-spacing (分かち書き), decided from each token's
  part-of-speech.

The reading is a **katakana pronunciation form** (発音形): long vowels
already as ー, and particle は read ワ / へ read エ. Adapters that expose
the dictionary's pronunciation field (janome ``phonetic``, fugashi UniDic
``pron``) give this directly; see each adapter for its field choice.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING
from typing import Protocol as _Protocol
from typing import runtime_checkable as _runtime_checkable

from brailix.core.errors import FrontendContractError
from brailix.core.span import Span
from brailix.frontend.ja._chars import _is_kana
from brailix.ir.inline import InlineNode, Space, Word

if _TYPE_CHECKING:
    from brailix.core.context import FrontendContext


@_dataclass(slots=True)
class JapaneseToken:
    """One morpheme: surface text, a katakana pronunciation-form reading
    (``None`` when the analyzer can't read it), the analyzer's
    part-of-speech string (drives word-spacing / 分かち書き), and a span
    relative to the analyzed run."""

    surface: str
    reading: str | None = None
    pos: str | None = None
    span: Span | None = None


@_runtime_checkable
class JapaneseAnalyzer(_Protocol):
    """Tokenize Japanese text into :class:`JapaneseToken` morphemes.

    A ``Protocol`` can only promise this object has an ``analyze`` method, so
    the rest of the contract is checked at :func:`analyze`, where the result
    crosses into the library (see :func:`_check_analyzer_output`): tokens come
    back as a ``list`` of :class:`JapaneseToken`, spans stay inside the
    analyzed text, and consecutive spans are ordered and non-overlapping —
    which here decides 分かち書き spacing, not merely provenance. Omitting
    spans is allowed; a surface that does not match the source at its span is
    allowed with a warning, since a normalising analyzer produces one.
    """

    name: str

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]: ...


# Japanese's own default, stated here rather than shared with the Chinese
# subsystem's identical one: zh and ja are independently replaceable
# language components, so this is a coincidence of policy, not one fact in
# two places. A single constant for both would mean neither language could
# change its default without touching the other.
#
# Nor is it read off :class:`brailix.Pipeline` the way ``analyzer`` /
# ``resolver`` are — those are fields a caller sets, whereas a Japanese
# analyzer is chosen through the per-language ``"{lang}_analyzer"`` option
# key and has no field of its own to agree with.
_DEFAULT_ANALYZER: str = "auto"


def analyze(
    text: str, ctx: FrontendContext | None = None
) -> list[JapaneseToken]:
    """Tokenize a Japanese run into :class:`JapaneseToken`.

    The analyzer is selected by ``ctx.options["ja_analyzer"]``; absent,
    the default is ``"auto"`` which lazily picks the best installed
    engine and falls back to the dependency-free ``kana`` analyzer.
    """
    name = _DEFAULT_ANALYZER
    if ctx is not None and ctx.options:
        name = ctx.options.get("ja_analyzer", _DEFAULT_ANALYZER)

    # Lazy import keeps registry-registration order independent of import
    # order at the top of ``frontend/__init__.py`` (mirrors frontend.zh).
    from brailix.frontend.ja.analyzer.registry import analyzer_registry

    tokens = analyzer_registry.get(name).analyze(text, ctx)
    _check_analyzer_output(tokens, text, name, ctx)
    return tokens


def _check_analyzer_output(
    tokens: object, text: str, adapter: str, ctx: FrontendContext | None
) -> None:
    """Verify what an analyzer adapter returned before anything consumes it.

    The Japanese counterpart of the check in
    :mod:`brailix.frontend.zh.analyzer`, and the same argument: a ``Protocol``
    proves an adapter *has* an ``analyze`` method, not what comes back, and the
    registry is open. What reads the result here is if anything more sensitive
    to the spans than the Chinese path is —
    :func:`_is_intraword_kana_continuation` decides whether two morphemes are
    one over-segmented word by testing ``prev.span.end == token.span.start``,
    so coordinates that overlap or run backwards do not merely mislocate text,
    they change the 分かち書き spacing of the braille. On top of that
    :func:`tokens_to_inline` rebuilds each node's end as
    ``start + len(surface)``, which quietly repairs a wrong end and leaves the
    wrong start in place.

    Refused outright: a result that is not a ``list`` of
    :class:`JapaneseToken`; a token whose ``surface`` / ``reading`` / ``pos`` /
    ``span`` is not the declared type; a span past the end of the analyzed
    text; spans that overlap or run backwards. ``span=None`` stays legal
    (:func:`tokens_to_inline` emits span-less nodes for it), and a ``surface``
    that does not match the text its span points at is a warning
    (``TOKEN_SPAN_MISMATCH``), not an error — an analyzer that normalises its
    input produces one legitimately.

    Written out here rather than shared with the Chinese check: zh and ja are
    independently replaceable language components
    (ARCHITECTURE#arch-layers), they validate different token types, and the
    two would have to be prised apart again the moment one language's token
    grew a field the other has not got. The same line
    :data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS` draws — the
    *fact* is shared (one error type, one warning code), the loop is not.
    """
    if not isinstance(tokens, list):
        raise FrontendContractError(
            f"ja analyzer {adapter!r} returned {type(tokens).__name__}, not a "
            f"list of JapaneseToken"
        )
    end_of_previous: int | None = None
    for i, tok in enumerate(tokens):
        if not isinstance(tok, JapaneseToken):
            raise FrontendContractError(
                f"ja analyzer {adapter!r} returned {type(tok).__name__} at "
                f"index {i}, not a JapaneseToken"
            )
        if (
            not isinstance(tok.surface, str)
            or not (tok.reading is None or isinstance(tok.reading, str))
            or not (tok.pos is None or isinstance(tok.pos, str))
        ):
            raise FrontendContractError(
                f"ja analyzer {adapter!r} token {i} has surface "
                f"{tok.surface!r} / reading {tok.reading!r} / pos {tok.pos!r}; "
                f"JapaneseToken declares surface: str and reading / pos: "
                f"str | None"
            )
        span = tok.span
        if span is None:
            continue
        if not isinstance(span, Span):
            raise FrontendContractError(
                f"ja analyzer {adapter!r} token {i} ({tok.surface!r}) has span "
                f"{span!r} of type {type(span).__name__}, not a Span"
            )
        if span.end > len(text):
            raise FrontendContractError(
                f"ja analyzer {adapter!r} token {i} ({tok.surface!r}) has span "
                f"{span} reaching past the end of the {len(text)}-character "
                f"text it analyzed"
            )
        if end_of_previous is not None and span.start < end_of_previous:
            raise FrontendContractError(
                f"ja analyzer {adapter!r} token {i} ({tok.surface!r}) starts at "
                f"{span.start}, before the previous token ended at "
                f"{end_of_previous}; tokens must be ordered and non-overlapping"
            )
        end_of_previous = span.end
        if ctx is not None and (
            span.length != len(tok.surface)
            or not text.startswith(tok.surface, span.start)
        ):
            ctx.warnings.warn(
                code="TOKEN_SPAN_MISMATCH",
                message=(
                    f"ja analyzer {adapter!r} token {i} claims surface "
                    f"{tok.surface!r} at {span}, where the source reads "
                    f"{text[span.start : span.end]!r}"
                ),
                surface=tok.surface,
                span=span,
                source="frontend.ja.analyzer",
            )


def list_analyzers() -> list[str]:
    """Return the names of every registered Japanese-analyzer adapter.

    Sorted, and independent of which third-party engines are installed:
    registration records only a lazy loader, so a name like ``"fugashi"``
    appears even on a bare install (selecting it raises
    :class:`~brailix.core.errors.MissingExtraError` only when the adapter
    is loaded). Mirrors :func:`brailix.frontend.zh.analyzer.list_analyzers`
    so a front-end populates its analyzer picker from the registry instead
    of duplicating the adapter set.
    """
    from brailix.frontend.ja.analyzer.registry import analyzer_registry

    return analyzer_registry.names()


# 付属語 (dependent words) attach to the preceding 自立語 with no space.
_DEPENDENT_POS: frozenset[str] = frozenset({"助詞", "助動詞"})


def _is_all_kana(s: str) -> bool:
    """True for a non-empty string made entirely of syllabic kana."""
    return bool(s) and all(_is_kana(c) for c in s)


def _is_intraword_kana_continuation(
    token: JapaneseToken, prev: JapaneseToken | None
) -> bool:
    """Whether ``token`` continues the *same* kana word as ``prev`` and so
    must **not** take a leading 分かち書き space.

    Analyzers (janome / IPADIC) over-segment an all-kana word into adjacent
    morphemes — ワタシ → ワタ + シ, both tagged 名詞 — which would otherwise
    drop a stray blank cell *inside* the word. We treat two contiguous,
    all-kana tokens as one word: 分かち書き spaces only at 文節 boundaries,
    never word-internally (J3 切れ続き 細則).

    Contiguity is decided from the spans (``prev.span.end == token.span.start``);
    a real source space comes back as its own 記号,空白 token, so genuinely
    separated kana runs are *not* contiguous and still get their boundary
    space. Normal 文節 heads (本 / 読む / 名前) carry kanji, so they aren't
    all-kana and are unaffected.
    """
    if prev is None or prev.span is None or token.span is None:
        return False
    if prev.span.end != token.span.start:
        return False
    # A 付属語 (助詞 / 助動詞) never fragments the following content word —
    # it closes its own 文節. Over-segmentation (ワタ → ワタ + シ) only
    # happens *inside* one 自立語, where both halves share a content POS.
    # Without this gate a particle followed by an all-kana content word
    # (は + パン, は + ペン) looked "contiguous and all-kana" and lost its
    # 文節-boundary space, so 私はパンを買う / これはペンです under-spaced.
    if prev.pos and prev.pos.split(",")[0] in _DEPENDENT_POS:
        return False
    return _is_all_kana(prev.surface) and _is_all_kana(token.surface)


def _is_bunsetsu_head(token: JapaneseToken, prev: JapaneseToken | None) -> bool:
    """Whether ``token`` starts a new bunsetsu (文節) — i.e. takes a leading
    blank cell under 文節分かち書き.

    A 自立語 (independent word) starts a bunsetsu. A 付属語 (助詞 / 助動詞)
    and a 接尾 suffix attach to the preceding word; a word right after a
    接頭詞 prefix attaches forward. A token with no POS (the dependency-free
    ``kana`` analyzer) yields ``False`` — no morphology, no auto-spacing,
    so kana-only output keeps whatever spaces the source had.

    Two contiguous all-kana tokens are one over-segmented word (ワタ + シ),
    so a continuation never takes a space — 分かち書き is a 文節 boundary
    rule, not a word-internal one (J3 切れ続き 細則).
    """
    if not token.pos:
        return False
    if _is_intraword_kana_continuation(token, prev):
        return False
    major = token.pos.split(",")[0]
    if major in _DEPENDENT_POS:
        return False
    if "接尾" in token.pos:
        return False
    # Substring match (not exact pos1 equality) so this works across POS
    # vocabularies: janome/IPADIC tags prefixes 接頭詞, fugashi/UniDic uses
    # 接頭辞. Mirrors the 接尾 substring test above; an exact "== 接頭詞"
    # silently failed under fugashi/sudachi (お名前 got a stray space).
    if prev is not None and prev.pos and "接頭" in prev.pos:
        return False
    return True


def tokens_to_inline(
    tokens: list[JapaneseToken], base: int = 0
) -> list[InlineNode]:
    """Convert Japanese tokens to inline IR (spans shifted by ``base``).

    A token with a reading → one :class:`Word`. A token with no reading
    (kanji the fallback couldn't read) → per-character one-character
    :class:`Word`
    placeholders so the backend warns ``MISSING_READING`` rather than
    mis-rendering. A blank cell is inserted before each 自立語 (bunsetsu
    head) for 文節 word-spacing (分かち書き), decided by the part-of-speech.
    """
    out: list[InlineNode] = []
    prev: JapaneseToken | None = None
    for t in tokens:
        start = base + t.span.start if t.span is not None else None
        # Wakachigaki: a blank cell precedes each 自立語 (bunsetsu head),
        # except the first token; 付属語 attach to the preceding word.
        if prev is not None and start is not None and _is_bunsetsu_head(t, prev):
            out.append(Space(surface="", span=Span(start, start)))
        reading = t.reading
        # An all-kana token the analyzer didn't read — an unknown katakana
        # word comes back with phonetic "*" — is already its own
        # pronunciation form: use the kana itself rather than a placeholder.
        if not reading and t.surface and all(_is_kana(c) for c in t.surface):
            reading = t.surface
        if reading:
            span = (
                Span(start, start + len(t.surface)) if start is not None else None
            )
            out.append(
                Word(surface=t.surface, reading=reading, pos=t.pos, span=span)
            )
        else:
            for k, ch in enumerate(t.surface):
                cspan = Span(start + k, start + k + 1) if start is not None else None
                out.append(Word(surface=ch, reading=None, span=cspan))
        prev = t
    return out


# This subsystem publishes its own contract, its token type, and the three
# entry points above — mirroring :mod:`brailix.frontend.zh.analyzer`. Without
# an ``__all__`` here, ``from brailix.frontend.ja.analyzer import *`` also
# handed out ``Span``, ``InlineNode``, ``Space`` and ``Word``: implementation
# dependencies this module imports to do its job, published by accident from
# an address the extension guide sends adapter authors to.
#
# ``JapaneseToken`` IS published here, unlike Chinese's ``ChineseToken``, and
# the asymmetry is deliberate: Japanese has one subsystem, so there is no
# second consumer for the token to stay independent of (see
# :mod:`brailix.frontend.zh.tokens`).
__all__ = (
    "JapaneseAnalyzer",
    "JapaneseToken",
    "analyze",
    "list_analyzers",
    "tokens_to_inline",
)
