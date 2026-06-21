"""OMML (Office Math Markup Language) adapter.

Converts the math markup that Microsoft Word stores inside ``.docx``
files (a.k.a. OMML, namespace
``http://schemas.openxmlformats.org/officeDocument/2006/math``) into
the project's normalisation mediator: MathML.

The converter is intentionally pure-stdlib — it uses only
:mod:`xml.etree.ElementTree`. ``python-docx`` and ``lxml`` are not
imported here; the input/docx adapter already pulled the OMML xml
string out for us, this adapter only does the dialect translation.

Coverage is the **common Word equation editor subset**: text runs,
fractions, sub/sup/subsup, radicals, n-ary (sum / product / integral /
union / ...), function applications, delimiters, matrices, equation
arrays, limit (under/over), bar, accent, group character, box,
border-box, and phantom. Constructs outside this subset are emitted
as ``<mtext>`` carrying the source text so we never crash; an
``MATH_ERROR`` warning is raised at the backend layer when the
downstream normalizer sees the ``<merror>`` wrapper.
"""

from __future__ import annotations

import contextvars
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from brailix.core._xml import local_name
from brailix.core.context import MathContext
from brailix.frontend.math.adapters._atoms import tokenize_math_text
from brailix.frontend.math.utils import (
    _MATHML_NS,
    merror_wrap,
    mrow_wrap,
    mtext,
)

# OMML elements live under this namespace. We accept both the Clark-
# notation form (``{ns}tag``) that ElementTree emits and the bare local
# name so callers can hand us either.
_OMML_NS: str = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_OMML_PREFIX: str = "{" + _OMML_NS + "}"


@dataclass(slots=True)
class OmmlMathSourceAdapter:
    """Convert an OMML XML fragment into MathML.

    The ``formula`` argument is the OMML XML serialised as text. It may
    be wrapped in ``<m:oMath>`` or ``<m:oMathPara>``; both are accepted.
    Whitespace and the OMML namespace declaration are normalised away.
    """

    source: str = "omml"

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        if isinstance(formula, bytes):
            try:
                formula = formula.decode("utf-8")
            except UnicodeDecodeError:
                return merror_wrap(repr(formula), reason="non-utf8 bytes")
        text = formula.strip()
        if not text:
            return merror_wrap("", reason="empty input")
        try:
            root = ET.fromstring(text)
        except ET.ParseError as e:
            return merror_wrap(text, reason=f"omml parse error: {e}")
        try:
            mathml_root = _convert_root(root)
        except Exception as e:  # noqa: BLE001 — keep adapter soft-failing
            return merror_wrap(text, reason=f"omml convert error: {e}")
        # Serialise without the default namespace dance — ET emits
        # ``ns0:`` prefixes if we hand it our own namespace; instead we
        # write ``xmlns`` manually on the root and keep children prefix-
        # free so the downstream normaliser sees clean local tags.
        return ET.tostring(mathml_root, encoding="unicode")


def _load() -> OmmlMathSourceAdapter:
    return OmmlMathSourceAdapter()


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Bare local name of an ElementTree tag — delegates to the shared
    :func:`brailix.core._xml.local_name` (the generic ``{...}`` strip
    handles the OMML namespace too)."""
    return local_name(tag)


def _children_with(elem: ET.Element, name: str) -> list[ET.Element]:
    return [c for c in elem if _local(c.tag) == name]


def _first_child(elem: ET.Element, name: str) -> ET.Element | None:
    for c in elem:
        if _local(c.tag) == name:
            return c
    return None


def _convert_root(root: ET.Element) -> ET.Element:
    """Wrap converted children in a ``<math>`` element."""
    math = ET.Element("math", {"xmlns": _MATHML_NS})
    # Common Word patterns: top-level can be ``<m:oMath>`` or
    # ``<m:oMathPara>``. ``oMathPara`` holds one or more ``oMath``
    # entries with paragraph-level formatting; we flatten that here.
    tag = _local(root.tag)
    if tag == "oMathPara":
        for omath in _children_with(root, "oMath"):
            for child in omath:
                _append_converted(math, child)
    elif tag == "oMath":
        for child in root:
            _append_converted(math, child)
    else:
        # Caller handed us a bare construct (m:f, m:r, ...); wrap.
        _append_converted(math, root)
    return math


def _append_converted(parent: ET.Element, node: ET.Element) -> None:
    """Convert ``node`` and append result(s) to ``parent``.

    Some OMML constructs (``m:r`` runs holding multiple tokens) expand
    into more than one MathML atom, which is why this helper appends
    rather than returning.
    """
    for converted in _convert(node):
        parent.append(converted)


# Cap conversion recursion so a pathologically deep formula degrades its
# *deepest* subtree to flat text instead of overflowing Python's stack — the
# broad except in `to_mathml` would otherwise catch the RecursionError and
# collapse the WHOLE formula to a single <merror>. Sibling math adapters
# (MTEF _v3/_v5, chem) carry the same kind of depth backstop. 64 matches the
# MTEF cap and is far beyond any real Word equation while leaving ample
# headroom under CPython's ~1000-frame limit (each level costs a few frames).
_MAX_CONVERT_DEPTH = 64

# Per-context (hence per-thread-safe) nesting counter, set/reset around every
# dispatch so it never leaks between formulas or across concurrent conversions.
_convert_depth: contextvars.ContextVar[int] = contextvars.ContextVar(
    "_omml_convert_depth", default=0
)


def _convert(node: ET.Element) -> list[ET.Element]:
    """Translate a single OMML node into MathML elements."""
    depth = _convert_depth.get()
    if depth > _MAX_CONVERT_DEPTH:
        # Too deep: localize the loss to this subtree (keep any leaf text so
        # the reader still sees *something*) instead of failing the formula.
        text = "".join(node.itertext()).strip()
        return [mtext(text)] if text else []
    tag = _local(node.tag)
    handler = _HANDLERS.get(tag)
    if handler is None:
        # Unknown — emit text fallback so layout stays sensible.
        text = "".join(node.itertext()).strip()
        if not text:
            return []
        return [mtext(text)]
    token = _convert_depth.set(depth + 1)
    try:
        return handler(node)
    finally:
        _convert_depth.reset(token)


# ---------------------------------------------------------------------------
# Per-tag handlers
# ---------------------------------------------------------------------------


def _wrap_children(node: ET.Element) -> list[ET.Element]:
    """Convert all children sequentially, no wrapper added."""
    out: list[ET.Element] = []
    for child in node:
        out.extend(_convert(child))
    return out


def _mrow_of(node: ET.Element) -> ET.Element:
    """Convert ``node``'s children and return them inside an ``<mrow>``.

    Used for sub-expressions like fraction numerators where MathML
    requires a single child element.
    """
    return mrow_wrap(_wrap_children(node))


def _convert_run(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:r>`` — a text run. Contains ``<m:t>`` plus formatting
    properties we ignore here (the braille backend is style-agnostic).
    """
    out: list[ET.Element] = []
    for t in _children_with(node, "t"):
        text = (t.text or "").strip()
        if not text:
            continue
        out.extend(tokenize_math_text(text))
    return out


def _convert_fraction(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:f>`` → MathML ``<mfrac>``.

    The OMML ``<m:fPr>/<m:type>`` attribute distinguishes ``bar`` /
    ``noBar`` / ``skw`` / ``lin``; we honour only the no-bar case
    (rendered as ``<mfrac linethickness="0">`` so the backend can elide
    the fraction line). Everything else uses the default ``<mfrac>``.
    """
    num = _first_child(node, "num")
    den = _first_child(node, "den")
    if num is None or den is None:
        return [mtext("(invalid fraction)")]
    mfrac = ET.Element("mfrac")
    mfrac.append(_mrow_of(num))
    mfrac.append(_mrow_of(den))
    f_type = _math_property(node, "fPr", "type")
    if f_type == "noBar":
        mfrac.set("linethickness", "0")
    return [mfrac]


def _convert_ssub(node: ET.Element) -> list[ET.Element]:
    base = _first_child(node, "e")
    sub = _first_child(node, "sub")
    if base is None or sub is None:
        return [mtext("(invalid sub)")]
    elem = ET.Element("msub")
    elem.append(_mrow_of(base))
    elem.append(_mrow_of(sub))
    return [elem]


def _convert_ssup(node: ET.Element) -> list[ET.Element]:
    base = _first_child(node, "e")
    sup = _first_child(node, "sup")
    if base is None or sup is None:
        return [mtext("(invalid sup)")]
    elem = ET.Element("msup")
    elem.append(_mrow_of(base))
    elem.append(_mrow_of(sup))
    return [elem]


def _convert_ssubsup(node: ET.Element) -> list[ET.Element]:
    base = _first_child(node, "e")
    sub = _first_child(node, "sub")
    sup = _first_child(node, "sup")
    if base is None or sub is None or sup is None:
        return [mtext("(invalid subsup)")]
    elem = ET.Element("msubsup")
    elem.append(_mrow_of(base))
    elem.append(_mrow_of(sub))
    elem.append(_mrow_of(sup))
    return [elem]


def _convert_spre(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:sPre>`` — pre-subscript/superscript (Word's left-side
    sub/sup). MathML lacks a single mapping; closest is ``<mmultiscripts>``."""
    base = _first_child(node, "e")
    sub = _first_child(node, "sub")
    sup = _first_child(node, "sup")
    if base is None or sub is None or sup is None:
        return [mtext("(invalid sPre)")]
    elem = ET.Element("mmultiscripts")
    elem.append(_mrow_of(base))
    # Trailing (post) scripts: none.
    none1 = ET.Element("none")
    none2 = ET.Element("none")
    elem.append(none1)
    elem.append(none2)
    # Pre-scripts marker.
    elem.append(ET.Element("mprescripts"))
    elem.append(_mrow_of(sub))
    elem.append(_mrow_of(sup))
    return [elem]


def _convert_radical(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:rad>`` → ``<msqrt>`` (no degree) or ``<mroot>`` (with).

    Word's ``hideDeg=on`` attribute means "degree present but hidden";
    we honour visibility by treating it as ``<msqrt>``.
    """
    deg = _first_child(node, "deg")
    radicand = _first_child(node, "e")
    if radicand is None:
        return [mtext("(invalid radical)")]
    hide_deg = _math_property(node, "radPr", "degHide") == "on"
    if deg is None or hide_deg or not list(deg):
        msqrt = ET.Element("msqrt")
        msqrt.append(_mrow_of(radicand))
        return [msqrt]
    mroot = ET.Element("mroot")
    mroot.append(_mrow_of(radicand))
    mroot.append(_mrow_of(deg))
    return [mroot]


def _convert_nary(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:nary>`` — n-ary operator (sum, product, integral, ...).

    Becomes ``<munderover><mo>op</mo>sub sup</munderover>e`` for limit
    location ``undOvr``, otherwise ``<msubsup>``. Word also lets the
    operator character be hidden (``subHide`` / ``supHide``); the
    output reflects that by omitting the corresponding child.
    """
    pr = _first_child(node, "naryPr")
    op_char = _math_property_of(pr, "chr") if pr is not None else None
    if not op_char:
        op_char = "∫"  # default integral, per Word (also when chr="")
    lim_loc = _math_property_of(pr, "limLoc") if pr is not None else None
    sub_hide = _math_property_of(pr, "subHide") == "on" if pr is not None else False
    sup_hide = _math_property_of(pr, "supHide") == "on" if pr is not None else False
    base = _first_child(node, "e")
    sub = _first_child(node, "sub")
    sup = _first_child(node, "sup")

    op = ET.Element("mo")
    op.text = op_char

    # Decide which sub/sup script container to use.
    sub_elem = (
        _mrow_of(sub) if sub is not None and not sub_hide else None
    )
    sup_elem = (
        _mrow_of(sup) if sup is not None and not sup_hide else None
    )
    if sub_elem is None and sup_elem is None:
        scripted = op
    else:
        # limLoc absent → munderover (limits above/below).  Word's true
        # default is location-by-operator (∑/∏ stack, ∫ scripts to the
        # side), so this slightly over-stacks a bare ∫ — but the braille
        # backend treats munderover and msubsup limits identically, so the
        # emitted cells are the same either way.  Kept simple until that
        # distinction starts to matter.
        use_underover = lim_loc != "subSup"
        container_tag = "munderover" if use_underover else "msubsup"
        if sub_elem is not None and sup_elem is not None:
            scripted = ET.Element(container_tag)
            scripted.append(op)
            scripted.append(sub_elem)
            scripted.append(sup_elem)
        elif sub_elem is not None:
            tag = "munder" if use_underover else "msub"
            scripted = ET.Element(tag)
            scripted.append(op)
            scripted.append(sub_elem)
        else:
            assert sup_elem is not None
            tag = "mover" if use_underover else "msup"
            scripted = ET.Element(tag)
            scripted.append(op)
            scripted.append(sup_elem)

    mrow = ET.Element("mrow")
    mrow.append(scripted)
    if base is not None:
        # The integrand / summand follows the operator.
        for c in _wrap_children(base):
            mrow.append(c)
    return [mrow]


def _convert_func(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:func>`` → ``<mrow><name><argument></mrow>``."""
    fname = _first_child(node, "fName")
    arg = _first_child(node, "e")
    if fname is None or arg is None:
        return [mtext("(invalid func)")]
    mrow = ET.Element("mrow")
    for c in _wrap_children(fname):
        mrow.append(c)
    # Apply-function operator U+2061 keeps the name semantically distinct
    # from the argument; the normalizer drops it (invisible operator)
    # before the backend sees the tree.
    apply_op = ET.Element("mo")
    apply_op.text = "⁡"
    mrow.append(apply_op)
    for c in _wrap_children(arg):
        mrow.append(c)
    return [mrow]


def _convert_delim(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:d>`` — delimited expression (parens, brackets, braces)."""
    pr = _first_child(node, "dPr")
    beg = _math_property_of(pr, "begChr") if pr is not None else None
    end = _math_property_of(pr, "endChr") if pr is not None else None
    sep = _math_property_of(pr, "sepChr") if pr is not None else None
    beg = beg if beg is not None else "("
    end = end if end is not None else ")"
    sep = sep if sep is not None else "|"

    mrow = ET.Element("mrow")
    if beg:
        op = ET.Element("mo")
        op.text = beg
        op.set("fence", "true")
        mrow.append(op)
    entries = _children_with(node, "e")
    for i, e in enumerate(entries):
        if i > 0 and sep:
            op = ET.Element("mo")
            op.text = sep
            op.set("separator", "true")
            mrow.append(op)
        for c in _wrap_children(e):
            mrow.append(c)
    if end:
        op = ET.Element("mo")
        op.text = end
        op.set("fence", "true")
        mrow.append(op)
    return [mrow]


def _convert_matrix(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:m>`` → ``<mtable>`` of ``<mtr>``/``<mtd>``."""
    mtable = ET.Element("mtable")
    for mr in _children_with(node, "mr"):
        mtr = ET.Element("mtr")
        for cell in _children_with(mr, "e"):
            mtd = ET.Element("mtd")
            for c in _wrap_children(cell):
                mtd.append(c)
            mtr.append(mtd)
        mtable.append(mtr)
    return [mtable]


def _convert_eqarr(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:eqArr>`` — equation array (one column, many rows)."""
    mtable = ET.Element("mtable")
    for row in _children_with(node, "e"):
        mtr = ET.Element("mtr")
        mtd = ET.Element("mtd")
        for c in _wrap_children(row):
            mtd.append(c)
        mtr.append(mtd)
        mtable.append(mtr)
    return [mtable]


def _convert_lim_low(node: ET.Element) -> list[ET.Element]:
    return _convert_limit(node, "munder")


def _convert_lim_upp(node: ET.Element) -> list[ET.Element]:
    return _convert_limit(node, "mover")


def _convert_limit(node: ET.Element, mathml_tag: str) -> list[ET.Element]:
    base = _first_child(node, "e")
    lim = _first_child(node, "lim")
    if base is None or lim is None:
        return [mtext("(invalid limit)")]
    elem = ET.Element(mathml_tag)
    elem.append(_mrow_of(base))
    elem.append(_mrow_of(lim))
    return [elem]


def _convert_group_chr(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:groupChr>`` — grouping character (brace etc.) under/over."""
    pr = _first_child(node, "groupChrPr")
    chr_val = _math_property_of(pr, "chr") if pr is not None else None
    pos = _math_property_of(pr, "pos") if pr is not None else None
    if not chr_val:  # absent OR explicit empty → default (as _convert_nary);
        chr_val = "⏟"  # an empty <mo/> here would be a meaningless wrapper
    base = _first_child(node, "e")
    if base is None:
        return [mtext("(invalid groupChr)")]
    container = ET.Element("mover" if pos == "top" else "munder")
    container.append(_mrow_of(base))
    op = ET.Element("mo")
    op.text = chr_val
    container.append(op)
    return [container]


def _convert_bar(node: ET.Element) -> list[ET.Element]:
    pr = _first_child(node, "barPr")
    pos = _math_property_of(pr, "pos") if pr is not None else None
    base = _first_child(node, "e")
    if base is None:
        return [mtext("(invalid bar)")]
    container = ET.Element("munder" if pos == "bot" else "mover")
    container.append(_mrow_of(base))
    op = ET.Element("mo")
    op.text = "¯"  # macron / overbar
    container.append(op)
    return [container]


def _convert_acc(node: ET.Element) -> list[ET.Element]:
    pr = _first_child(node, "accPr")
    chr_val = _math_property_of(pr, "chr") if pr is not None else None
    if not chr_val:  # absent OR explicit empty → default (as _convert_nary);
        chr_val = "̂"  # combining circumflex, not a meaningless empty <mo/>
    base = _first_child(node, "e")
    if base is None:
        return [mtext("(invalid acc)")]
    container = ET.Element("mover")
    container.append(_mrow_of(base))
    op = ET.Element("mo")
    op.text = chr_val
    container.append(op)
    return [container]


def _convert_box(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:box>`` / ``<m:borderBox>`` — wrapper boxes. We pass the
    contents through; visual box-drawing isn't representable in braille."""
    base = _first_child(node, "e")
    if base is None:
        return []
    return _wrap_children(base)


def _convert_phant(node: ET.Element) -> list[ET.Element]:
    """OMML ``<m:phant>`` — invisible. The braille backend renders
    ``<mphantom>`` as nothing, which is what Word means by ``phant``."""
    base = _first_child(node, "e")
    if base is None:
        return []
    mphantom = ET.Element("mphantom")
    for c in _wrap_children(base):
        mphantom.append(c)
    return [mphantom]


def _passthrough_child(node: ET.Element) -> list[ET.Element]:
    """For wrapper elements (``m:e``, ``m:num``, ...) used as direct
    converter targets, just convert their children."""
    return _wrap_children(node)


# ---------------------------------------------------------------------------
# Property helpers
# ---------------------------------------------------------------------------


def _math_property(node: ET.Element, pr_name: str, child_name: str) -> str | None:
    """Read ``node/<m:{pr_name}>/<m:{child_name}>``'s ``m:val`` attribute.

    OMML stores most settings as ``<m:type m:val="..."/>``-style child
    elements, not XML attributes. The lookup is forgiving — missing
    elements yield ``None`` so callers can default cleanly.
    """
    pr = _first_child(node, pr_name)
    if pr is None:
        return None
    return _math_property_of(pr, child_name)


def _math_property_of(pr: ET.Element, child_name: str) -> str | None:
    child = _first_child(pr, child_name)
    if child is None:
        return None
    # ``m:val`` lives in the OMML namespace; ``val`` is a Word-extension
    # alias some tooling emits. Fall back to the alias only when the
    # namespaced attribute is ABSENT — an explicit empty value is
    # meaningful (Word writes ``<m:endChr m:val=""/>`` for "no right
    # delimiter" in piecewise-function braces; an ``or`` chain would
    # swallow it and invent a phantom ``)`` downstream).
    value = child.get(_OMML_PREFIX + "val")
    if value is None:
        value = child.get("val")
    return value


# ---------------------------------------------------------------------------
# Handler table
# ---------------------------------------------------------------------------


_HANDLERS: dict[str, Callable[..., Any]] = {
    "r": _convert_run,
    "t": lambda n: tokenize_math_text((n.text or "").strip()),
    "f": _convert_fraction,
    "sSub": _convert_ssub,
    "sSup": _convert_ssup,
    "sSubSup": _convert_ssubsup,
    "sPre": _convert_spre,
    "rad": _convert_radical,
    "nary": _convert_nary,
    "func": _convert_func,
    "d": _convert_delim,
    "m": _convert_matrix,
    "eqArr": _convert_eqarr,
    "limLow": _convert_lim_low,
    "limUpp": _convert_lim_upp,
    "groupChr": _convert_group_chr,
    "bar": _convert_bar,
    "acc": _convert_acc,
    "box": _convert_box,
    "borderBox": _convert_box,
    "phant": _convert_phant,
    # Wrapper / pass-through containers.
    "e": _passthrough_child,
    "num": _passthrough_child,
    "den": _passthrough_child,
    "sub": _passthrough_child,
    "sup": _passthrough_child,
    "deg": _passthrough_child,
    "lim": _passthrough_child,
    "fName": _passthrough_child,
    "mr": _passthrough_child,
    "oMath": _passthrough_child,
    # Property nodes carry settings, no content — drop.
    "rPr": lambda n: [],
    "ctrlPr": lambda n: [],
    "fPr": lambda n: [],
    "naryPr": lambda n: [],
    "dPr": lambda n: [],
    "radPr": lambda n: [],
    "mPr": lambda n: [],
    "eqArrPr": lambda n: [],
    "groupChrPr": lambda n: [],
    "barPr": lambda n: [],
    "accPr": lambda n: [],
    "boxPr": lambda n: [],
    "borderBoxPr": lambda n: [],
    "phantPr": lambda n: [],
    "sSubPr": lambda n: [],
    "sSupPr": lambda n: [],
    "sSubSupPr": lambda n: [],
    "sPrePr": lambda n: [],
    "limLowPr": lambda n: [],
    "limUppPr": lambda n: [],
    "funcPr": lambda n: [],
}
