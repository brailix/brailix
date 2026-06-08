"""Japanese morphological-analysis subsystem.

Two public callables, both consumed by the Japanese
:class:`~brailix.core.protocols.LanguageFrontend` (``_JaFrontend`` in
:mod:`brailix.frontend`):

* :func:`analyze` — text → ``list[JapaneseToken]`` via the analyzer
  adapter selected by ``ctx.options["ja_analyzer"]``. ``"auto"`` lazily
  picks the best installed engine (janome → fugashi → sudachi), falling
  back to the dependency-free ``kana`` analyzer.
* :func:`tokens_to_inline` — convert :class:`JapaneseToken` →
  :class:`~brailix.ir.inline.InlineNode`. A token with a reading becomes
  one :class:`~brailix.ir.inline.Word` (the reading rides ``Word.reading``
  the way pinyin does for Chinese); a token with no reading (a kanji the
  ``kana`` fallback can't read) becomes per-character placeholder
  :class:`~brailix.ir.inline.HanziChar` nodes (the backend emits a
  ``MISSING_READING`` cell). A blank cell precedes each 自立語 (bunsetsu
  head) for word-spacing (分かち書き), decided from each token's
  part-of-speech.

The reading is a **katakana pronunciation form** (発音形): long vowels
already as ー, and particle は read ワ / へ read エ. Adapters that expose
the dictionary's pronunciation field (janome ``phonetic``, fugashi UniDic
``pron``) give this directly; see each adapter for its field choice.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from brailix.core.span import Span
from brailix.frontend.ja._chars import _is_kana
from brailix.ir.inline import HanziChar, InlineNode, Space, Word

if TYPE_CHECKING:
    from brailix.core.context import FrontendContext


@dataclass(slots=True)
class JapaneseToken:
    """One morpheme: surface text, a katakana pronunciation-form reading
    (``None`` when the analyzer can't read it), the analyzer's
    part-of-speech string (drives word-spacing / 分かち書き), and a span
    relative to the analyzed run."""

    surface: str
    reading: str | None = None
    pos: str | None = None
    span: Span | None = None


@runtime_checkable
class JapaneseAnalyzer(Protocol):
    name: str

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[JapaneseToken]: ...


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

    return analyzer_registry.get(name).analyze(text, ctx)


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
    (kanji the fallback couldn't read) → per-character :class:`HanziChar`
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
                out.append(HanziChar(surface=ch, reading=None, span=cspan))
        prev = t
    return out
