"""Classification of non-standard input characters, for diagnostics.

Shared by the prose, math, and chemistry backends so a stray full-width
symbol or an invisible zero-width character is reported the same actionable
way everywhere — naming the *writing* problem instead of a bare "unknown".

Nothing here rewrites input: a full-width ``＝`` (U+FF1D) and a half-width
``=`` (U+003D) are different code points, and the translator never silently
folds one into the other. It only classifies the character so the warning
can tell the writer what to fix.
"""

from __future__ import annotations

# Zero-width / invisible formatting characters (ZWSP, ZWNJ, ZWJ, word joiner,
# BOM / ZWNBSP) — usually paste artefacts from the web, a PDF, or Word.
_ZERO_WIDTH_CPS: frozenset[int] = frozenset(
    {0x200B, 0x200C, 0x200D, 0x2060, 0xFEFF}
)


def nonstandard_char_hint(text: str) -> str | None:
    """An actionable hint when ``text`` is a single non-standard character:
    a full-width ASCII variant (naming its half-width form), a full-width
    space, or an invisible zero-width char. Returns ``None`` for an ordinary
    character, so callers append the hint only when there is one to give."""
    if len(text) != 1:
        return None
    cp = ord(text)
    if 0xFF01 <= cp <= 0xFF5E:
        return (
            f"full-width '{text}' (U+{cp:04X}); use the half-width "
            f"'{chr(cp - 0xFEE0)}'"
        )
    if cp == 0x3000:
        return "full-width space (U+3000); use a normal space"
    if cp in _ZERO_WIDTH_CPS:
        return f"zero-width / invisible character (U+{cp:04X})"
    return None
