"""Translate Japanese inline IR (Word / HanziChar) into braille cells.

Japanese braille (仮名点字) is fully phonetic: every prose node carries a
katakana *pronunciation form* in ``node.reading`` (long vowels already as
ー, particle は read ワ etc.). The ja frontend fills that field; at J0 the
reading is supplied directly (the backend is testable on its own, the way
:mod:`brailix.backend.zh` is).

The job here is purely ``reading -> cells``:

1. Normalise the reading to katakana (tolerate hiragana input).
2. Segment it into mora — maximal munch, so a base kana plus a following
   small ゃ/ゅ/ょ binds into one youon mora (キ + ャ -> キャ).
3. Look each mora up in the profile's ``kana`` table
   (``resources/ja/<scheme>/kana.json``) and emit one BrailleCell per
   cell. Dakuon / handakuon / youon mora resolve to two-cell sequences
   already composed in the table (the ⑤/⑥/④ prefix then the base).

Missing reading -> ``MISSING_READING`` warning + one unknown cell per
surface character. Unknown mora -> ``UNKNOWN_KANA`` warning + an unknown
cell. The pipeline never crashes on data gaps (mirrors backend.zh).

Out of J0 scope (later phases): wakachigaki spacing, the number sign +
tsunagi-fu, romaji 外字符 / 大文字符, and punctuation. Those are driven by
the frontend / number / punct / latin backends, not here.
"""

from __future__ import annotations

from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import HanziChar, Word

# Katakana small ya/yu/yo — these glue onto the preceding kana to form a
# youon mora (キ + ャ -> キャ). Other small kana (ァ ィ ゥ ェ ォ, foreign
# sounds) are left standalone; they belong to the 外来語 rules of a later
# phase and miss the table gracefully until then.
_SMALL_YOUON = ("ャ", "ュ", "ョ")

# Hiragana block — shifted to katakana by +0x60 so the table (katakana-
# keyed) can be looked up regardless of which kana the reading uses.
_HIRAGANA_LO = 0x3041
_HIRAGANA_HI = 0x3096
_KANA_SHIFT = 0x60


def translate_word(
    node: Word, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    return _translate_japanese(
        node.surface, node.reading, node.span, ctx, profile
    )


def translate_hanzi_char(
    node: HanziChar, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    return _translate_japanese(
        node.surface, node.reading, node.span, ctx, profile
    )


def _to_katakana(reading: str) -> str:
    out: list[str] = []
    for ch in reading:
        code = ord(ch)
        if _HIRAGANA_LO <= code <= _HIRAGANA_HI:
            out.append(chr(code + _KANA_SHIFT))
        else:
            out.append(ch)
    return "".join(out)


def _split_mora(reading: str) -> list[str]:
    """Segment a katakana reading into mora (maximal munch on youon)."""
    kata = _to_katakana(reading)
    mora: list[str] = []
    i, n = 0, len(kata)
    while i < n:
        if i + 1 < n and kata[i + 1] in _SMALL_YOUON:
            mora.append(kata[i : i + 2])
            i += 2
        else:
            mora.append(kata[i])
            i += 1
    return mora


def _translate_japanese(
    surface: str,
    reading: str | None,
    span: Span | None,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    if not surface:
        return []
    if not reading:
        # No kana reading — emit one unknown cell per character and warn
        # so the proofreader sees what fell through (mirrors backend.zh).
        ctx.warnings.warn(
            code="MISSING_READING",
            message=f"no kana reading for {surface!r}",
            surface=surface,
            span=span,
            source="backend.ja",
        )
        return [
            BrailleCell(
                dots=(),
                role="unknown",
                source_span=_char_span(span, i),
                source_text=ch,
            )
            for i, ch in enumerate(surface)
        ]

    kana_table = profile.lang_table("kana")
    out: list[BrailleCell] = []
    for mora in _split_mora(reading):
        seq = kana_table.get(mora)
        if seq is None:
            ctx.warnings.warn(
                code="UNKNOWN_KANA",
                message=(
                    f"no braille cell for mora {mora!r} "
                    f"(in {surface!r} / {reading!r})"
                ),
                surface=mora,
                span=span,
                source="backend.ja",
            )
            out.append(
                BrailleCell(
                    dots=(), role="unknown", source_span=span, source_text=mora
                )
            )
            continue
        for dots in seq:
            out.append(
                BrailleCell(
                    dots=dots,
                    role="ja_kana",
                    source_span=span,
                    source_text=mora,
                )
            )
    return out


def _char_span(parent: Span | None, index: int) -> Span | None:
    if parent is None:
        return None
    return Span(parent.start + index, parent.start + index + 1)
