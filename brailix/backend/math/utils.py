"""Small pure helpers shared by the math handler set.

Everything in here is either:

* a tiny shape check on ``ET.Element`` (``_is_leaf_like`` / ``_is_atomic``
  / ``_is_single_digit_mn``);
* a span / cell construction helper (``_unknown_cell`` / ``_fallback_surface``
  / ``_parse_bk_span``);
* a tag-shape unpacker (``_unpack_script`` / ``_unpack_under_over``);
* a profile-driven structure emitter (``_emit_structure``);
* a constant table (``_NUMBER_BREAKING_ROLES`` / ``_ROLE_TO_CELL_ROLE``).

None of these recursively re-enter the dispatcher, so they live outside
the handlers module — keeping the handler file smaller and easier to
scan during rule-by-rule audits.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math.context import MathBrailleContext
from brailix.core.span import Span
from brailix.ir.braille import BLANK_CELL, BrailleCell

# Roles that reset ``need_number_sign`` on the next digit run.
_NUMBER_BREAKING_ROLES: frozenset[str] = frozenset(
    {"op", "rel", "shape", "big_op"}
)


# Map profile-level symbol roles to the cell-level role tag.
_ROLE_TO_CELL_ROLE: dict[str, str] = {
    "op": "math_op",
    "rel": "math_rel",
    "delim": "math_delim",
    "punct": "math_punct",
    "shape": "math_shape",
    "big_op": "math_big_op",
}


def _parse_bk_span(value: str | None) -> Span | None:
    """Parse a ``data-bk-span`` attribute value.

    Accepts ``"start,end"`` (decimal integers, optionally space-padded)
    and returns a :class:`Span`. Returns ``None`` on missing input,
    malformed text, or numerically invalid spans — silent fallback
    is intentional so a stray attrib doesn't break translation.
    """
    if not value:
        return None
    parts = value.split(",")
    if len(parts) != 2:
        return None
    try:
        start = int(parts[0].strip())
        end = int(parts[1].strip())
    except ValueError:
        return None
    if start < 0 or end < start:
        return None
    return Span(start, end)


def _emit_structure(
    cells: list[BrailleCell],
    mctx: MathBrailleContext,
    name: str,
    *,
    role: str,
) -> None:
    """Emit a named structural marker (fraction bar, sup/sub indicators,
    sqrt brackets, big-op prefix, ...). Profiles that don't define the
    marker simply skip it — that's a configuration choice, not an error.
    """
    seq = mctx.profile.math_structure(name)
    for dots in seq:
        cells.append(
            BrailleCell(dots=dots, role=role, source_span=mctx.span)
        )


def _unpack_script(elem: ET.Element) -> tuple[ET.Element | None, ET.Element | None, ET.Element | None]:
    """Return (base, sub, sup) tuples for msub / msup / msubsup."""
    kids = list(elem)
    base = kids[0] if len(kids) >= 1 else None
    if elem.tag == "msub":
        sub = kids[1] if len(kids) >= 2 else None
        return base, sub, None
    if elem.tag == "msup":
        sup = kids[1] if len(kids) >= 2 else None
        return base, None, sup
    # msubsup
    sub = kids[1] if len(kids) >= 2 else None
    sup = kids[2] if len(kids) >= 3 else None
    return base, sub, sup


def _unpack_under_over(elem: ET.Element) -> tuple[ET.Element | None, ET.Element | None, ET.Element | None]:
    """Return (base, sub, sup) for munder / mover / munderover.

    By design under≈sub and over≈sup for the script handler.
    """
    kids = list(elem)
    base = kids[0] if len(kids) >= 1 else None
    if elem.tag == "munder":
        sub = kids[1] if len(kids) >= 2 else None
        return base, sub, None
    if elem.tag == "mover":
        sup = kids[1] if len(kids) >= 2 else None
        return base, None, sup
    # munderover
    sub = kids[1] if len(kids) >= 2 else None
    sup = kids[2] if len(kids) >= 3 else None
    return base, sub, sup


def _is_leaf_like(elem: ET.Element | None) -> bool:
    """A 'leaf-like' element is a single-token ``<mi>`` or ``<mn>`` with
    no children. Used by the script simplifiability check (close marker
    omission for atomic sup/sub content)."""
    if elem is None:
        return False
    if elem.tag not in ("mi", "mn"):
        return False
    if list(elem):
        return False
    text = (elem.text or "").strip()
    if not text:
        return False
    # Single-token: a one-character mi, or any mn (single number).
    if elem.tag == "mi" and len(text) > 1:
        return False
    return True


_SINGLE_STRUCTURE_TAGS: frozenset[str] = frozenset({
    "mn", "mi",
    "msqrt", "mroot",
    "mfrac",
    "msub", "msup", "msubsup",
    "munder", "mover", "munderover",
})


def _is_single_structure(elem: ET.Element | None) -> bool:
    """One self-fenced MathML element.

    Used by the fraction simplifiability check: when both numerator and
    denominator are a single structure, the fraction renders without
    ⠆…⠰ open/close brackets — the inner structure carries its own
    closing marker (sqrt.close, script.close, Antoine lower digit,
    nested fraction.close) so the bar position is unambiguous.

    Single-child ``<mrow>`` wrappers are transparent — latex2mathml
    wraps numerator/denominator in `<mrow>` even when they hold one
    element, and that wrapping shouldn't force compound form.
    """
    if elem is None:
        return False
    # Peel transparent single-child mrow wrappers.
    while elem.tag == "mrow":
        kids = list(elem)
        if len(kids) != 1:
            return False
        elem = kids[0]
    if elem.tag not in _SINGLE_STRUCTURE_TAGS:
        return False
    # An empty leaf doesn't count: an empty mn / mi renders nothing,
    # which would leave the bar without a recognisable operand.
    if elem.tag in ("mn", "mi") and not (elem.text or "").strip():
        return False
    return True


def _peel_single_mrow(elem: ET.Element | None) -> ET.Element | None:
    """Peel transparent single-child ``<mrow>`` wrappers (latex2mathml
    wraps even a one-element numerator/denominator), returning the inner
    element, or ``None`` if a wrapper holds anything but exactly one child.
    """
    while elem is not None and elem.tag == "mrow":
        kids = list(elem)
        if len(kids) != 1:
            return None
        elem = kids[0]
    return elem


def _antoine_applies(
    numerator: ET.Element | None, denominator: ET.Element | None, profile
) -> bool:
    """True if both operands are single-digit ``<mn>`` with Antoine
    upper/lower digit forms in the profile — the compact fraction that is
    self-fenced (the lower-form digit implies the bar; no open/close)."""
    if not _is_single_digit_mn(numerator) or not _is_single_digit_mn(denominator):
        return False
    assert numerator is not None and denominator is not None  # narrowed above
    upper = profile.digits.get((numerator.text or "").strip())
    lower = profile.math_digits_lower.get((denominator.text or "").strip())
    return bool(upper and lower)


def _is_self_fenced(elem: ET.Element | None, profile) -> bool:
    """A single structure that carries its own right-hand fence, so a
    fraction bar placed immediately after it is unambiguous.

    Like :func:`_is_single_structure`, but a nested *simple* fraction does
    **not** qualify: it renders as a bare ``a bar b`` with no closing mark,
    so wrapping it in another fraction's simple form would flatten into an
    ambiguous slash chain (``a/b/c`` can't distinguish ``(a/b)/c`` from
    ``a/(b/c)``). Antoine and compound fractions DO carry a fence
    (lower-digit / explicit close) and still qualify.
    """
    if not _is_single_structure(elem):
        return False
    if _fraction_renders_simple(elem, profile):
        return False
    return True


def _fraction_simplifiable(
    numerator: ET.Element | None, denominator: ET.Element | None, profile
) -> bool:
    """Whether an ``<mfrac>`` with these operands renders in the simple bar
    form (``numerator bar denominator``, no brackets): both operands are
    single *self-fenced* structures and ``math.simplify_fraction`` is on.
    """
    return (
        _is_self_fenced(numerator, profile)
        and _is_self_fenced(denominator, profile)
        and profile.feature("math.simplify_fraction", True)
    )


def _fraction_renders_simple(elem: ET.Element | None, profile) -> bool:
    """True if ``elem`` (single-mrow-peeled) is an ``<mfrac>`` that renders
    in the bare simple bar form — i.e. without any closing fence.

    Used by :func:`_is_self_fenced` to decide that such a fraction can't be
    nested inside another fraction's simple form. Antoine fractions (lower
    digit implies the bar) and compound fractions (explicit ``fraction.close``)
    return ``False`` here because they are self-fenced.
    """
    elem = _peel_single_mrow(elem)
    if elem is None or elem.tag != "mfrac":
        return False
    kids = list(elem)
    numerator = kids[0] if len(kids) >= 1 else None
    denominator = kids[1] if len(kids) >= 2 else None
    if _antoine_applies(numerator, denominator, profile):
        return False
    return _fraction_simplifiable(numerator, denominator, profile)


def _is_atomic(elem: ET.Element | None) -> bool:
    """An element is *atomic* if it's a single-token ``<mi>`` / ``<mn>``.
    Used by the script simplifiability check."""
    return _is_leaf_like(elem)


def _is_single_digit_mn(elem: ET.Element | None) -> bool:
    if elem is None or elem.tag != "mn":
        return False
    if list(elem):
        return False
    text = (elem.text or "").strip()
    return len(text) == 1 and text.isdigit()


def _last_is_blank(cells: list[BrailleCell]) -> bool:
    return bool(cells) and cells[-1].dots == BLANK_CELL.dots


# Cell roles that mean "the previous symbol can't be the left operand of a
# binary operator" — used by :func:`_emit_mo` to drop the leading blank an
# operator like ``-`` or ``+`` would normally claim. Together with the
# empty-cells check, this is what distinguishes the minus sign ``a - b`` (binary,
# keeps the blank because ``a`` is an operand) from the negative sign ``= -5`` (unary,
# drops the blank because ``=`` is itself a relation). ``math_delim``
# isn't blanket-listed because closing delims like ``)`` mark the end of
# a bracketed operand (``(a) - b`` is binary); open delims are detected
# by source-text below.
_UNARY_CONTEXT_ROLES: frozenset[str] = frozenset(
    {"math_op", "math_rel", "math_big_op"}
)
_OPEN_DELIMS: frozenset[str] = frozenset({"(", "[", "{"})


def _previous_suppresses_space_before(cells: list[BrailleCell]) -> bool:
    """Return True when the trailing cell of ``cells`` makes the next
    ``space_before`` operator behave like a unary sign — another
    operator / relation / big-operator or an *open* delimiter sits
    immediately before, so this operator has no left operand to space
    away from."""
    if not cells:
        return False
    last = cells[-1]
    if last.role in _UNARY_CONTEXT_ROLES:
        return True
    if last.role == "math_delim" and last.source_text in _OPEN_DELIMS:
        return True
    return False


def _unknown_cell(text: str, span: Span | None) -> BrailleCell:
    return BrailleCell(dots=(), role="unknown", source_span=span, source_text=text)


def _fallback_surface(surface: str, span: Span | None) -> list[BrailleCell]:
    return [
        BrailleCell(
            dots=(),
            role="unknown",
            source_span=Span(span.start + i, span.start + i + 1) if span else None,
            source_text=ch,
        )
        for i, ch in enumerate(surface)
    ]


def _is_single_char_mi(elem: ET.Element) -> bool:
    """A bare ``<mi>`` with exactly one character of text — the shape
    MTEF (and per-letter OMML runs) produce when a function name like
    ``cos`` is stored as three separate character records.
    """
    if elem.tag != "mi":
        return False
    if list(elem):
        return False
    if elem.attrib:
        return False
    text = elem.text or ""
    return len(text) == 1


def _coalesce_function_names(elem: ET.Element, profile) -> ET.Element:
    """Return ``elem`` with consecutive single-char ``<mi>`` runs whose
    concatenation matches a registered function name merged into one
    ``<mi>``.

    **Does not mutate the input tree.** When nothing changes at this
    element or below, the original element is returned unchanged — so the
    common "no function-name run" case allocates nothing and subtrees are
    shared. Otherwise a fresh element is built along the path that
    changed. The math IR (``MathInline.math``) is consumed read-only by
    the backend and is cached in the pipeline / serialized into the
    proofread JSON (see ``ARCHITECTURE.md``), so coalescing
    must never edit it in place.

    MTEF stores each character of ``cos`` / ``sin`` / ``arcsin`` as its
    own record, so the math frontend emits one ``<mi>`` per letter and
    the backend's per-element handler can't see they form a function.
    Greedy longest-match here lets ``arcsin`` win over ``arc`` + ``sin``
    and ``sinh`` win over ``sin`` + ``h``.

    A ``data-bk-chem`` tree is left untouched: its ``<mi>`` nodes are
    element symbols (authoritative), not function-name letter runs, so we
    must never coalesce them. The attribute only sits on the chem root, so
    short-circuiting here skips the whole chemical formula.
    """
    if elem.get("data-bk-chem") is not None:
        return elem
    original = list(elem)
    new_children = [_coalesce_function_names(child, profile) for child in original]
    changed = any(nc is not oc for nc, oc in zip(new_children, original, strict=True))

    if len(new_children) >= 2:
        merged = _merge_function_name_runs(new_children, profile)
        if merged is not new_children:
            new_children = merged
            changed = True

    if not changed:
        return elem

    # Build a new element; shared (unchanged) children are reused by
    # reference — safe because both trees are only ever read afterwards.
    out = ET.Element(elem.tag, dict(elem.attrib))
    out.text = elem.text
    out.tail = elem.tail
    out.extend(new_children)
    return out


def _merge_function_name_runs(
    children: list[ET.Element], profile
) -> list[ET.Element]:
    """Merge consecutive single-char ``<mi>`` runs that spell a known
    function name into one ``<mi>`` each. Returns the *same list object*
    when no merge applies (so callers can detect "unchanged" by identity),
    otherwise a new list.
    """
    out: list[ET.Element] = []
    i = 0
    n = len(children)
    merged_any = False
    while i < n:
        if not _is_single_char_mi(children[i]):
            out.append(children[i])
            i += 1
            continue
        run_end = i + 1
        while run_end < n and _is_single_char_mi(children[run_end]):
            run_end += 1
        k = i
        while k < run_end:
            best_len = 0
            for length in range(run_end - k, 1, -1):
                name = "".join(children[k + d].text or "" for d in range(length))
                if profile.math_function(name) is not None:
                    best_len = length
                    break
            if best_len > 0:
                merged = ET.Element("mi")
                merged.text = "".join(
                    children[k + d].text or "" for d in range(best_len)
                )
                out.append(merged)
                k += best_len
                merged_any = True
            else:
                out.append(children[k])
                k += 1
        i = run_end
    return out if merged_any else children
