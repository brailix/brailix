"""Small pure helpers shared by the math handler set.

Everything in here is either:

* a tiny shape check on ``ET.Element`` (``_is_atomic`` /
  ``_is_single_digit_mn``);
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
from brailix.core.chars import nonstandard_char_hint
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell

# Roles that reset ``need_number_sign`` on the next digit run.
_NUMBER_BREAKING_ROLES: frozenset[str] = frozenset(
    {"op", "rel", "delim", "shape", "big_op"}
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


_SINGLE_STRUCTURE_TAGS: frozenset[str] = frozenset({
    "mn", "mi",
    "msqrt", "mroot",
    "mfrac",
    "msub", "msup", "msubsup",
    "munder", "mover", "munderover",
})

_SCRIPT_LIKE_TAGS: frozenset[str] = frozenset({
    "msub", "msup", "msubsup",
    "munder", "mover", "munderover",
})


def _is_typed_slash_mrow(elem: ET.Element) -> bool:
    """The typed-slash fraction shape: an ``<mrow>`` of exactly
    ``[X, <mo>/</mo>, Y]``. :func:`handlers.containers._emit_mrow`
    re-dispatches it through the fraction handler so ``a / b`` gets the
    same encoding as ``\\frac{a}{b}``."""
    if elem.tag != "mrow":
        return False
    kids = list(elem)
    return (
        len(kids) == 3
        and kids[1].tag == "mo"
        and (kids[1].text or "").strip() == "/"
    )


def _mi_routes_to_function(text: str, profile) -> bool:
    """Whether a multi-char ``<mi>`` takes the function path (⠫ prefix +
    abbreviation or spelled-out name).

    True for registered function names (``sin`` / ``Tr`` — backslash
    stripped first, matching the lookup in ``_emit_function_name``), and
    for content that isn't a pure letter run (a literal ``\\foo`` from an
    unrecognised LaTeX command). A pure letter run that isn't a
    registered function (``ab`` / ``max``-as-``\\mathrm`` / OMML word
    runs) is NOT a function — it renders as letters with per-class
    letter signs instead (see ``_emit_letter_runs``).
    """
    if profile.math_function(text.lstrip("\\")) is not None:
        return True
    return any(profile.letter_class(ch) is None for ch in text)


def _is_function_head(elem: ET.Element | None, profile) -> bool:
    """A node that renders as a function name (⠫ prefix + name cells):

    * a multi-char ``<mi>`` that routes to the function path (registered
      abbreviation, or non-letter content spelled behind ⠫) — a plain
      multi-letter run (``ab``) is a letter word, not a function;
    * an ``<mo>`` whose text is a registered function name — the
      ``_emit_mo`` fallback path for latex2mathml's ``<mo>lim</mo>``;
    * a script / limit wrapper (msub / msup / msubsup / munder / mover /
      munderover) whose base is such a node — ``\\log_2`` / ``\\sin^2``
      / ``\\lim_{x \\to 0}``.
    """
    if elem is None:
        return False
    if elem.tag == "mi":
        text = (elem.text or "").strip()
        return (
            len(text) > 1
            and not list(elem)
            and _mi_routes_to_function(text, profile)
        )
    if elem.tag == "mo":
        text = (elem.text or "").strip()
        return len(text) > 1 and profile.math_function(text) is not None
    if elem.tag in _SCRIPT_LIKE_TAGS:
        kids = list(elem)
        return bool(kids) and _is_function_head(kids[0], profile)
    return False


def _is_function_application(elem: ET.Element | None, profile) -> bool:
    """An ``<mrow>`` of exactly ``[function head, argument]`` where the
    argument is a single self-fenced structure — ``cos α``, ``sin x²``,
    ``log₂ x``.

    A function applied to one operand is a single
    term: the ⠫ function prefix opens it and the argument's own shape
    closes it, so a fraction with such a numerator / denominator keeps
    the simple bar form (no ⠆…⠰ brackets) — ``⠫cos α⠳a`` reads
    unambiguously as (cos α)/a because cos-of-a-fraction is *required*
    to take the bracketed compound form (see
    ``MathBrailleContext.fraction_is_function_arg``).

    A multi-token argument run (``cos 2α`` — three siblings) does not
    qualify; such a numerator stays in the compound form. Invisible
    apply-function operators never appear here — the normalizer drops
    them before the backend runs.
    """
    if elem is None:
        return False
    while elem.tag == "mrow" and len(elem) == 1:
        elem = elem[0]
    if elem.tag != "mrow":
        return False
    kids = list(elem)
    if len(kids) != 2:
        return False
    head, arg = kids
    return _is_function_head(head, profile) and _is_self_fenced(arg, profile)


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
    single terms — a single *self-fenced* structure or a function
    application (``cos α``) — and ``math.simplify_fraction`` is on.
    """
    return (
        _fraction_operand_is_term(numerator, profile)
        and _fraction_operand_is_term(denominator, profile)
        and profile.feature("math.simplify_fraction", True)
    )


def _fraction_operand_is_term(elem: ET.Element | None, profile) -> bool:
    """One numerator / denominator counts as a single term for the simple
    bar form: a self-fenced structure, or a function application whose
    argument is one (``\\frac{\\cos α}{a}`` keeps the bare bar)."""
    return _is_self_fenced(elem, profile) or _is_function_application(
        elem, profile
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
    """Whether a script's content is self-delimiting, so the trailing
    ``script.close`` marker can be omitted.

    Only a bare number (``<mn>``) qualifies: a digit run carries its own
    number context, so ``x_1`` / ``x^{12}`` read unambiguously without a
    close. A single *letter* (``<mi>``) does NOT — ``a^n`` / ``a_n`` keep
    the close to bound the script. Used only by the regular-script close decision.
    """
    if elem is None or elem.tag != "mn":
        return False
    if list(elem):
        return False
    return bool((elem.text or "").strip())


def _is_single_digit_mn(elem: ET.Element | None) -> bool:
    if elem is None or elem.tag != "mn":
        return False
    if list(elem):
        return False
    text = (elem.text or "").strip()
    return len(text) == 1 and text.isdigit()


def _last_is_blank(cells: list[BrailleCell]) -> bool:
    """Is the last cell already a separator, so a following operator/
    connector must not add another blank?

    Judge by *role*, not by ``dots == ()``. BLANK_CELL, LINE_BREAK_CELL,
    HANG_OPEN_CELL, HANG_CLOSE_CELL and unknown placeholder cells all
    carry empty dots, so the old ``dots == ()`` test treated every one of
    them as a blank. That swallowed the required space before a binary
    operator after a matrix / determinant / equation-system (which ends
    in HANG_CLOSE_CELL): ``|A| = 5`` lost the blank before ``=``. Only a
    real space and a line break count as separation here — a line break
    renders to whitespace so a following blank stays suppressed, but a
    closed hanging group or an unknown symbol is content the next
    operator must be spaced away from."""
    return bool(cells) and cells[-1].role in {"space", "line_break"}


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


def _describe_nonstandard_char(text: str) -> str:
    """A full, actionable reason for a character the chem parser can't accept,
    for the soft-``<merror>`` warning — a full-width / zero-width hint when one
    applies (see :func:`~brailix.core.chars.nonstandard_char_hint`), else
    a plain "unsupported character". Never rewrites the input."""
    return nonstandard_char_hint(text) or f"unsupported character {text!r}"


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


# Containers whose children form a *linear sequence* — the only places
# where adjacent siblings may be coalesced into runs. Positional
# containers (msub / msup / mfrac / mroot / munder / ...) give each child
# slot a distinct structural meaning, so merging across their direct
# children would swallow structure: ``l_n`` must stay l-sub-n, never
# become the function ``ln``; ``T_r`` must not become ``Tr``.
_SEQUENCE_CONTAINER_TAGS: frozenset[str] = frozenset(
    {"math", "mrow", "msqrt", "mtd"}
)


def _coalesce_identifier_runs(elem: ET.Element, profile) -> ET.Element:
    """Return ``elem`` with consecutive single-char ``<mi>`` runs merged:
    first runs spelling a registered function name into one function
    ``<mi>`` (greedy longest match), then remaining adjacent letters into
    one letter-word ``<mi>`` so the letter-sign rule sees whole runs.

    Both merges apply only to the children of *sequence* containers
    (:data:`_SEQUENCE_CONTAINER_TAGS`); the children of positional
    containers are distinct slots and are never merged with each other
    (recursion still descends into them).

    **Does not mutate the input tree.** When nothing changes at this
    element or below, the original element is returned unchanged — so the
    common "no run" case allocates nothing and subtrees are shared.
    Otherwise a fresh element is built along the path that changed. The
    math IR (``MathInline.math``) is consumed read-only by the backend
    and is cached in the pipeline / serialized into the proofread JSON
    (see ``ARCHITECTURE.md``), so coalescing must never edit
    it in place.

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
    new_children = [_coalesce_identifier_runs(child, profile) for child in original]
    changed = any(nc is not oc for nc, oc in zip(new_children, original, strict=True))

    if elem.tag in _SEQUENCE_CONTAINER_TAGS and len(new_children) >= 2:
        merged = _merge_function_name_runs(new_children, profile)
        if merged is not new_children:
            new_children = merged
            changed = True
        merged = _merge_letter_word_runs(new_children, profile)
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


def _is_letter_run_mi(elem: ET.Element, profile) -> bool:
    """A bare single-letter ``<mi>`` eligible for letter-run merging.

    Wider than :func:`_is_single_char_mi` (the function-name predicate):
    the letter must be in one of the profile's letter tables, and the
    upright/italic ``mathvariant`` values plus ``data-bk-span`` are
    allowed — ``\\mathrm{ABC}`` letters are exactly the words this rule
    exists for. Other ``mathvariant`` values (bold / script / fraktur /
    double-struck) are semantically distinct symbols and stay per-char.
    """
    if elem.tag != "mi" or list(elem):
        return False
    text = elem.text or ""
    if len(text) != 1 or profile.letter_class(text) is None:
        return False
    for key, value in elem.attrib.items():
        if key == "data-bk-span":
            continue
        if key == "mathvariant" and value in ("normal", "italic"):
            continue
        return False
    return True


def _merge_letter_word_runs(
    children: list[ET.Element], profile
) -> list[ET.Element]:
    """Merge adjacent single-letter ``<mi>`` siblings (two or more) into
    one multi-letter ``<mi>`` so the letter-sign rule can see the whole
    run — one sign per same-class
    stretch instead of one per letter. Runs that span class changes
    (``mW`` / ``πr``) still merge; the emitter re-partitions by class.

    Returns the *same list object* when no merge applies (so callers can
    detect "unchanged" by identity), otherwise a new list.

    ``data-bk-span`` provenance: when every member of a run carries a
    parseable span the merged ``<mi>`` gets their union (min start, max
    end); when none do the attribute is omitted. A mixed run is left
    unmerged — collapsing it would mis-attribute cells to the spanned
    subset.
    """
    out: list[ET.Element] = []
    i = 0
    n = len(children)
    merged_any = False
    while i < n:
        if not _is_letter_run_mi(children[i], profile):
            out.append(children[i])
            i += 1
            continue
        run_end = i + 1
        while run_end < n and _is_letter_run_mi(children[run_end], profile):
            run_end += 1
        run = children[i:run_end]
        if len(run) < 2:
            out.append(children[i])
            i = run_end
            continue
        spans = [_parse_bk_span(c.get("data-bk-span")) for c in run]
        present = [s for s in spans if s is not None]
        if present and len(present) != len(run):
            out.extend(run)
            i = run_end
            continue
        merged = ET.Element("mi")
        merged.text = "".join(c.text or "" for c in run)
        if present:
            merged.set(
                "data-bk-span",
                f"{min(s.start for s in present)},{max(s.end for s in present)}",
            )
        out.append(merged)
        merged_any = True
        i = run_end
    return out if merged_any else children
