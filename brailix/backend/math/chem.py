"""Chemistry-mode emission for the math backend.

Reached when a MathML subtree carries the ``data-bk-chem`` attribute,
set by the mhchem ``\\ce`` source adapter and honoured by the dispatcher
the same way as ``data-bk-span``. Chemistry braille differs from maths on
the points the project's braille domain expert specified:

* **Subscripts carry no subscript indicator.** The digit uses its lowered
  Antoine form directly — H₂O's ``2`` is ⠆ (``digits_lower["2"]``), with
  no ``script.sub`` marker, no number sign, no closing marker.
* **Casing is decided per molecule.** A molecule whose elements are all
  single letters gets one leading chemical-formula indicator ⠸
  (``chem.indicator``) and bare element letters (``H2O`` → ⠸ H ⠆ O). A
  molecule containing a multi-letter element (Si, Na, Cl) drops the ⠸ and
  prefixes each element's first letter with the capital sign ⠠
  (``letter_prefix.latin_upper``), writing the
  rest bare (``H2SiO3`` → ⠠H ⠆ ⠠Si ⠠O ⠒). In an equation each species
  decides independently (``Na + H2`` → ⠠Na … ⠸H₂).
* **Coefficients and the operators ``+`` / ``=`` / ⇌ reuse maths.** They are
  emitted through the ordinary number / operator handlers (coefficient =
  number-sign + digit; ``+`` / ``=`` keep maths spacing). Only the gas ↑ /
  precipitate ↓ arrows are chem-specific (attached, no leading space).

All cells come from the profile tables; the rules live here in the
backend, the only layer allowed to own braille output decisions.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.utils import (
    _ROLE_TO_CELL_ROLE,
    _last_is_blank,
    _unknown_cell,
)
from brailix.ir.braille import BLANK_CELL, BrailleCell

# Gas / precipitate arrows attach to the formula with no leading space —
# unlike ``+`` / ``=`` / ⇌, which keep ordinary maths operator spacing.
_ATTACHED_ARROWS = frozenset({"↑", "↓"})

# Charge-sign chars as they reach :func:`_emit_sign_cells` for the symbol
# lookup. ``"+"`` resolves in the symbol table directly; the minus charge
# maps to the maths minus sign U+2212 (ASCII ``"-"`` is not a symbol key).
_CHARGE_PLUS = "+"
_CHARGE_MINUS = "−"


def emit_children(
    cells: list[BrailleCell], mctx: MathBrailleContext, children: list[ET.Element]
) -> None:
    """Emit a chem container's children, grouping consecutive element nodes
    into molecules so each molecule gets its own casing decision + leading
    chemical-formula indicator. Coefficients (``<mn>``), the ``+`` operator and
    the ``=`` / ⇌ reaction connectors fall through to the ordinary maths paths.
    """
    from brailix.backend.math.dispatch import _emit_element

    i = 0
    n = len(children)
    while i < n:
        if _is_element_node(children[i]):
            # A molecule run is its element nodes plus any *structural* bonds
            # (``=`` double / ``#`` triple) between them — the bond stays inside
            # the run so the whole molecule carries one leading ⠸, not a fresh
            # indicator after every bond. (The spaced reaction connector is NOT
            # a structural bond, so it still splits runs.)
            j = i + 1
            while j < n and (
                _is_element_node(children[j])
                or _is_structural_bond_node(children[j])
            ):
                j += 1
            # Never let a run end on a dangling bond (a bond joins two atoms).
            while j > i + 1 and _is_structural_bond_node(children[j - 1]):
                j -= 1
            emit_molecule(cells, mctx, children[i:j])
            i = j
        else:
            _emit_element(cells, mctx, children[i])
            i += 1


def _is_structural_bond_node(node: ET.Element) -> bool:
    """A structural bond ``<mo>`` (the frontend's tight ``=`` double / ``#`` ≡
    triple), tagged ``data-bk-chem-bond``. It lives *inside* a molecule run, so
    it doesn't trigger a fresh chemical-formula indicator the way the spaced
    reaction connector does."""
    return node.tag == "mo" and node.get("data-bk-chem-bond") is not None


def _is_element_node(node: ET.Element) -> bool:
    """An element symbol (``<mi>``) or an element carrying a subscript /
    superscript (``<msub>`` / ``<msup>`` / ``<msubsup>`` whose base is an
    ``<mi>``). These nodes make up a molecule; coefficients and operators
    are everything else."""
    if node.tag == "mi":
        return True
    if node.tag in ("msub", "msup", "msubsup"):
        return len(node) >= 1 and node[0].tag == "mi"
    return False


def _is_charge_node(node: ET.Element) -> bool:
    """True for an ion's charge node — an ``<msup>`` / ``<msubsup>`` over an
    element ``<mi>`` base. In chemistry mode an upper script is always a
    charge (chemistry has no mathematical exponents), so a charged species
    is recognised structurally, with no extra attribute."""
    return (
        node.tag in ("msup", "msubsup")
        and len(node) >= 1
        and node[0].tag == "mi"
    )


def emit_molecule(
    cells: list[BrailleCell], mctx: MathBrailleContext, nodes: list[ET.Element]
) -> None:
    """Emit one molecule (a run of element nodes), deciding casing for it:
    all-single-letter → one leading chemical-formula indicator ⠸ + bare
    letters; any multi-letter element → per-element capital sign ⠠, no ⠸.

    A run holding a charge (an ion) is always written per-element: a single
    ion like F⁻ / O²⁻ takes the capital sign ⠠ (⠠F / ⠠O), not the
    chemical-formula indicator ⠸ a neutral single-letter formula would carry —
    the ⠸ marks a molecule, not a charged species."""
    from brailix.backend.math.dispatch import _emit_element

    mctx.chem_per_element = _any_multi_letter(nodes) or any(
        _is_charge_node(node) for node in nodes
    )
    if not mctx.chem_per_element:
        for dots in mctx.profile.math_structure("chem.indicator"):
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_chem_indicator",
                    source_span=mctx.span,
                )
            )
    for node in nodes:
        _emit_element(cells, mctx, node)


def _any_multi_letter(nodes: list[ET.Element]) -> bool:
    """True when any element symbol across ``nodes`` spans ≥2 letters."""
    for node in nodes:
        for mi in node.iter("mi"):
            if len((mi.text or "").strip()) >= 2:
                return True
    return False


def emit_element(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Emit one element symbol's letters.

    In per-element mode the first letter gets the capital sign ⠠; in
    all-single-letter mode there is no per-letter prefix (the leading ⠸
    already stood in for it). Every letter is the bare letter cell from
    the profile's ``latin_letters`` table.
    """
    text = (elem.text or "").strip()
    if not text:
        return
    profile = mctx.profile
    for idx, ch in enumerate(text):
        if mctx.chem_per_element and idx == 0:
            for dots in profile.math_structure("letter_prefix.latin_upper"):
                cells.append(
                    BrailleCell(
                        dots=dots,
                        role="math_chem_capital",
                        source_span=mctx.span,
                        source_text=ch,
                    )
                )
        bare = profile.bare_letter(ch)
        if bare is None:
            mctx.backend.warnings.warn(
                code="MATH_UNKNOWN_IDENTIFIER",
                message=f"no braille mapping for chem element letter {ch!r}",
                surface=ch,
                span=mctx.span,
                source="backend.math",
            )
            cells.append(_unknown_cell(ch, mctx.span))
            continue
        cells.append(
            BrailleCell(
                dots=bare,
                role="math_identifier",
                source_span=mctx.span,
                source_text=ch,
            )
        )
    mctx.need_number_sign = False


def emit_operator(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> bool:
    """Gas ↑ / precipitate ↓ arrows only: emit their profile symbol cells
    (``uarr`` = ⠰⠌, ``darr`` = ⠘⠡) attached with **no** leading space —
    chemistry writes H₂↑, not H₂ ↑.

    A structural bond (``data-bk-chem-bond`` from the frontend — ``"double"``
    for a tight ``=``, ``"triple"`` for ``#``) renders the matching
    ``chem.bond_double`` ⠶ / ``chem.bond_triple`` ⠿ cell with **no** surrounding
    space. The triple bond is distinct from the math ≡ / equiv ⠘⠶; the double
    bond reuses the ⠶ glyph but, unlike the spaced reaction connector, sits
    tight against its atoms.

    The reverse reaction arrow ``←`` (mhchem ``<-``) renders ``chem.arrow_reverse``
    ⠠⠶⠂ **spaced** (a leading blank, like the forward connector) — not the math
    left arrow ⠫⠒.

    Returns ``False`` for every other ``<mo>`` (``+`` / spaced ``=`` / ⇌ …) so
    they take the ordinary spaced maths operator path; chemistry reuses maths
    operator rules for those.
    """
    bond = elem.get("data-bk-chem-bond")
    if bond in ("double", "triple"):
        for dots in mctx.profile.math_structure(f"chem.bond_{bond}"):
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_chem_bond",
                    source_span=mctx.span,
                    source_text=(elem.text or "").strip(),
                )
            )
        mctx.need_number_sign = True
        return True
    text = (elem.text or "").strip()
    if text == "←":
        # Reverse reaction arrow (mhchem ``<-``): chem-specific cells
        # (``chem.arrow_reverse`` ⠠⠶⠂), spaced like the forward connector — a
        # leading blank — NOT the math left arrow ⠫⠒ (``larr``).
        if cells and not _last_is_blank(cells):
            cells.append(BLANK_CELL)
        for dots in mctx.profile.math_structure("chem.arrow_reverse"):
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_rel",
                    source_span=mctx.span,
                    source_text="←",
                )
            )
        mctx.need_number_sign = True
        return True
    if text not in _ATTACHED_ARROWS:
        return False
    sym = mctx.profile.math_symbol(text)
    if sym is None:
        return False
    role = mctx.profile.math_symbol_role(text)
    cell_role = _ROLE_TO_CELL_ROLE.get(role or "", "math_op")
    for dots in sym:
        cells.append(
            BrailleCell(
                dots=dots,
                role=cell_role,
                source_span=mctx.span,
                source_text=text,
            )
        )
    mctx.need_number_sign = True
    return True


def emit_connector_with_conditions(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Emit a reaction connector (``=`` / ⇌) carrying over/under conditions
    (``<mover>`` / ``<munder>`` / ``<munderover>`` from ``->[above][below]``).

    The connector itself goes through the ordinary maths ``<mo>`` path
    (spaced). Each condition reuses the maths big-operator over/under
    machinery — 46-prefix ⠨ + superscript sign ⠌ (above) / subscript sign ⠡
    (below) + content — except a heat mark (Δ / △), which is the inline
    ``chem.heat`` symbol ⠘⠸⠲ with no positioning.
    """
    from brailix.backend.math.dispatch import _emit_element
    from brailix.backend.math.utils import _unpack_under_over

    base, under, over = _unpack_under_over(elem)
    if base is not None:
        _emit_element(cells, mctx, base)
    if over is not None:
        _emit_condition_side(cells, mctx, over, "script.sup", "math_superscript")
    if under is not None:
        _emit_condition_side(cells, mctx, under, "script.sub", "math_subscript")
    mctx.need_number_sign = True


def _emit_condition_side(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    content: ET.Element,
    indicator_struct: str,
    indicator_role: str,
) -> None:
    """One condition side. A heat mark renders as the inline ``chem.heat``
    symbol; everything else takes the big-operator over/under form
    (46-prefix + indicator + content) via :func:`handlers._emit_big_op_side`.
    """
    if _is_heat(content):
        for dots in mctx.profile.math_structure("chem.heat"):
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="math_chem_heat",
                    source_span=mctx.span,
                )
            )
        return
    from brailix.backend.math.utils import _emit_structure

    # 46-prefix ⠨ + superscript sign ⠌ / subscript sign ⠡ (the big-operator
    # over/under form).
    _emit_structure(
        cells, mctx, "script.big_op_prefix", role="math_big_op_script_prefix"
    )
    _emit_structure(cells, mctx, indicator_struct, role=indicator_role)
    mctx.need_number_sign = True
    if content.tag == "mtext":
        # A prose condition (Chinese 点燃 / 催化剂 / 高温 …) — translate it
        # through the pipeline.
        _emit_prose_condition(cells, mctx, content)
    else:
        # A formula condition (MnO2 …) — emit it as its own molecule run so it
        # gets an independent casing decision (⠸ vs per-element capital sign)
        # instead of inheriting the preceding species'. ``<mrow>`` unwraps to its
        # children; a lone node (a collapsed singleton) emits on its own.
        nodes = list(content) if content.tag == "mrow" else [content]
        emit_children(cells, mctx, nodes)
    _emit_structure(cells, mctx, "script.close", role="math_script_close")
    mctx.need_number_sign = True


def _emit_prose_condition(
    cells: list[BrailleCell], mctx: MathBrailleContext, content: ET.Element
) -> None:
    """Translate a prose reaction condition (Chinese text) through the
    pipeline-injected ``inline_text_translator`` — the same zh / latin text
    path the music backend uses for ``<words>`` / lyrics, so the chem layout
    just references the result rather than the backend re-running the
    frontend. Falls back to the literal per-char ``<mtext>`` path (a warning
    per unknown char) when no translator was injected (e.g. a bare
    backend-only run with no pipeline)."""
    from brailix.backend._inline import rebase_translated_cells
    from brailix.backend.math.dispatch import _emit_element

    text = (content.text or "").strip()
    translator = mctx.backend.inline_text_translator()
    if not text or translator is None:
        _emit_element(cells, mctx, content)
        return
    # The translator's cells carry throwaway-document spans — rebase
    # onto the formula's own span so proofread jumps land here.
    cells.extend(rebase_translated_cells(translator(text), mctx.span))


def _is_heat(node: ET.Element | None) -> bool:
    """True when a condition node is the heat mark Δ / △ — the
    reaction-condition heat symbol, set by the adapter as ``<mi>Δ</mi>``."""
    if node is None:
        return False
    text = (node.text or "").strip() or "".join(node.itertext()).strip()
    return text in ("Δ", "△")


def emit_subscript(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    base: ET.Element | None,
    sub: ET.Element | None,
) -> None:
    """Emit a chemical subscript: the element base, then the subscript
    digit(s) in lowered Antoine form — **no** ``script.sub`` marker, **no**
    number sign, **no** closing marker. Multi-digit subscripts lower each
    digit in turn (C₁₂ → element + ⠂⠆-style run)."""
    from brailix.backend.math.dispatch import _emit_element

    if base is not None:
        _emit_element(cells, mctx, base)
    _emit_lowered_digits(cells, mctx, sub)
    mctx.need_number_sign = False


def emit_state(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Emit a physical-state label's letters ((s)/(l)/(g)/(aq)).

    The state is an English abbreviation, so it is written as the bare Latin
    letters behind **one** Latin-lowercase prefix ⠰ (``letter_prefix.latin_lower``)
    — ``(aq)`` is ⠣ ⠰ a q ⠜, ``(l)`` is ⠣ ⠰ l ⠜. The surrounding parens ⠣ ⠜
    come from the ``<mo>`` siblings (the ordinary delim path); this renders
    only the inside, with no chemical-formula / capital-sign element casing.
    """
    text = (elem.text or "").strip()
    if not text:
        return
    profile = mctx.profile
    for dots in profile.math_structure("letter_prefix.latin_lower"):
        cells.append(
            BrailleCell(
                dots=dots,
                role="math_chem_state_prefix",
                source_span=mctx.span,
            )
        )
    for ch in text:
        bare = profile.bare_letter(ch)
        if bare is None:
            mctx.backend.warnings.warn(
                code="MATH_UNKNOWN_IDENTIFIER",
                message=f"no braille mapping for state letter {ch!r}",
                surface=ch,
                span=mctx.span,
                source="backend.math",
            )
            cells.append(_unknown_cell(ch, mctx.span))
            continue
        cells.append(
            BrailleCell(
                dots=bare,
                role="math_chem_state",
                source_span=mctx.span,
                source_text=ch,
            )
        )
    mctx.need_number_sign = True


def _emit_lowered_digits(
    cells: list[BrailleCell], mctx: MathBrailleContext, sub: ET.Element | None
) -> None:
    """Emit subscript digit(s) in lowered Antoine form — no ``script.sub``
    marker, no number sign, no close. Shared by an element's own subscript
    (H₂O) and the subscript of a subscripted ion (Hg₂²⁺)."""
    text = (sub.text or "").strip() if sub is not None else ""
    profile = mctx.profile
    for digit in text:
        lower = profile.math_digits_lower.get(digit)
        if not lower:
            mctx.backend.warnings.warn(
                code="MATH_UNKNOWN_DIGIT",
                message=f"no lowered digit for chem subscript {digit!r}",
                surface=digit,
                span=mctx.span,
                source="backend.math",
            )
            cells.append(_unknown_cell(digit, mctx.span))
            continue
        cells.append(
            BrailleCell(
                dots=lower,
                role="math_digit_lower",
                source_span=mctx.span,
                source_text=digit,
            )
        )


def emit_charge(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    base: ET.Element | None,
    sub: ET.Element | None,
    sup: ET.Element | None,
) -> None:
    """Emit an ion's charge: ``element [lowered subscript] charge sign ⠨
    [number sign + digit] plus/minus sign``.

    Per the project's braille domain expert, an ionic charge is written with
    the 46-prefix charge marker ⠨ (``chem.charge``) — *not* the maths
    exponent indicator ⠌ — and the magnitude, when ≥2, as number sign ⠼ +
    digit::

        Na⁺  = ⠠N a ⠨ ⠖                F⁻  = ⠠F ⠨ ⠤
        Mg²⁺ = ⠠M g ⠨ ⠼⠃ ⠠⠖           O²⁻ = ⠠O ⠨ ⠼⠃ ⠤

    A unit charge carries no number. The ``+`` after a magnitude digit takes
    a ⠠ guard (``chem.charge_plus_guard``) because its cell ⠖ (2-3-5) is also
    the lowered digit 6 — the guard tells them apart, exactly as factorial
    ! = ⠠⠖ is told from the plus sign ⠖. The ``-`` cell ⠤ (3-6) collides with
    no lowered digit, so it never takes the guard.

    Casing depends on whether the ion is one atom or a group. A **monatomic**
    ion (``<mi>`` base — Na⁺ / F⁻ / O²⁻) is forced to the capital sign ⠠: a
    lone atom symbol takes the capital sign, not the chemical-formula
    indicator ⠸ a single-letter *molecule* would carry. A **group** base (an
    ``<mrow>`` — polyatomic ion SO₄²⁻ / OH⁻, or a bracketed complex
    ``[Cu(NH3)4]``) is emitted through :func:`emit_children`, so each run
    inside it (and any delimiters / nested groups) is cased on its own — ⠸ for
    an all-single-letter run, per-element capital sign for a run with a
    multi-letter element (MnO₄⁻, [Cu…]). For a
    single-run group this is exactly the molecule rule; for a multi-run
    bracket it cases each piece independently. The charge tail is identical.
    """
    from brailix.backend.math.dispatch import _emit_element

    magnitude, positive = _parse_charge_sup(sup)
    if base is not None and base.tag == "mrow":
        # Polyatomic ion / bracketed complex: per-run casing.
        emit_children(cells, mctx, list(base))
    else:
        # Monatomic ion: capital sign ⠠, never the chemical-formula indicator ⠸.
        mctx.chem_per_element = True
        if base is not None:
            _emit_element(cells, mctx, base)
    if sub is not None:
        _emit_lowered_digits(cells, mctx, sub)  # Hg₂²⁺, or [..]ₙ^charge
    # charge sign ⠨ (the 46-prefix charge marker).
    from brailix.backend.math.utils import _emit_structure

    _emit_structure(cells, mctx, "chem.charge", role="math_chem_charge")
    had_number = bool(magnitude) and magnitude != "1"
    if had_number:
        assert magnitude is not None  # had_number implies a truthy magnitude
        _emit_charge_magnitude(cells, mctx, magnitude)
    if positive:
        if had_number:
            # ⠠ guard: a bare ⠖ here would read as the lowered digit 6.
            _emit_structure(
                cells, mctx, "chem.charge_plus_guard", role="math_chem_charge_guard"
            )
        _emit_sign_cells(cells, mctx, _CHARGE_PLUS)
    else:
        _emit_sign_cells(cells, mctx, _CHARGE_MINUS)
    mctx.need_number_sign = True


def _parse_charge_sup(sup: ET.Element | None) -> tuple[str | None, bool]:
    """Read a charge superscript → ``(magnitude, is_positive)``.

    The adapter emits a bare ``<mo>±</mo>`` for a unit charge or
    ``<mrow><mn>n</mn><mo>±</mo></mrow>`` for a magnitude-n charge. The
    magnitude is the ``<mn>`` text (``None`` when absent); the sign is the
    ``<mo>`` — ``"+"`` → positive, anything else → negative.
    """
    magnitude: str | None = None
    sign = "+"
    if sup is not None:
        if sup.tag == "mo":
            sign = (sup.text or "").strip() or "+"
        else:  # <mrow> holding <mn> and <mo> (or a lone child after collapse)
            for child in sup:
                if child.tag == "mn" and magnitude is None:
                    magnitude = (child.text or "").strip() or None
                elif child.tag == "mo":
                    sign = (child.text or "").strip() or sign
    return magnitude, sign == "+"


def _emit_charge_magnitude(
    cells: list[BrailleCell], mctx: MathBrailleContext, magnitude: str
) -> None:
    """Emit a charge magnitude as number sign ⠼ + upper digit(s) — O²⁻'s ⠼⠃."""
    profile = mctx.profile
    if profile.feature("math.number_sign", True) and profile.number_sign:
        cells.append(
            BrailleCell(
                dots=profile.number_sign,
                role="number_sign",
                source_span=mctx.span,
            )
        )
    for digit in magnitude:
        upper = profile.digits.get(digit)
        if not upper:
            mctx.backend.warnings.warn(
                code="MATH_UNKNOWN_DIGIT",
                message=f"no braille mapping for charge digit {digit!r}",
                surface=digit,
                span=mctx.span,
                source="backend.math",
            )
            cells.append(_unknown_cell(digit, mctx.span))
            continue
        cells.append(
            BrailleCell(
                dots=upper,
                role="math_digit",
                source_span=mctx.span,
                source_text=digit,
            )
        )


def _emit_sign_cells(
    cells: list[BrailleCell], mctx: MathBrailleContext, sign_char: str
) -> None:
    """Emit a charge sign's cells from the symbol table (plus sign ⠖ / minus
    sign ⠤), attached with **no** operator spacing — a charge sign hugs its
    magnitude, unlike the spaced binary ``+`` / ``-`` of maths.

    The cells carry a dedicated ``math_chem_charge_sign`` role, **not**
    ``math_op``: a charge sign closes the ion (it is a postfix mark, not a
    binary operator), so a following reaction ``+`` / ``=`` must keep its
    ordinary leading space — ``Na+ + Cl-`` is ⠠N a ⠨⠖ ␣ ⠖ ⠠C l ⠨⠤. Were the
    sign an ``op``, the next operator would read the ion as its left operand's
    unary context and drop that space."""
    sym = mctx.profile.math_symbol(sign_char)
    if sym is None:
        mctx.backend.warnings.warn(
            code="MATH_UNKNOWN_SYMBOL",
            message=f"no braille mapping for charge sign {sign_char!r}",
            surface=sign_char,
            span=mctx.span,
            source="backend.math",
        )
        cells.append(_unknown_cell(sign_char, mctx.span))
        return
    for dots in sym:
        cells.append(
            BrailleCell(
                dots=dots,
                role="math_chem_charge_sign",
                source_span=mctx.span,
                source_text=sign_char,
            )
        )
