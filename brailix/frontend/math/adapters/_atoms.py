"""Shared lexer that splits a literal text run into MathML leaf atoms.

The OMML, MTEF, and EQ-field adapters all face the same sub-problem: a run
of plain text (``2x+1``, a Word ``<m:t>`` payload, an accumulated MTEF
CHAR stream) must become ``<mn>`` / ``<mi>`` / ``<mo>`` atoms so the math
backend can apply per-class rules (number sign, identifier style, ...).
They used to carry three near-identical copies that had drifted — different
operator sets, different comma handling. This is the one implementation.

The single genuine source-format difference is whether ``,`` groups inside
a number: OMML / MTEF text can carry thousands-grouped numbers (``1,234``),
whereas in EQ-field syntax ``,`` is the argument separator and must never be
swallowed into a number (``\\f(1,2)`` is two operands). That difference is
the explicit ``comma_in_number`` flag, not accidental drift.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

# Symbols that read as operators even when adjacent to identifiers. This is
# the union of what the three adapters historically listed; the extra
# relation glyphs (≈ ≡ ≜ ≝) are all non-alphabetic, so widening the set is
# behaviour-preserving — they already fell through to the ``<mo>`` branch.
MATH_OPERATORS: frozenset[str] = frozenset(
    "+-=<>±×÷·∑∏∫√≤≥≠∞∂∇∈∉⊂⊃∪∩→←↔⇒⇔∀∃≈≡≜≝"
)


def is_identifier_char(ch: str) -> bool:
    """Identifier = ASCII letter or Greek letter (and not a known operator)."""
    if ch in MATH_OPERATORS:
        return False
    if ch.isalpha() and ord(ch) < 0x80:
        return True
    if 0x0370 <= ord(ch) <= 0x03FF and ch.isalpha():  # Greek block
        return True
    return False


def tokenize_math_text(text: str, *, comma_in_number: bool = True) -> list[ET.Element]:
    """Split ``text`` into ``<mn>`` / ``<mi>`` / ``<mtext>`` / ``<mo>`` atoms.

    Digit runs (with ``.``, and ``,`` when ``comma_in_number``) coalesce
    into one ``<mn>``; runs of identifier chars into one ``<mi>``; runs of
    natural-language letters that aren't math identifiers (CJK, kana,
    Cyrillic, accented Latin — ``isalpha`` but outside the ASCII/Greek
    identifier set) coalesce into one ``<mtext>``, which the backend hands
    to the injected inline-text translator (e.g. the Chinese condition
    「当x>0时」in a Word formula); every other non-space character becomes
    its own single-char ``<mo>``. Whitespace is dropped (sources insert it
    only for visual padding).
    """
    number_seps = ".," if comma_in_number else "."
    out: list[ET.Element] = []
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        # ``isdigit`` here is deliberately broader than the segmenter's strict
        # ASCII+fullwidth ``_is_digit``: a math source run (OMML <m:t>, MTEF / EQ
        # char stream) must let a fullwidth digit reach <mn> so the *backend*
        # warns on it (emit_digit_run fold_nonascii=False) rather than the
        # frontend silently dropping it. The wider net also folds a stray
        # superscript / circled digit into <mn>, an accepted harmless edge in an
        # already-known-math run. (The prose segmenter is strict because there a
        # superscript must split off; see segment._is_digit.)
        if ch.isdigit() or (ch in number_seps and i + 1 < n and text[i + 1].isdigit()):
            j = i
            while j < n and (
                text[j].isdigit()
                or (text[j] in number_seps and j + 1 < n and text[j + 1].isdigit())
            ):
                j += 1
            atom = ET.Element("mn")
            atom.text = text[i:j]
            out.append(atom)
            i = j
            continue
        if is_identifier_char(ch):
            j = i
            while j < n and is_identifier_char(text[j]):
                j += 1
            atom = ET.Element("mi")
            atom.text = text[i:j]
            out.append(atom)
            i = j
            continue
        if ch.isalpha():
            # A letter that isn't a math identifier (CJK / kana / Cyrillic /
            # accented Latin) is natural-language text, not a per-char
            # operator. Coalesce the run into <mtext> so the backend routes
            # it through the inline-text translator instead of emitting one
            # MATH_UNKNOWN_SYMBOL per character.
            j = i
            while j < n and text[j].isalpha() and not is_identifier_char(text[j]):
                j += 1
            atom = ET.Element("mtext")
            atom.text = text[i:j]
            out.append(atom)
            i = j
            continue
        atom = ET.Element("mo")
        atom.text = ch
        out.append(atom)
        i += 1
    return out


def classify_math_token(text: str) -> str:
    """Tag an already-isolated token (no internal splitting).

    ``mn`` for an all-digit/separator run, ``mi`` for an all-identifier
    run, ``mo`` for a lone other character, else ``mtext``. Used where a
    token is already split out (MTEF's matrix / pile walker), as opposed
    to :func:`tokenize_math_text` which splits a mixed run.
    """
    if not text:
        return "mtext"
    if text[0].isdigit() or (text[0] in ".," and len(text) > 1 and text[1].isdigit()):
        return "mn" if all(ch.isdigit() or ch in ".," for ch in text) else "mtext"
    if all(is_identifier_char(ch) for ch in text):
        return "mi"
    if len(text) == 1 and not text.isalpha():
        return "mo"
    # A lone natural-language letter (CJK / kana / …) or any multi-char run
    # is text, not an operator — route it through <mtext> so it reaches the
    # inline-text translator rather than becoming MATH_UNKNOWN_SYMBOL.
    return "mtext"
