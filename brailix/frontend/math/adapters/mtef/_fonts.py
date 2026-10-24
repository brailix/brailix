"""MathType private-font glyph translation for the MTEF adapter.

MathType / Equation Editor store characters from *symbolic* fonts
(Symbol, MT Extra) by font-internal position rather than by Unicode:

* **v5** — the CHAR record's ``MTCode`` value carries real Unicode for
  characters that have one, but characters whose glyph lives only in a
  private font arrive as a Private Use Area codepoint (``U+F0xx``,
  low byte = font position — the Windows Symbol-charset convention),
  or with the MTCode omitted entirely (``mtefOPT_CHAR_ENC_NO_MTCODE``)
  and only an 8/16-bit *font position* left to identify the glyph.
* **v3** — the 16-bit character value is "a Unicode value if the font
  is a text font or a font index if the font is symbolic" (MTEF v3
  spec): a Symbol-font ``α`` is stored as position ``0x61``, which is
  why the reader must know the font before it can name the character.

Which font a CHAR belongs to comes from its typeface field (unbiased):
positive values are MathType *styles* (``4`` fnLCGREEK / ``5``
fnUCGREEK / ``6`` fnSYMBOL all render through the Symbol font; ``11``
is MT Extra), negative values reference an explicit FONT / FONT_DEF
record by number.

This module owns:

* :data:`_SYMBOL_POSITIONS` — the Symbol font byte-position → Unicode
  table;
* :data:`_MTEXTRA_POSITIONS` — the MT Extra font byte-position table
  (real-Unicode targets only; see the table's comment for provenance
  and adjudications);
* :data:`_MTCODE_PUA` / :data:`_MTCODE_SPACING` — MathType's own
  Private Use Area assignments on the *MTCode value* itself,
  independent of any font: ``U+E9xx`` extended operators, ``U+EAxx``
  negated relations, a few ``U+EBxx`` arrow combinations,
  ``U+EDxx`` letterlike constants, and the ``U+EF00..EF08`` custom
  spacing values (dropped silently — braille spacing is
  backend-driven);
* :class:`_FontEnv` — per-equation walk state: the explicit-font table
  gathered from FONT / FONT_DEF records plus the recorded degradations
  that end up as the ``data-bk-degraded`` attribute on the ``<math>``
  root;
* the resolve / translate helpers the v3 / v5 readers call.

Provenance — the tables were built from the official Design Science
encoding pages (archived at web.archive.org,
``dessci.com/en/support/mathtype/tech/encodings/{mtextra,symbol}.htm``)
cross-checked against two independent open-source MTEF converters: the
transpect project's machine-readable ``MathType_MTCode.xml`` fontmap
and jure/mathtype_to_mathml's ``char.xsl`` overrides. Where the three
disagree, the official glyph names decide (e.g. ``EBxx`` are arrow
combinations, not spaces as transpect's fontmap claims; ``E98F`` is
the "medium dot operator" → ``·`` per jure, not the z-notation spot).
A handful of entries were dropped as unverifiable (``E90D`` targets an
unassigned codepoint) or as layout fragments (harpoon-over combos,
multi-space wide accents).

References:

* `MTEF v.3 <https://rtf2latex2e.sourceforge.net/MTEF3.html>`_ (typeface
  bias, FONT records, "font index if the font is symbolic")
* `MTEF v.5 <https://rtf2latex2e.sourceforge.net/MTEF5.html>`_ (CHAR
  option bits, FONT_DEF, font positions)
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from dataclasses import field as _field

# Cap on recorded degradations per equation: the attribute rides the
# MathML island (and therefore the editable markdown), so a formula
# with hundreds of unknown templates must not blow the text up.
_MAX_DEGRADATIONS = 10

# Typeface styles (unbiased, shared numbering between v3 and v5) whose
# characters come from a private/symbolic font. Greek variable styles
# (4 fnLCGREEK, 5 fnUCGREEK) are rendered through the Symbol font in
# MathType, as is fnSYMBOL (6); style 11 is MT Extra.
_STYLE_FONTS: dict[int, str] = {
    4: "Symbol",
    5: "Symbol",
    6: "Symbol",
    11: "MT Extra",
}

# Font names (case/space-insensitive) recognised as the Symbol font.
# MathType writes "Symbol"; some clones ship "SymbolMT" with the same
# byte layout. The real fixture also carries "MT Extra" (with a space)
# — normalised comparisons handle both spellings.
_SYMBOL_FONT_NAMES: frozenset[str] = frozenset({"symbol", "symbolmt"})
_MTEXTRA_FONT_NAMES: frozenset[str] = frozenset({"mtextra", "mt extra"})


def _normalise_font_name(name: str | None) -> str:
    return "".join((name or "").split()).casefold()


def _font_kind(font_name: str | None) -> str | None:
    """Classify a font name: ``"symbol"`` / ``"mtextra"`` / ``None``."""
    key = _normalise_font_name(font_name)
    if key in _SYMBOL_FONT_NAMES:
        return "symbol"
    if key in _MTEXTRA_FONT_NAMES:
        return "mtextra"
    return None


# The Symbol font byte-position → Unicode table. Layout follows the
# public Symbol encoding, cross-checked position-by-position against the
# official MathType "Symbol encoding" page (archived Dessci
# documentation): the Greek scrambles and all operator spots verified
# exactly (0x61 α, 0xB4 ×, 0xCE ∈, 0xD5 ∏, 0xF2 ∫, ...). Entries where
# the official table settles an Apple-vs-Adobe ambiguity take the
# official value (0x27 is ∍ U+220D "small contains as member"; 0x40 is
# ≅ U+2245 "approximately equal to", which the Apple chart omits).
#
# Deliberate reinterpretations, because a braille edition needs the
# *mathematical* reading, not the typographic one:
#   0xA4 fraction slash ⁄ → "/" (solidus — what it means in a formula)
#   0xB7 bullet •         → "∙" U+2219 bullet operator (official 2022)
#   0xBC ellipsis …       → "⋯" U+22EF midline ellipsis (official 2026;
#                          the prose … is a rejected prose-punctuation
#                          character in the math domain and would raise
#                          a bogus writing-error warning)
#   0xE1 / 0xF1 angle brackets → U+27E8/U+27E9 (official 2329/232A, the
#                          CJK-compat forms; 27E8/27E9 are the proper
#                          mathematical pair and match _FENCE_OPEN)
#
# Deliberate omissions (absent → visible soft failure, addable later):
#   0x60 radicalex / 0xBD ⏐ / 0xBE ⎯ — layout extension pieces
#   0xE6-0xEF, 0xF3-0xFE — big-bracket / integral extension pieces
#   0xF0 Apple logo, 0x80-0x9F, 0xFF — undefined positions
_SYMBOL_POSITIONS: dict[int, str] = {
    0x20: " ", 0x21: "!", 0x22: "∀", 0x23: "#", 0x24: "∃",
    0x25: "%", 0x26: "&", 0x27: "∍", 0x28: "(", 0x29: ")",
    0x2A: "∗", 0x2B: "+", 0x2C: ",", 0x2D: "−", 0x2E: ".",
    0x2F: "/", 0x40: "≅",
    # Greek uppercase in the Symbol font's (alphabet-scrambled) order.
    0x41: "Α", 0x42: "Β", 0x43: "Χ", 0x44: "Δ", 0x45: "Ε",
    0x46: "Φ", 0x47: "Γ", 0x48: "Η", 0x49: "Ι", 0x4A: "ϑ",
    0x4B: "Κ", 0x4C: "Λ", 0x4D: "Μ", 0x4E: "Ν", 0x4F: "Ο",
    0x50: "Π", 0x51: "Θ", 0x52: "Ρ", 0x53: "Σ", 0x54: "Τ",
    0x55: "Υ", 0x56: "ς", 0x57: "Ω", 0x58: "Ξ", 0x59: "Ψ",
    0x5A: "Ζ",
    0x5B: "[", 0x5C: "∴", 0x5D: "]", 0x5E: "⊥", 0x5F: "_",
    # Greek lowercase in the same scrambled order (J=ϕ, V=ϖ variants).
    0x61: "α", 0x62: "β", 0x63: "χ", 0x64: "δ", 0x65: "ε",
    0x66: "φ", 0x67: "γ", 0x68: "η", 0x69: "ι", 0x6A: "ϕ",
    0x6B: "κ", 0x6C: "λ", 0x6D: "μ", 0x6E: "ν", 0x6F: "ο",
    0x70: "π", 0x71: "θ", 0x72: "ρ", 0x73: "σ", 0x74: "τ",
    0x75: "υ", 0x76: "ϖ", 0x77: "ω", 0x78: "ξ", 0x79: "ψ",
    0x7A: "ζ",
    0x7B: "{", 0x7C: "|", 0x7D: "}", 0x7E: "~",
    0xA0: "€", 0xA1: "ϒ", 0xA2: "′", 0xA3: "≤", 0xA4: "/",
    0xA5: "∞", 0xA6: "ƒ", 0xA7: "♣", 0xA8: "♦", 0xA9: "♥",
    0xAA: "♠", 0xAB: "↔", 0xAC: "←", 0xAD: "↑", 0xAE: "→",
    0xAF: "↓",
    0xB0: "°", 0xB1: "±", 0xB2: "″", 0xB3: "≥", 0xB4: "×",
    0xB5: "∝", 0xB6: "∂", 0xB7: "∙", 0xB8: "÷", 0xB9: "≠",
    0xBA: "≡", 0xBB: "≈", 0xBC: "⋯", 0xBF: "↵",
    0xC0: "ℵ", 0xC1: "ℑ", 0xC2: "ℜ", 0xC3: "℘", 0xC4: "⊗",
    0xC5: "⊕", 0xC6: "∅", 0xC7: "∩", 0xC8: "∪", 0xC9: "⊃",
    0xCA: "⊇", 0xCB: "⊄", 0xCC: "⊂", 0xCD: "⊈", 0xCE: "∈",
    0xCF: "∉",
    0xD0: "∠", 0xD1: "∇", 0xD2: "®", 0xD3: "©", 0xD4: "™",
    0xD5: "∏", 0xD6: "√", 0xD7: "⋅", 0xD8: "¬", 0xD9: "∧",
    0xDA: "∨", 0xDB: "⇔", 0xDC: "⇐", 0xDD: "⇑", 0xDE: "⇒",
    0xDF: "⇓",
    0xE0: "◊", 0xE1: "⟨", 0xE2: "®", 0xE3: "©", 0xE4: "™",
    0xE5: "∑",
    0xF1: "⟩", 0xF2: "∫",
}
# Positions 0x30-0x3F are ASCII in every Symbol variant (digits and
# the :;<=>? relations) — fill them as identity rather than spelling
# out sixteen identical entries.
_SYMBOL_POSITIONS.update(
    {pos: chr(pos) for pos in range(0x30, 0x40)}
)

# The MT Extra font byte-position table, from the official MathType
# "MTExtra encoding" page (archived Dessci documentation; the full
# 0x20-0xFF list). Only positions whose official MTCode target is a
# *real* Unicode character with genuine content are listed — the
# official table also maps many positions to MathType-PUA codepoints
# (ECxx bracket pieces, EE0x multi-space wide accents, EBxx arrow-over
# combos, D1-D6 integral loops), which are layout fragments: those stay
# visible soft failures. When a CHAR carries an MTCode at all, the PUA
# targets resolve through :data:`_MTCODE_PUA` instead, so this table
# mainly serves font-position-only CHAR records (v5 without MTCode, and
# all v3 symbolic values).
#
# Deliberate reinterpretations (same policy as the Symbol table):
#   0x4B horizontal ellipsis 2026 → "⋯" U+22EF (0x4C already is 22EF)
#   0x7B / 0x7D over/under curly bracket FE38/FE37 → "⏟"/"⏞" (the
#          CJK-compat presentation forms → proper math brackets)
#   0xA7 / 0xA8 white square brackets 301A/301B → "⟦"/"⟧" (ditto)
#   0xF9 fraction slash 2044 → "/" (solidus — same as Symbol 0xA4)
#
# Deliberate omissions: 0x23-0x26 combining embellishment marks (a
# standalone combining char renders as nothing), 0x6E FFFD "unknown",
# 0xE7 Apple logo, 0xFB-0xFF combining accents, and every position
# whose official target is MathType-PUA layout (see above).
_MTEXTRA_POSITIONS: dict[int, str] = {
    0x20: " ",
    0x28: "⌣", 0x29: "⌢",
    0x3A: "∼", 0x3B: "≃", 0x3C: "◃", 0x3D: "≪", 0x3E: "▹",
    0x3F: "≫", 0x40: "≜", 0x41: "≙", 0x42: "≐", 0x43: "∐",
    0x44: "ƛ",
    0x49: "∩", 0x4A: "℧", 0x4B: "⋯", 0x4C: "⋯", 0x4D: "⋮",
    0x4E: "⋰", 0x4F: "⋱", 0x50: "∥", 0x51: "∵", 0x52: "∡",
    0x53: "∢",
    0x55: "∪", 0x56: "△", 0x57: "□", 0x58: "▭", 0x59: "▱",
    0x5A: "↗", 0x5B: "↙",
    0x5D: "↘", 0x5E: "↖",
    0x60: "‵", 0x61: "↦", 0x62: "↕", 0x63: "⇕", 0x64: "○",
    0x65: "⊙", 0x66: "≻", 0x67: "·", 0x68: "ℏ",
    0x6C: "ℓ", 0x6D: "∓", 0x6F: "∘", 0x70: "≺",
    0x7B: "⏟",
    0x7D: "⏞",
    0x80: "⇄",
    0x83: "⇌",
    0x86: "⇀", 0x87: "↽",
    0xA1: "ℝ", 0xA2: "ℤ", 0xA3: "ℂ", 0xA4: "ℚ", 0xA5: "ℕ",
    0xA7: "⟦", 0xA8: "⟧",
    0xE8: "Š", 0xE9: "Ý", 0xEA: "Þ", 0xEB: "Ð", 0xEC: "š",
    0xED: "ý", 0xEE: "þ", 0xEF: "ð", 0xF0: "ﬁ", 0xF1: "ﬂ",
    0xF2: "ı", 0xF3: "¹", 0xF4: "²", 0xF5: "³", 0xF6: "½",
    0xF7: "¼", 0xF8: "¾", 0xF9: "/", 0xFA: "¦",
}

# MathType's own Private Use Area assignments on the MTCode *value* —
# font-independent (a CHAR of any typeface can carry one). Built from
# the transpect MathType_MTCode.xml fontmap cross-checked against
# jure/mathtype_to_mathml's char.xsl overrides; where they disagree the
# official glyph names decide (see the module docstring). Ranges:
# U+E900..E9FF extended operators, U+EA00..EA7F negated / subset
# relations, U+EB01..EB06 arrow combinations, U+ED00..ED16 letterlike
# constants, U+EE19 quadruple prime, U+EF80..EF82 integral variants.
# Absent values (ECxx pieces, harpoon combos, wide accents, anything
# unverified) fall through to the visible soft-failure marker.
_MTCODE_PUA: dict[int, str] = {
    0xE901: "⩲", 0xE902: "⩱", 0xE903: "⨦", 0xE904: "⨤",
    0xE908: "⪜", 0xE909: "⪛", 0xE90A: "⋾", 0xE90B: "⊇",
    0xE90C: "⊆", 0xE912: "⋷", 0xE913: "⪚", 0xE914: "⫈",
    0xE915: "⫇", 0xE916: "⪾", 0xE917: "⪽", 0xE918: "⩦",
    0xE919: "⨫", 0xE91A: "⨬", 0xE91F: "⩬", 0xE920: "⩏",
    0xE921: "⩎", 0xE922: "⋚", 0xE924: "⩪", 0xE925: "⩫",
    0xE926: "⪋", 0xE927: "⪌", 0xE928: "≲", 0xE929: "≳",
    0xE92A: "⫍", 0xE92B: "⫎", 0xE92C: "⩧", 0xE92D: "⋛",
    0xE92E: "|", 0xE92F: "ǁ", 0xE930: "⫼", 0xE932: "≲",
    0xE933: "≳", 0xE936: "⪡", 0xE937: "⪢", 0xE93A: "≾",
    0xE93B: "≿", 0xE940: "⪳", 0xE941: "⪴", 0xE948: "⫤",
    0xE949: "∾", 0xE94A: "⊣", 0xE94B: "⩵", 0xE94C: "⩶",
    0xE94D: "⧴", 0xE94E: "≡", 0xE950: "⧏", 0xE951: "⧐",
    0xE954: "⊃", 0xE957: "⫊", 0xE958: "⫉", 0xE959: "⪐",
    0xE95A: "⪏", 0xE95C: "⩓", 0xE95D: "⩔", 0xE95E: "⩣",
    0xE95F: "⩢", 0xE962: "⩰", 0xE966: "⩸", 0xE96B: "⫷",
    0xE96C: "⫸", 0xE96D: "≏", 0xE96E: "⪤", 0xE96F: "⩙",
    0xE970: "⫖", 0xE971: "⫕", 0xE972: "⫔", 0xE973: "⫓",
    0xE979: "⫴", 0xE97B: "⫨", 0xE97C: "⫣", 0xE97D: "⫥",
    0xE97E: "⫩", 0xE97F: "⫤", 0xE980: "⦡", 0xE981: "⫽",
    0xE982: "⦜", 0xE984: "⦶", 0xE985: "⨸", 0xE98A: "⫡",
    0xE98B: "⦞", 0xE98C: "⦠", 0xE98E: "⥾", 0xE98F: "·",
    0xE990: "⧋", 0xE992: "⨳", 0xE994: "⧥", 0xE995: "⩩",
    0xE996: "⫰", 0xE998: "☾", 0xE999: "☺", 0xE9A5: "⬡",
    0xE9A6: "⨤", 0xE9A7: "⨦",
    0xEA06: "≰", 0xEA07: "≱", 0xEA0B: "⊉", 0xEA0C: "⊈",
    0xEA32: "⪉", 0xEA33: "⪊", 0xEA34: "≨", 0xEA35: "≩",
    0xEA3A: "⋨", 0xEA3B: "⋩", 0xEA40: "⪵", 0xEA41: "⪶",
    0xEA42: "⊈", 0xEA43: "⊉", 0xEA44: "⊊", 0xEA45: "⊋",
    0xEB01: "⥂", 0xEB02: "⥄", 0xEB05: "⤢", 0xEB06: "⤡",
    0xED01: "⅁", 0xED02: "𝚥", 0xED03: "ϝ", 0xED04: "ⅆ",
    0xED05: "ⅇ", 0xED06: "ⅈ", 0xED07: "ⅉ", 0xED08: "ⅅ",
    0xED10: "ⅆ", 0xED11: "ⅇ", 0xED12: "ⅈ", 0xED13: "ⅉ",
    0xED16: "ⅅ",
    0xEE19: "⁗",
    0xEF80: "∲", 0xEF81: "⨖", 0xEF82: "⨏",
}

# MathType's custom spacing values (thin / em / hair / zero-width
# variants and the alignment mark) — observed in real Word documents
# (e.g. U+EF04). They carry no braille meaning: spacing inside a
# formula is the backend's own rule. Dropped silently, like a plain
# space, instead of becoming a visible marker each time.
_MTCODE_SPACING: frozenset[int] = frozenset(range(0xEF00, 0xEF09))


def _is_pua(value: int) -> bool:
    """True for the Unicode Private Use Area (U+E000..U+F8FF)."""
    return 0xE000 <= value <= 0xF8FF


def translate_font_position(position: int, font_name: str | None) -> str | None:
    """A private-font byte position → the Unicode math character, or ``None``.

    ``position`` may also be a full PUA codepoint (``U+F0xx``): the
    Windows Symbol-charset convention stores positions there, and the
    low byte is the position. Only ``U+F000..U+F0FF`` unwraps, and only
    for the Symbol font — that offset convention is a Symbol-charset
    artefact, not a general PUA rule. A caller that can't name the font
    gets ``None`` (and the reader turns that into the visible marker).
    """
    kind = _font_kind(font_name)
    if kind is None:
        return None
    if kind == "symbol" and 0xF000 <= position <= 0xF0FF:
        position -= 0xF000
    if not 0 <= position <= 0xFF:
        return None
    table = _SYMBOL_POSITIONS if kind == "symbol" else _MTEXTRA_POSITIONS
    return table.get(position)


@_dataclass(slots=True)
class _FontEnv:
    """Per-equation walk state shared by the v3 / v5 readers.

    ``explicit_fonts`` maps a font number → font name, gathered from
    the definition records as the walk sees them:

    * **v3** FONT records carry their own ``tface`` number — keyed
      directly by it (the spec: a FONT record's ``tface`` is the
      negated unbiased typeface a CHAR references it with).
    * **v5** FONT_DEF records carry an *encoding* index plus a name,
      in encounter order — keyed 1-based by that order. MathType
      itself almost always uses positive style typefaces instead (the
      real-world fixtures do), so this convention only matters for
      hand-built or third-party emitters.

    ``degradations`` accumulates human-readable reasons (unknown
    templates, ...) that :func:`_convert_v3` / :func:`_convert_v5` join
    onto the ``<math>`` root as ``data-bk-degraded`` — the marker the
    math frontend turns into a visible warning at parse time.
    """

    explicit_fonts: dict[int, str] = _field(default_factory=dict)
    degradations: list[str] = _field(default_factory=list)

    def resolve_typeface(self, unbiased_typeface: int) -> str | None:
        """The font name a CHAR's unbiased typeface value refers to.

        Positive values are MathType styles — only the private-font
        styles (Greek / Symbol / MT Extra) resolve to a name; text
        styles (variable, number, function, ...) resolve to ``None``
        because their character values are already Unicode. Negative
        values reference an explicit font definition by number.
        """
        if unbiased_typeface < 0:
            return self.explicit_fonts.get(-unbiased_typeface)
        return _STYLE_FONTS.get(unbiased_typeface)

    def note_degradation(self, reason: str) -> None:
        if len(self.degradations) < _MAX_DEGRADATIONS:
            self.degradations.append(reason)
        elif len(self.degradations) == _MAX_DEGRADATIONS:
            self.degradations.append("(more degradations not recorded)")
