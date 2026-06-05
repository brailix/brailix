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
  ``MISSING_READING`` cell). No wakachigaki spacing here — that is J3.

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
from brailix.ir.inline import HanziChar, InlineNode, Word

if TYPE_CHECKING:
    from brailix.core.context import FrontendContext


@dataclass(slots=True)
class JapaneseToken:
    """One morpheme: surface text, a katakana pronunciation-form reading
    (``None`` when the analyzer can't read it), the analyzer's
    part-of-speech string (used by J3 wakachigaki; informational here),
    and a span relative to the analyzed run."""

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


def tokens_to_inline(
    tokens: list[JapaneseToken], base: int = 0
) -> list[InlineNode]:
    """Convert Japanese tokens to inline IR (spans shifted by ``base``).

    A token with a reading → one :class:`Word`. A token with no reading
    (kanji the fallback couldn't read) → per-character :class:`HanziChar`
    placeholders so the backend warns ``MISSING_READING`` rather than
    mis-rendering. No word-boundary spacing yet (wakachigaki is J3).
    """
    out: list[InlineNode] = []
    for t in tokens:
        start = base + t.span.start if t.span is not None else None
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
    return out
