r"""mhchem ``\ce{...}`` → MathML adapter (chemistry).

``latex2mathml`` has no mhchem support — it turns ``\ce{H2O}`` into a
stray ``<mi>\ce</mi>`` plus ordinary maths, losing every chemical
meaning. Chemical formulae therefore get their own small, bounded parser
here. mhchem is a LaTeX package, so the ``latex`` adapter delegates to
:func:`convert_ce` when it spots a top-level ``\ce{...}``; this module is
also registered as a standalone ``chem`` source format (pure-stdlib, no
optional dependency).

What the adapter outputs is a MathML string whose ``<math>`` root carries
``data-bk-chem="1"``. That attribute is an explicit backend ABI (the same
mechanism as ``data-bk-span``): it states the *semantic* fact "this is a
chemical formula" and nothing about braille. Every braille *decision* —
no subscript indicator, the leading chemical-formula indicator ⠸, the
per-element capital sign — lives in :mod:`brailix.backend.math.chem`, the
layer that owns output rules. See ``ARCHITECTURE.md``

Supported subset (school chemistry, grown incrementally): element symbols
``[A-Z][a-z]*`` with numeric subscripts (``H2O``, ``H2SiO3``, ``NaCl``),
gas / precipitate arrows (mhchem ``^`` / ``v`` or literal ↑ / ↓), leading
coefficients (``2H2O``), the ``+`` operator, the reaction connectors ``->``
/ ``=`` (yields, rendered ``=``) and ``<=>`` (reversible ⇌), and over/under
reaction conditions (``->[above][below]`` — formula conditions like ``MnO2``
and the heat mark ``\Delta``; a Chinese condition like 点燃 is carried as
``<mtext>`` for now, pending the zh-backed condition path), ionic charges —
monatomic (``Na+``, ``Mg^2+``, ``O^2-``) and polyatomic (``SO4^2-`` — an
``<msup>`` the backend renders with the charge sign ⠨), and parenthesised groups
with a multiplier (``Ca(OH)2``, ``(NH4)2SO4`` — a ``<mrow>`` group whose
content is parsed and cased on its own). States (``(s)`` / ``(aq)`` …),
chemical bonds (``-``/``=``/``#``) and square-bracket complexes
(``[Cu(NH3)4]^2+``) still fall back to a soft ``<merror>`` until their braille
rules are specified.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

from brailix.core.context import MathContext
from brailix.frontend.math.utils import (
    _MATHML_NS,
    _strip_math_delimiters,
    merror_wrap,
)

# An element symbol: a capital letter optionally followed by lowercase
# letters (H, He, Na, Si). This same rule tells the backend whether the
# whole formula is "all single-letter" (one <mi> per element).
_ELEMENT_RE = re.compile(r"[A-Z][a-z]*")

# Physical-state labels written in parentheses after a species: solid /
# liquid / gas / aqueous. They are English abbreviations, not chemical
# elements — the backend renders them as the bare letters behind one
# Latin-lowercase prefix ⠰. Lowercase, so never confused with an element
# symbol ((s) the state vs ``S`` sulfur).
_STATE_TOKENS = frozenset({"s", "l", "g", "aq"})

# Gas (↑) / precipitate (↓) arrows. mhchem writes a standalone ``^`` / ``v``
# token; we also accept the literal Unicode arrows. Emitted as ``<mo>`` so
# the backend renders them through the existing ``uarr`` / ``darr`` symbol
# cells (《盲文常用数学符号》, arrows section: ↑ = ⠰⠌, ↓ = ⠘⠡).
_GAS = "↑"   # ↑
_PRECIPITATE = "↓"  # ↓


class _ChemParseError(ValueError):
    """Raised for ``\\ce`` content outside the supported subset. The
    adapter converts it into a soft ``<merror>`` so one unparseable
    formula never breaks the surrounding document."""


@dataclass(slots=True)
class ChemMathSourceAdapter:
    """``\\ce{...}`` (or bare formula text) → chemistry MathML string."""

    source: str = "chem"

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        if isinstance(formula, bytes):
            try:
                formula = formula.decode("utf-8")
            except UnicodeDecodeError:
                return merror_wrap(repr(formula), reason="non-utf8 bytes")
        text = _strip_math_delimiters(formula.strip())
        if not text:
            return merror_wrap("", reason="empty input")
        # Accept both the wrapped ``\ce{...}`` form and bare formula text
        # (when something routed ``source="chem"`` directly).
        inner = extract_ce_inner(text)
        if inner is None:
            inner = text
        return convert_ce(inner)


def extract_ce_inner(text: str) -> str | None:
    r"""Return the inner content of a top-level ``\ce{...}``, or ``None``.

    Brace-matched so nested groups (``\ce{... ->[{cat}] ...}``) extract
    whole. Returns ``None`` when the text isn't exactly one ``\ce{...}``
    (e.g. trailing content, unbalanced braces) — the LaTeX adapter then
    leaves it to ``latex2mathml``.
    """
    s = text.strip()
    if not s.startswith("\\ce"):
        return None
    i = len("\\ce")
    while i < len(s) and s[i].isspace():
        i += 1
    if i >= len(s) or s[i] != "{":
        return None
    depth = 0
    j = i
    while j < len(s):
        if s[j] == "{":
            depth += 1
        elif s[j] == "}":
            depth -= 1
            if depth == 0:
                break
        j += 1
    if depth != 0:
        return None
    if s[j + 1 :].strip():
        return None
    return s[i + 1 : j]


def convert_ce(inner: str) -> str:
    r"""Convert the content of a ``\ce{...}`` into a MathML string tagged
    ``data-bk-chem="1"``. Unsupported content yields a soft ``<merror>``."""
    inner = inner.strip()
    if not inner:
        return merror_wrap("", reason="empty \\ce")
    try:
        body = _emit_formula(inner)
    except _ChemParseError as e:
        return merror_wrap(inner, reason=f"unsupported \\ce content: {e}")
    return f'<math xmlns="{_MATHML_NS}" data-bk-chem="1">{body}</math>'


def _match_arrow(inner: str, i: int) -> tuple[str, int] | None:
    """Recognise a gas / precipitate arrow at position ``i``.

    Returns ``(symbol, next_index)`` for ↑ / ↓ / standalone ``v`` /
    standalone ``^``, else ``None``. ``^`` is only the gas arrow when it
    stands alone (next char is whitespace or end) so a charge like
    ``^2-`` isn't mistaken for one; ``v`` is unambiguous because an element
    symbol always starts uppercase, so a lone lowercase ``v`` can't be one.
    """
    ch = inner[i]
    if ch == "↑":
        return _GAS, i + 1
    if ch == "↓":
        return _PRECIPITATE, i + 1
    if ch == "v":
        return _PRECIPITATE, i + 1
    if ch == "^" and (i + 1 >= len(inner) or inner[i + 1].isspace()):
        return _GAS, i + 1
    return None


def _species_follows(inner: str, i: int) -> bool:
    """True when a new species begins at/after ``i`` (skipping spaces): a
    coefficient digit or an uppercase element letter.

    This is what separates an addition ``+`` from a trailing charge ``+``.
    In ``2H2+O2`` / ``H2 + O2`` the ``+`` is followed by the next reactant,
    so it joins two species (addition). In ``Na+`` / ``H+`` the ``+`` ends a
    species — nothing, or a space then a connector, follows — so it's a
    charge (handled in the element branch as an ``<msup>``)."""
    n = len(inner)
    j = i
    while j < n and inner[j].isspace():
        j += 1
    if j >= n:
        return False
    ch = inner[j]
    return ch.isdigit() or "A" <= ch <= "Z"


# Condition strings that mean "heat" — rendered as the inline heat symbol
# ⠘⠸⠲ (``chem.heat``) by the backend, not as an over/under script. The
# triangle/Delta here is the *reaction-condition* heat mark, distinct from
# the geometry triangle △ (which keeps its own symbol elsewhere).
_HEAT_CONDITIONS = frozenset({"\\Delta", "Δ", "△", "\\triangle", "\\vartriangle"})


def _match_connector(inner: str, i: int) -> tuple[str, int] | None:
    """Reaction connector at ``i``: ``<=>`` → reversible ⇌, ``->`` / ``=`` →
    yields (rendered ``=``). Returns ``(char, next_index)`` or ``None``."""
    if inner.startswith("<=>", i):
        return "⇌", i + 3
    if inner.startswith("->", i):
        return "=", i + 2
    if inner[i] == "=":
        return "=", i + 1
    return None


def _parse_conditions(inner: str, i: int) -> tuple[str | None, str | None, int]:
    """Parse up to two ``[...]`` condition groups after a connector (mhchem
    ``->[above][below]``). Returns ``(above, below, next_index)``; either may
    be ``None``. Bracket-matched so ``[{...}]`` extracts whole."""
    conds: list[str] = []
    n = len(inner)
    while len(conds) < 2:
        j = i
        while j < n and inner[j].isspace():
            j += 1
        if j >= n or inner[j] != "[":
            break
        depth = 0
        k = j
        while k < n:
            if inner[k] == "[":
                depth += 1
            elif inner[k] == "]":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        if depth != 0:
            break  # unbalanced — leave the rest for the element parser
        conds.append(inner[j + 1 : k])
        i = k + 1
    above = conds[0] if len(conds) >= 1 else None
    below = conds[1] if len(conds) >= 2 else None
    return above, below, i


def _connector_mathml(conn: str, above: str | None, below: str | None) -> str:
    """Build the connector MathML, wrapping it in ``<mover>`` / ``<munder>``
    / ``<munderover>`` when conditions are present (base, under, over)."""
    base = f"<mo>{conn}</mo>"
    if above is None and below is None:
        return base
    over = _condition_mathml(above) if above is not None else None
    under = _condition_mathml(below) if below is not None else None
    if over is not None and under is not None:
        return f"<munderover>{base}{under}{over}</munderover>"
    if over is not None:
        return f"<mover>{base}{over}</mover>"
    return f"<munder>{base}{under}</munder>"


def _condition_mathml(text: str) -> str:
    """Render one reaction condition to MathML. ``\\Delta`` / Δ / △ become the
    heat marker (``<mi>Δ</mi>``, rendered inline by the backend). Otherwise
    the condition is parsed as a chemical formula (``MnO2`` …); content that
    isn't a formula (e.g. Chinese 点燃) falls back to ``<mtext>`` — a
    placeholder until the zh-backed condition path (increment B) lands."""
    text = text.strip()
    if text.startswith("{") and text.endswith("}"):
        text = text[1:-1].strip()
    if text in _HEAT_CONDITIONS:
        return "<mi>Δ</mi>"
    try:
        return f"<mrow>{_emit_formula(text)}</mrow>"
    except _ChemParseError:
        return f"<mtext>{escape(text)}</mtext>"


def _emit_formula(inner: str) -> str:
    """Parse the content of a ``\\ce{...}`` into MathML children.

    Handles element symbols with numeric subscripts (one ``<mi>`` /
    ``<msub>`` each), leading coefficients (``<mn>``), the ``+`` operator,
    the reaction connectors ``->`` / ``=`` (rendered ``=``) and ``<=>``
    (reversible ⇌), gas / precipitate arrows (``<mo>``), ionic charges
    (``Na+`` / ``SO4^2-`` → ``<msup>``; see :func:`_match_charge`), and
    parenthesised groups with a multiplier (``Ca(OH)2``; see
    :func:`_parenthesised_group`). Element casing and the chemical-formula
    indicator are decided downstream by the backend.

    ``+`` is the addition operator when it separates two species — either
    preceded by a token boundary (``H2 + O2``) or immediately followed by a
    new species, i.e. a coefficient digit or an uppercase element letter
    (``2H2+O2``, the spaceless form school equations are written in). A ``+``
    that ends a species with nothing after it (``Na+`` / ``H+``) is a charge,
    handled in the element branch below. The connectors ``->`` / ``<=>`` /
    ``=`` are unambiguous and need no such guard.

    ``species_atoms`` counts element symbols since the last species boundary
    (start / space / ``+`` / connector). A charge is only emitted on a
    single-atom species (``species_atoms == 1``); a charge on a polyatomic
    species (``SO4^2-``) raises so the whole formula degrades to ``<merror>``
    rather than charging only its last atom — those casing rules aren't
    specified yet.
    """
    parts: list[str] = []
    i = 0
    n = len(inner)
    prev_boundary = True  # start of string acts like a token boundary
    species_atoms = 0  # element symbols since the last species boundary
    while i < n:
        ch = inner[i]
        if ch.isspace():
            prev_boundary = True
            species_atoms = 0
            i += 1
            continue
        connector = _match_connector(inner, i)
        if connector is not None:
            conn_char, i = connector
            above, below, i = _parse_conditions(inner, i)
            parts.append(_connector_mathml(conn_char, above, below))
            prev_boundary = False
            species_atoms = 0
            continue
        if ch == "+" and (prev_boundary or _species_follows(inner, i + 1)):
            parts.append("<mo>+</mo>")
            i += 1
            prev_boundary = False
            species_atoms = 0
            continue
        arrow = _match_arrow(inner, i)
        if arrow is not None:
            symbol, i = arrow
            parts.append(f"<mo>{symbol}</mo>")
            prev_boundary = False
            continue
        if ch.isdigit():
            start = i
            while i < n and inner[i].isdigit():
                i += 1
            parts.append(f"<mn>{inner[start:i]}</mn>")
            prev_boundary = False
            continue
        if ch == "(" or ch == "[":
            group, i = _bracketed_group(inner, i)
            parts.append(group)
            species_atoms += 1
            prev_boundary = False
            continue
        m = _ELEMENT_RE.match(inner, i)
        if not m:
            raise _ChemParseError(f"unexpected {ch!r} at index {i}")
        element = m.group(0)
        i = m.end()
        start = i
        while i < n and inner[i].isdigit():
            i += 1
        sub = inner[start:i] if i > start else None
        species_atoms += 1
        atom = (
            f"<msub><mi>{element}</mi><mn>{sub}</mn></msub>"
            if sub is not None
            else f"<mi>{element}</mi>"
        )
        charge = _match_charge(inner, i)
        if charge is not None:
            sign, magnitude, i = charge
            if species_atoms == 1:
                parts.append(_charge_node(element, sub, magnitude, sign))
            else:
                # Polyatomic ion (SO4^2-, OH-): the charge belongs to the
                # whole species. Pull this species' earlier atoms back out of
                # ``parts`` and wrap the group in one ``<msup>``.
                prior = parts[-(species_atoms - 1):]
                del parts[-(species_atoms - 1):]
                group = "".join(prior) + atom
                parts.append(
                    f"<msup><mrow>{group}</mrow>{_charge_sup(magnitude, sign)}</msup>"
                )
        else:
            parts.append(atom)
        prev_boundary = False
    if not parts:
        raise _ChemParseError("no chemical content")
    return "".join(parts)


# Round / square group delimiters, keyed by the open char. Both render
# through the math symbol table — ⠣⠜ for ``()`` (lpar/rpar), ⠷⠾ for ``[]``
# (lbrack/rbrack) — so the backend needs no chemistry-specific delimiter rule.
_GROUP_CLOSE = {"(": ")", "[": "]"}


def _bracketed_group(inner: str, i: int) -> tuple[str, int]:
    """Parse a ``(...)`` or ``[...]`` group at ``i`` plus an optional trailing
    whole-group multiplier (subscript) and/or charge. Returns
    ``(mathml, next_index)``.

    The group content is parsed **recursively** as its own formula, so it is
    cased independently of the atoms around it — ``Ca(OH)2`` is ⠠Ca then the
    group ⠣ ⠸OH ⠜ ⠆, with the (OH) run getting its own chemical-formula
    indicator, and ``(NH4)2SO4`` carries one ⠸ for NH₄ inside the parens and
    another for the trailing SO₄. Square brackets behave identically with the
    bracket cells ⠷⠾ and admit a trailing charge for complex ions
    (``[Cu(NH3)4]^2+``).

    A trailing digit run is the whole-group multiplier (the 2 in ``(OH)2``) and
    wraps the group in an ``<msub>``; a trailing charge wraps it in an
    ``<msup>`` (or ``<msubsup>`` when both are present) — the backend lowers a
    multiplier like an atom subscript and renders a charge with the charge
    sign ⠨. Raises :class:`_ChemParseError` on an unbalanced or empty group.

    A physical-state label (``(s)`` / ``(l)`` / ``(g)`` / ``(aq)``) is special-
    cased here: its lowercase content is an English abbreviation, not chemical
    elements, so it becomes an ``<mtext data-bk-chem-state>`` the backend
    renders as the bare letters behind one Latin-lowercase prefix ⠰ — never the
    chemical-formula / capital-sign element casing.
    """
    n = len(inner)
    open_char = inner[i]
    close_char = _GROUP_CLOSE[open_char]
    depth = 0
    j = i
    while j < n:
        if inner[j] == open_char:
            depth += 1
        elif inner[j] == close_char:
            depth -= 1
            if depth == 0:
                break
        j += 1
    if depth != 0:
        raise _ChemParseError("unbalanced bracket")
    content = inner[i + 1 : j]
    if open_char == "(" and content.strip() in _STATE_TOKENS:
        return (
            f'<mrow><mo>(</mo><mtext data-bk-chem-state="1">'
            f"{content.strip()}</mtext><mo>)</mo></mrow>",
            j + 1,
        )
    body = _emit_formula(content)  # recurse on the group content
    base = f"<mrow><mo>{open_char}</mo>{body}<mo>{close_char}</mo></mrow>"
    k = j + 1
    start = k
    while k < n and inner[k].isdigit():
        k += 1
    sub = inner[start:k] if k > start else None
    charge = _match_charge(inner, k)
    if charge is not None:
        sign, magnitude, k = charge
        csup = _charge_sup(magnitude, sign)
        if sub is not None:
            group = f"<msubsup>{base}<mn>{sub}</mn>{csup}</msubsup>"
        else:
            group = f"<msup>{base}{csup}</msup>"
    elif sub is not None:
        group = f"<msub>{base}<mn>{sub}</mn></msub>"
    else:
        group = base
    return group, k


def _match_charge(inner: str, i: int) -> tuple[str, str | None, int] | None:
    r"""Recognise an ionic charge at ``i`` (immediately after a species).

    Three mhchem spellings, all attached (no separating space) to the
    species they charge:

    * caret form ``^n+`` / ``^n-`` (optionally braced ``^{n+}``) — the
      magnitude ``n`` is the run of digits, the sign the trailing ``+`` /
      ``-`` (``Mg^2+`` → 2+, ``O^2-`` → 2−);
    * a bare trailing ``+`` — a unit positive charge, but only when no new
      species follows (``Na+`` / ``H+``); a ``+`` with a species after it is
      the addition operator and is left for the caller (``2H2+O2``);
    * a bare trailing ``-`` — a unit negative charge (``Cl-``), unless it
      opens the ``->`` connector.

    Returns ``(sign, magnitude_or_None, next_index)`` with ``sign`` one of
    ``"+"`` / ``"-"`` and ``magnitude`` the digit string (``None`` for a unit
    charge), or ``None`` when no charge is present. A lone ``^`` (no sign
    after it) returns ``None`` so :func:`_match_arrow` can still read it as a
    gas arrow.
    """
    n = len(inner)
    if i >= n:
        return None
    ch = inner[i]
    if ch == "^":
        j = i + 1
        braced = j < n and inner[j] == "{"
        if braced:
            j += 1
        d0 = j
        while j < n and inner[j].isdigit():
            j += 1
        magnitude = inner[d0:j] or None
        if j >= n or inner[j] not in "+-":
            return None  # e.g. a standalone gas-arrow ^
        sign = inner[j]
        j += 1
        if braced:
            if j < n and inner[j] == "}":
                j += 1
            else:
                return None
        return sign, magnitude, j
    if ch == "+":
        if not _species_follows(inner, i + 1):
            return "+", None, i + 1
        return None
    if ch == "-" and not inner.startswith("->", i):
        return "-", None, i + 1
    return None


def _charge_sup(magnitude: str | None, sign: str) -> str:
    """The charge superscript: a bare sign ``<mo>`` for a unit charge, or
    ``<mn>n</mn>`` + ``<mo>±</mo>`` in an ``<mrow>`` for a multi-unit charge.
    Shared by the monatomic (:func:`_charge_node`) and polyatomic builders."""
    if magnitude is None or magnitude == "1":
        return f"<mo>{sign}</mo>"
    return f"<mrow><mn>{magnitude}</mn><mo>{sign}</mo></mrow>"


def _charge_node(
    element: str, sub: str | None, magnitude: str | None, sign: str
) -> str:
    """Build the MathML for a charged **single-atom** species.

    A plain charged atom is an ``<msup>``; an atom with both a subscript and a
    charge is a flat ``<msubsup>`` (base, sub, charge) so the backend unpacks
    ``(base, sub, sup)`` directly::

        Na+    → <msup><mi>Na</mi><mo>+</mo></msup>
        Mg^2+  → <msup><mi>Mg</mi><mrow><mn>2</mn><mo>+</mo></mrow></msup>
        Hg2^2+ → <msubsup><mi>Hg</mi><mn>2</mn><mrow><mn>2</mn><mo>+</mo></mrow></msubsup>

    A polyatomic ion instead wraps its whole atom group in ``<msup><mrow>…``
    (built inline in :func:`_emit_formula`). The backend owns every braille
    decision; this only states the structure.
    """
    sup = _charge_sup(magnitude, sign)
    if sub is not None:
        return f"<msubsup><mi>{element}</mi><mn>{sub}</mn>{sup}</msubsup>"
    return f"<msup><mi>{element}</mi>{sup}</msup>"


def _load() -> ChemMathSourceAdapter:
    """Factory — pure-stdlib, so no ``extra`` is needed when registering."""
    return ChemMathSourceAdapter()
