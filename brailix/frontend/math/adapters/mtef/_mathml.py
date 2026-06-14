"""MathML element builders and TMPL/fence translation for the MTEF adapter.

These helpers build MathML out of already-parsed pieces (accumulated CHAR
streams, converted template slots) and never read raw MTEF bytes, so they
are shared by both the v3 and v5 byte readers without depending on them.
The v3/v5 template dispatch tables and their per-selector handlers live
here because :func:`_build_tmpl` selects between them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from typing import Any

from brailix.frontend.math.adapters._atoms import classify_math_token
from brailix.frontend.math.adapters.mtef._reader import _MtefParseError
from brailix.frontend.math.utils import mrow_wrap, mtext

# ---------------------------------------------------------------------------
# Common helpers — building MathML
# ---------------------------------------------------------------------------


def _mo(text: str, **attrs: str) -> ET.Element:
    el = ET.Element("mo")
    el.text = text
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _classify_token(text: str) -> str:
    """Tag a single accumulated MTEF token — delegates to the shared
    classifier so MTEF / OMML / EQ output stays homogeneous downstream
    (see :func:`brailix.frontend.math.adapters._atoms.classify_math_token`).
    """
    return classify_math_token(text)


# Script templates whose first child is the *nucleus* (base) slot.
_SCRIPT_BASE_TAGS = frozenset({"msub", "msup", "msubsup"})


def _attach_preceding_base(
    sink: list[ET.Element], built: list[ET.Element]
) -> None:
    """Move the previous sibling into an empty script base slot.

    MathType does **not** store a script's base inside the script
    template. It emits the base as the run *preceding* the template and
    leaves the template's nucleus slot empty (a null LINE). So ``x²`` is
    the CHAR ``x`` followed by a superscript template whose base slot is
    empty; the handlers therefore build ``<msup><mrow/><mn>2</mn></msup>``
    with a hollow ``<mrow/>`` nucleus, and the real base ``x`` would
    dangle as a separate sibling — the script attaching to nothing.

    When the freshly built element is a single script with an empty base
    slot and a previous sibling exists, pop that sibling into the base
    slot. A script whose base slot is already populated (e.g. the
    defensive base-in-slot0 form some emitters use) is left untouched,
    as is a leading script with no sibling to attach to.
    """
    if not sink or len(built) != 1:
        return
    script = built[0]
    if script.tag not in _SCRIPT_BASE_TAGS or len(script) == 0:
        return
    base = script[0]
    if base.tag == "mrow" and len(base) == 0 and not (base.text or "").strip():
        script.remove(base)
        script.insert(0, sink.pop())


# ---------------------------------------------------------------------------
# CHAR → MathML (shared by v3 and v5)
# ---------------------------------------------------------------------------


def _char_to_mathml(
    char_value: int | None, embell_list: list[int]
) -> list[ET.Element]:
    """Map an MTCode / character value plus embellishments to MathML atoms.

    The base atom's tag is decided by character class: digits→``<mn>``,
    Latin / Greek letters→``<mi>``, everything else→``<mo>``. Empty /
    sentinel char values (``None``, ``0``, ``0xEEFF`` used as MathType's
    "no character" marker) yield no output.

    **PUA suppression**: MathType emits glyphs from its private fonts
    (Symbol / MTExtra / custom typeface slots) by stuffing the font-
    internal position into the MTCode field as a Unicode Private Use
    Area codepoint (``U+E000``–``U+F8FF``). These codepoints have no
    standard semantics — downstream (braille, screen reader, copy /
    paste) cannot render them meaningfully, and the actual glyph
    depends on a MathType-private font table we don't have. We treat
    the whole PUA range as sentinels and drop them, the same way
    ``0xEEFF`` (MathType's documented "no character" marker) is
    already dropped. If a specific PUA codepoint is later identified
    as a real symbol, add it to a translation table before this
    suppression check rather than inside it.

    Embellishments wrap the base in an ``<mover>``/``<munder>`` with the
    corresponding combining character (e.g. dot, prime, hat, vector).
    """
    if char_value is None or char_value == 0 or char_value == 0xEEFF:
        return []
    if 0xE000 <= char_value <= 0xF8FF:
        return []
    if 0xD800 <= char_value <= 0xDFFF:
        # UTF-16 surrogate halves.  ``chr()`` accepts them, but a lone
        # surrogate poisons the produced string: it serialises fine and
        # then blows up the UTF-8 re-encode when the normalizer parses
        # the MathML back (UnicodeEncodeError — escaping the adapter's
        # soft-failure contract).  Only a corrupt / truncated MTEF
        # stream produces one; drop it like the other sentinels.
        return []
    try:
        ch = chr(char_value)
    except ValueError:
        return [mtext(f"\\u{char_value:04x}")]
    if not ch or ch.isspace():
        return []
    tag = _classify_token(ch)
    base = ET.Element(tag)
    base.text = ch
    if not embell_list:
        return [base]
    out: ET.Element = base
    for code in embell_list:
        out = _wrap_embell(out, code)
    return [out]


_EMBELL_OVER = {
    2: "˙",   # dot above
    3: "¨",   # diaeresis
    4: "⃛",   # triple dot above
    5: "′",   # prime
    6: "″",   # double prime
    7: "‵",   # reversed prime
    8: "˜",   # tilde
    9: "^",   # caret / hat
    11: "→",  # right arrow over
    12: "←",  # left arrow over
    13: "↔",  # left-right arrow over
    14: "⃗",  # right harpoon over
    15: "⃖",  # left harpoon over
    17: "¯",  # macron / overbar
    18: "‴",  # triple prime
    19: "⌢",  # frown (arc down)
    20: "⌣",  # smile (arc up)
}

_EMBELL_UNDER = {
    16: "¯",  # underbar (single bar)
}


def _wrap_embell(base: ET.Element, code: int) -> ET.Element:
    """Wrap ``base`` in the right script container for an embell code."""
    if code == 10:  # slash — render as base + mo "/"
        mrow = ET.Element("mrow")
        mrow.append(base)
        mrow.append(_mo("̸"))  # combining long solidus overlay
        return mrow
    if code in _EMBELL_UNDER:
        container = ET.Element("munder")
        container.append(base)
        container.append(_mo(_EMBELL_UNDER[code]))
        return container
    sym = _EMBELL_OVER.get(code)
    if sym is None:
        return base
    container = ET.Element("mover")
    container.append(base)
    container.append(_mo(sym))
    return container


# ---------------------------------------------------------------------------
# TMPL builder (selector → MathML)
# ---------------------------------------------------------------------------


def _build_tmpl(
    selector: int,
    variation: int,
    slots: list[list[ET.Element]],
    *,
    version: int,
) -> list[ET.Element]:
    """Translate a TMPL record into MathML.

    Dispatch tables differ between v3 and v5 because their selector
    values were assigned independently. ``slots`` is the ordered list
    of converted sub-LINEs (one per template slot, in the order Word
    stores them).
    """
    table = _V5_TMPL if version == 5 else _V3_TMPL
    handler = table.get(selector)
    if handler is None:
        return _tmpl_passthrough(slots)
    try:
        return handler(variation, slots)
    except _MtefParseError:
        raise
    except Exception:  # noqa: BLE001 — never crash a template
        return _tmpl_passthrough(slots)


def _tmpl_passthrough(slots: list[list[ET.Element]]) -> list[ET.Element]:
    """Default for unrecognised templates: concatenate all slots."""
    out: list[ET.Element] = []
    for s in slots:
        out.extend(s)
    return out


def _slot(slots: list[list[ET.Element]], i: int) -> ET.Element:
    """Return slot ``i`` wrapped in ``<mrow>`` if it has multiple atoms.

    Falls back to an empty ``<mrow>`` if the slot is missing — keeps
    MathML well-formed even when the input is truncated.
    """
    if i >= len(slots):
        return ET.Element("mrow")
    children = slots[i]
    if not children:
        return ET.Element("mrow")
    return mrow_wrap(list(children))


# ---- Fence builders (shared) ----

_FENCE_OPEN = ["⟨", "(", "{", "[", "|", "‖", "⌊", "⌈"]
_FENCE_CLOSE = ["⟩", ")", "}", "]", "|", "‖", "⌋", "⌉"]


def _build_fence(
    open_ch: str | None,
    close_ch: str | None,
    body: list[ET.Element],
) -> list[ET.Element]:
    mrow = ET.Element("mrow")
    if open_ch:
        mrow.append(_mo(open_ch, fence="true"))
    for c in body:
        mrow.append(c)
    if close_ch:
        mrow.append(_mo(close_ch, fence="true"))
    return [mrow]


# ---- v5 selectors ----


def _v5_paren_class(idx: int):
    def handler(variation, slots, _open=_FENCE_OPEN[idx], _close=_FENCE_CLOSE[idx]):
        body = slots[0] if slots else []
        return _build_fence(_open, _close, body)
    return handler


def _v5_custom_fence(variation, slots):
    """selector 8/9 — fence whose left and right brackets are stored
    as trailing CHAR records rather than implied by the selector.

    MathType encodes half-open intervals like ``[a, b)`` and any other
    mismatched-bracket fence (``]a, b[``, ``(a, b]``, single-side
    brackets) with this template. Slot 0 carries the body; slots 1
    and 2 are single-CHAR slots holding the literal left and right
    bracket characters. The variation byte's high nibble flags
    "left bracket present" and low nibble "right bracket present"
    (0x10 / 0x02 in the bit positions we've observed), but we read
    the slots positionally so an unknown variation encoding still
    produces sensible output.
    """
    body = slots[0] if slots else []
    open_ch = _fence_char_from_slot(slots, 1)
    close_ch = _fence_char_from_slot(slots, 2)
    return _build_fence(open_ch, close_ch, body)


def _fence_char_from_slot(slots, idx):
    """Pull the bracket character out of a single-CHAR custom-fence slot.

    Returns ``None`` for missing or non-trivially-shaped slots so the
    fence builder skips that side instead of emitting a malformed
    ``<mo>`` element.
    """
    if idx >= len(slots):
        return None
    slot = slots[idx]
    if not slot or len(slot) != 1:
        return None
    text = slot[0].text
    return text or None


def _v5_radical(variation, slots):
    if len(slots) >= 2 and slots[1]:
        mroot = ET.Element("mroot")
        mroot.append(_slot(slots, 0))
        mroot.append(_slot(slots, 1))
        return [mroot]
    msqrt = ET.Element("msqrt")
    msqrt.append(_slot(slots, 0))
    return [msqrt]


def _v5_fraction(variation, slots):
    mfrac = ET.Element("mfrac")
    mfrac.append(_slot(slots, 0))
    mfrac.append(_slot(slots, 1))
    return [mfrac]


def _v5_underbar(variation, slots):
    container = ET.Element("munder")
    container.append(_slot(slots, 0))
    container.append(_mo("¯"))
    return [container]


def _v5_overbar(variation, slots):
    container = ET.Element("mover")
    container.append(_slot(slots, 0))
    container.append(_mo("¯"))
    return [container]


def _v5_arrow(variation, slots):
    container = ET.Element("mover")
    container.append(_slot(slots, 0))
    container.append(_mo("→"))
    return [container]


def _v5_bigop(op: str):
    def handler(variation, slots):
        sub = slots[1] if len(slots) > 1 and slots[1] else None
        sup = slots[2] if len(slots) > 2 and slots[2] else None
        op_el = _mo(op)
        if sub and sup:
            head = ET.Element("munderover")
            head.append(op_el)
            head.append(_slot(slots, 1))
            head.append(_slot(slots, 2))
        elif sub:
            head = ET.Element("munder")
            head.append(op_el)
            head.append(_slot(slots, 1))
        elif sup:
            head = ET.Element("mover")
            head.append(op_el)
            head.append(_slot(slots, 2))
        else:
            head = op_el
        body = slots[0] if slots else []
        mrow = ET.Element("mrow")
        mrow.append(head)
        for c in body:
            mrow.append(c)
        return [mrow]
    return handler


def _v5_limits(variation, slots):
    """selector 23 — Word's Limit template (lim_{x→0}, etc.).

    Slot 0 is the base (typically the word "lim"), slot 1 is the
    under-script.
    """
    container = ET.Element("munder")
    container.append(_slot(slots, 0))
    container.append(_slot(slots, 1))
    return [container]


def _v5_hbrace_under(variation, slots):
    container = ET.Element("munder")
    container.append(_slot(slots, 0))
    container.append(_mo("⏟"))
    return [container]


def _v5_hbrace_over(variation, slots):
    container = ET.Element("mover")
    container.append(_slot(slots, 0))
    container.append(_mo("⏞"))
    return [container]


def _v5_subscript(variation, slots):
    elem = ET.Element("msub")
    elem.append(_slot(slots, 0))
    elem.append(_slot(slots, 1))
    return [elem]


def _v5_superscript(variation, slots):
    elem = ET.Element("msup")
    elem.append(_slot(slots, 0))
    elem.append(_slot(slots, 1))
    return [elem]


def _v5_subsup(variation, slots):
    elem = ET.Element("msubsup")
    elem.append(_slot(slots, 0))
    elem.append(_slot(slots, 1))
    elem.append(_slot(slots, 2))
    return [elem]


def _v5_accent(sym: str):
    def handler(variation, slots):
        container = ET.Element("mover")
        container.append(_slot(slots, 0))
        container.append(_mo(sym))
        return [container]
    return handler


_V5_TMPL: dict[int, Callable[..., Any]] = {
    0: _v5_paren_class(0),
    1: _v5_paren_class(1),
    2: _v5_paren_class(2),
    3: _v5_paren_class(3),
    4: _v5_paren_class(4),
    5: _v5_paren_class(5),
    6: _v5_paren_class(6),
    7: _v5_paren_class(7),
    8: _v5_custom_fence,
    9: _v5_custom_fence,
    10: _v5_radical,
    11: _v5_fraction,
    12: _v5_underbar,
    13: _v5_overbar,
    14: _v5_arrow,
    15: _v5_bigop("∫"),  # integral
    16: _v5_bigop("∑"),  # sum
    17: _v5_bigop("∏"),  # product
    18: _v5_bigop("∐"),  # coproduct
    19: _v5_bigop("⋃"),  # union
    20: _v5_bigop("⋂"),  # intersection
    21: _v5_bigop("∫"),  # integral-style
    22: _v5_bigop("∑"),  # summation-style
    23: _v5_limits,
    24: _v5_hbrace_over,
    25: _v5_hbrace_under,
    27: _v5_subscript,
    28: _v5_superscript,
    29: _v5_subsup,
    31: _v5_accent("→"),  # vector
    32: _v5_accent("˜"),  # tilde
    33: _v5_accent("^"),  # hat
    34: _v5_accent("⌢"),  # arc
}


# ---- v3 selectors ----


def _v3_paren(open_ch: str, close_ch: str):
    def handler(variation, slots):
        body = slots[0] if slots else []
        return _build_fence(open_ch, close_ch, body)
    return handler


def _v3_radical(variation, slots):
    if len(slots) >= 2 and slots[1]:
        mroot = ET.Element("mroot")
        mroot.append(_slot(slots, 0))
        mroot.append(_slot(slots, 1))
        return [mroot]
    msqrt = ET.Element("msqrt")
    msqrt.append(_slot(slots, 0))
    return [msqrt]


def _v3_fraction(variation, slots):
    mfrac = ET.Element("mfrac")
    mfrac.append(_slot(slots, 0))
    mfrac.append(_slot(slots, 1))
    return [mfrac]


def _v3_slash_fraction(variation, slots):
    """selector 41 — slash-style fraction. Renders as a / b inline."""
    mrow = ET.Element("mrow")
    mrow.append(_slot(slots, 0))
    mrow.append(_mo("/"))
    mrow.append(_slot(slots, 1))
    return [mrow]


def _v3_scripts(variation, slots):
    """selector 15 — sub / sup / subsup decided by variation.

    Variation bits: 0x01=has sub, 0x02=has sup. Both → msubsup.
    """
    has_sub = bool(variation & 0x01)
    has_sup = bool(variation & 0x02)
    if has_sub and has_sup:
        elem = ET.Element("msubsup")
        elem.append(_slot(slots, 0))
        elem.append(_slot(slots, 1))
        elem.append(_slot(slots, 2))
        return [elem]
    if has_sub:
        elem = ET.Element("msub")
        elem.append(_slot(slots, 0))
        elem.append(_slot(slots, 1))
        return [elem]
    if has_sup:
        elem = ET.Element("msup")
        elem.append(_slot(slots, 0))
        elem.append(_slot(slots, 1))
        return [elem]
    return [_slot(slots, 0)]


def _v3_underbar(variation, slots):
    return [_munder_with(_slot(slots, 0), "¯")]


def _v3_overbar(variation, slots):
    return [_mover_with(_slot(slots, 0), "¯")]


def _v3_bigop(op: str):
    def handler(variation, slots):
        has_sub = bool(variation & 0x01)
        has_sup = bool(variation & 0x02)
        op_el = _mo(op)
        if has_sub and has_sup:
            head = ET.Element("munderover")
            head.append(op_el)
            head.append(_slot(slots, 1))
            head.append(_slot(slots, 2))
        elif has_sub:
            head = ET.Element("munder")
            head.append(op_el)
            head.append(_slot(slots, 1))
        elif has_sup:
            head = ET.Element("mover")
            head.append(op_el)
            head.append(_slot(slots, 2))
        else:
            head = op_el
        mrow = ET.Element("mrow")
        mrow.append(head)
        # The body (integrand / summand) follows in slot 0 — but the
        # OMML mental model puts it after the operator script. Append
        # whatever's left.
        body_slot = slots[0] if slots else []
        for c in body_slot:
            mrow.append(c)
        return [mrow]
    return handler


def _v3_limits(variation, slots):
    container = ET.Element("munder")
    container.append(_slot(slots, 0))
    container.append(_slot(slots, 1))
    return [container]


def _v3_hbrace_under(variation, slots):
    return [_munder_with(_slot(slots, 0), "⏟")]


def _v3_hbrace_over(variation, slots):
    return [_mover_with(_slot(slots, 0), "⏞")]


def _v3_left_sub_sup(variation, slots):
    """selector 44 — leading subscript/superscript (Word's pre-scripts).

    Becomes ``<mmultiscripts>`` so MathML stays semantically correct.
    """
    elem = ET.Element("mmultiscripts")
    elem.append(_slot(slots, 0))
    elem.append(ET.Element("none"))
    elem.append(ET.Element("none"))
    elem.append(ET.Element("mprescripts"))
    elem.append(_slot(slots, 1))
    elem.append(_slot(slots, 2))
    return [elem]


def _munder_with(base: ET.Element, sym: str) -> ET.Element:
    el = ET.Element("munder")
    el.append(base)
    el.append(_mo(sym))
    return el


def _mover_with(base: ET.Element, sym: str) -> ET.Element:
    el = ET.Element("mover")
    el.append(base)
    el.append(_mo(sym))
    return el


_V3_TMPL: dict[int, Callable[..., Any]] = {
    0: _v3_paren("⟨", "⟩"),
    1: _v3_paren("(", ")"),
    2: _v3_paren("{", "}"),
    3: _v3_paren("[", "]"),
    4: _v3_paren("|", "|"),
    5: _v3_paren("‖", "‖"),
    6: _v3_paren("⌊", "⌋"),
    7: _v3_paren("⌈", "⌉"),
    8: _v3_paren("{", "{"),
    9: _v3_paren("}", "}"),
    10: _v3_paren("}", "{"),
    11: _v3_paren("(", "}"),
    12: _v3_paren("(", "}"),
    13: _v3_radical,
    14: _v3_fraction,
    15: _v3_scripts,
    16: _v3_underbar,
    17: _v3_overbar,
    21: _v3_bigop("∫"),
    22: _v3_bigop("∬"),  # double integral
    23: _v3_bigop("∭"),  # triple integral
    24: _v3_bigop("∫"),
    25: _v3_bigop("∬"),
    26: _v3_bigop("∭"),
    27: _v3_hbrace_over,
    28: _v3_hbrace_under,
    29: _v3_bigop("∑"),
    30: _v3_bigop("∑"),
    31: _v3_bigop("∏"),
    32: _v3_bigop("∏"),
    33: _v3_bigop("∐"),
    34: _v3_bigop("∐"),
    35: _v3_bigop("⋃"),
    36: _v3_bigop("⋃"),
    37: _v3_bigop("⋂"),
    38: _v3_bigop("⋂"),
    39: _v3_limits,
    41: _v3_slash_fraction,
    44: _v3_left_sub_sup,
}
