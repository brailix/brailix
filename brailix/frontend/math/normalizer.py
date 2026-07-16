"""MathML normalization.

After a source adapter produces a MathML string, the normalizer cleans
it up so downstream consumers (the math backend) have fewer special
cases to handle:

* strip leading / trailing whitespace text nodes inside element runs;
* collapse single-child ``<mrow>`` wrappers (``<mrow><mi>x</mi></mrow>``
  → ``<mi>x</mi>``);
* neutralise presentational wrappers: ``<mstyle>`` / ``<mpadded>``
  (typographic hints only — latex2mathml wraps every ``\\displaystyle``
  formula in one) are renamed to ``<mrow>`` so their content keeps
  flowing through ordinary dispatch, and ``<mspace>`` / ``<mphantom>``
  (print-space occupiers with no braille meaning) are removed;
* drop invisible-operator ``<mo>`` elements (U+2061 function application
  / U+2062 invisible times / U+2063 invisible separator / U+2064
  invisible plus — the OMML ``m:func`` adapter emits U+2061): they
  render as nothing in print and braille alike, and removing them keeps
  sibling shapes uniform for the backend (a function name directly
  precedes its argument);
* drop the MathML XML namespace from every element tag so callers can
  match on bare local names (``mi``, ``mrow``, ...) instead of
  Clark-notation names.

The normalizer never raises — malformed input is wrapped into a single
``<merror>`` and returned. The backend turns that into an unknown cell
with a ``MATH_ERROR`` warning and the pipeline keeps running.

**Attribute preservation**: the normalizer rewrites ``elem.tag`` (to
drop namespaces) but never touches ``elem.attrib``. Any
``data-bk-*`` provenance attribute set by an adapter (e.g.
``data-bk-span="3,4"`` for sub-element source spans, see
``ARCHITECTURE.md``) survives normalization untouched.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.core._xml import (
    safe_fromstring,
    strip_namespace,
    strip_whitespace_text,
    tree_depth_exceeds,
)
from brailix.frontend.math.utils import merror_wrap

# A MathML tree deeper than this would overflow the recursive normalization
# passes below (and the math backend's recursive descent downstream). Real
# math nests well under ~30 levels; a corrupt / adversarial tree past the cap
# degrades to a single ``<merror>`` here instead of raising — honouring the
# "normalizer never raises" contract. The depth probe is iterative, so the
# guard itself is depth-safe.
_MAX_MATHML_DEPTH = 150


def normalize(mathml: str) -> ET.Element:
    """Parse a MathML string and return a normalized :class:`Element`
    tree with the MathML namespace stripped.

    Soft-failure contract: invalid XML is wrapped into a ``<merror>``
    document via :func:`merror_wrap` so the caller always gets a tree
    rooted at ``<math>``.
    """
    try:
        root = safe_fromstring(mathml)
    except ET.ParseError as e:
        root = ET.fromstring(merror_wrap(mathml, reason=f"parse error: {e}"))
    if tree_depth_exceeds(root, _MAX_MATHML_DEPTH):
        # Too deep for the recursive passes below — degrade to a <merror>
        # (shallow, so it sails through them) rather than overflow the stack.
        # Strip its namespace too, so the backend dispatches the bare
        # <merror> tag exactly as on the parse-error path.
        degraded = ET.fromstring(
            merror_wrap(
                "", reason=f"formula nested deeper than {_MAX_MATHML_DEPTH} levels"
            )
        )
        strip_namespace(degraded)
        return degraded
    strip_namespace(root)
    _drop_presentational(root)
    _collapse_singleton_mrows(root)
    strip_whitespace_text(root)
    _flag_repeated_operators(root)
    _tag_thousands_separators(root)
    _rewrite_degree_circle(root)
    return root


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------

# Unicode invisible operators: FUNCTION APPLICATION, INVISIBLE TIMES,
# INVISIBLE SEPARATOR, INVISIBLE PLUS. Semantic-MathML markers that render
# as nothing in print; braille likewise expresses them by juxtaposition
# (``2x``, mixed numbers, ``sin α``), so an ``<mo>`` holding one is dropped
# outright. The OMML ``m:func`` adapter emits U+2061 between a function
# name and its argument.
_INVISIBLE_OPERATORS: frozenset[str] = frozenset(
    {"\u2061", "\u2062", "\u2063", "\u2064"}
)


def _is_invisible_mo(elem: ET.Element) -> bool:
    return (
        elem.tag == "mo"
        and len(elem) == 0
        and (elem.text or "").strip() in _INVISIBLE_OPERATORS
    )


def _drop_presentational(elem: ET.Element) -> None:
    """In-place: neutralise presentational elements the backend has no
    handlers for, so real content never degrades to an unknown cell.

    * ``<mstyle>`` / ``<mpadded>`` carry only typographic hints
      (``displaystyle``, padding tweaks).  They are renamed to
      ``<mrow>`` — the wrapped content keeps its grouping and flows
      through ordinary dispatch.  latex2mathml wraps every
      ``\\displaystyle`` formula in ``<mstyle>``; before this rename
      the whole formula collapsed into a single unknown cell.
      Presentational attributes are dropped; ``data-bk-*`` provenance
      attributes (if an adapter ever sets them here) are kept.
    * ``<mspace>`` / ``<mphantom>`` exist to occupy *print* space —
      braille has no use for either (operator spacing is the backend's
      own profile-driven rule), so they are removed outright, phantom
      content included (it is invisible by definition). The one
      exception is ``<mspace linebreak="newline">`` — latex2mathml's
      output for a bare ``\\\\`` line break outside a table environment.
      That carries content-separating meaning (without it the flanking
      expressions would fuse), so it is kept for the backend's mspace
      handler to render as a blank-cell separator.
    * an ``<mo>`` holding a Unicode invisible operator
      (:data:`_INVISIBLE_OPERATORS`) is removed: it renders as nothing,
      and dropping it keeps a function name directly adjacent to its
      argument — the shape the backend's function-argument fraction
      rule keys off.

    Runs before :func:`_collapse_singleton_mrows` so a renamed
    single-child wrapper collapses away like any other ``<mrow>``.
    """
    for child in list(elem):
        if (
            (child.tag == "mspace" and child.get("linebreak") != "newline")
            or child.tag == "mphantom"
            or _is_invisible_mo(child)
        ):
            elem.remove(child)
            continue
        _drop_presentational(child)
        if child.tag in ("mstyle", "mpadded"):
            child.tag = "mrow"
            for key in [
                k for k in child.attrib if not k.startswith("data-bk-")
            ]:
                del child.attrib[key]


def _collapse_singleton_mrows(elem: ET.Element) -> None:
    """In-place: replace ``<mrow>`` elements that have exactly one
    child with that child. Visits descendants first so deeply nested
    redundant wrappers all collapse.
    """
    # Iterate over a snapshot so we can mutate in place.
    for child in list(elem):
        _collapse_singleton_mrows(child)
    new_children: list[ET.Element] = []
    for child in list(elem):
        if child.tag == "mrow" and len(child) == 1 and not (child.text and child.text.strip()):
            grand = child[0]
            # Carry the collapsed mrow's attributes (data-bk-span,
            # data-bk-chem, ...) onto the surviving child so normalization
            # stays attribute-preserving — backend dispatch reads these off
            # the tree (math-redesign §7 / math-boundaries §7.2). The
            # child's own value wins on conflict.
            for _k, _v in child.attrib.items():
                grand.attrib.setdefault(_k, _v)
            new_children.append(grand)
        else:
            new_children.append(child)
    # Replace the element's children, preserving order.
    if new_children != list(elem):
        for c in list(elem):
            elem.remove(c)
        for c in new_children:
            elem.append(c)


# Binary operators / relations whose *immediate* repetition (``==``, ``<<``,
# ``++``, ``--``) is almost always a typo rather than real notation — unlike
# ``!!`` (double factorial) or ``||`` (norm bars), which are intentionally
# doubled and so are excluded.
_REPEAT_TYPO_OPS: frozenset[str] = frozenset({"=", "<", ">", "+", "-"})


def _flag_repeated_operators(elem: ET.Element) -> None:
    """In-place: tag the second of two immediately-adjacent identical
    ``<mo>`` siblings (from :data:`_REPEAT_TYPO_OPS`) with
    ``data-bk-warn="repeated-operator"`` so the backend flags a likely typo.

    Only *adjacent* duplicates are flagged, so a legitimate chained relation
    (``a = b = c``, whose ``=`` operators are separated by operands) is left
    alone. This is the general-math counterpart to the chemistry frontend's
    own repeated-connector check; an ``<mo>`` already tagged (by chem) is left
    untouched. The cell still renders — faithful output, just a warning."""
    for child in list(elem):
        _flag_repeated_operators(child)
    kids = list(elem)
    for prev, cur in zip(kids, kids[1:], strict=False):
        text = (cur.text or "").strip()
        if (
            prev.tag == "mo"
            and cur.tag == "mo"
            and "data-bk-warn" not in cur.attrib
            and text in _REPEAT_TYPO_OPS
            and text == (prev.text or "").strip()
        ):
            cur.set("data-bk-warn", "repeated-operator")


# U+2218 RING OPERATOR — what latex2mathml emits for ``\circ``. LaTeX has no
# dedicated degree glyph, so authors spell a degree sign ``144^\circ``; the
# converter turns that into a number superscripted with this ring.
_RING_OPERATOR = "∘"


def _is_degree_circle_msup(elem: ET.Element) -> bool:
    """``<msup><mn>…</mn><mo>∘</mo></msup>`` — latex2mathml's spelling of a
    degree sign (``144^\\circ`` / ``144^{\\circ}``).

    Numeric base only: a superscript ring on a *symbol* is a different
    notation (set interior ``A^\\circ``, polar cone), so it keeps its own
    reading rather than being silently turned into degrees. The ``144^{\\circ}``
    form arrives with the ring wrapped in an ``<mrow>``, but this runs after
    :func:`_collapse_singleton_mrows`, so the sup is already a bare ``<mo>``.
    """
    return (
        elem.tag == "msup"
        and len(elem) == 2
        and elem[0].tag == "mn"
        and elem[1].tag == "mo"
        and (elem[1].text or "").strip() == _RING_OPERATOR
    )


def _rewrite_degree_circle(elem: ET.Element) -> None:
    """In-place: replace each ``<msup>NUMBER<mo>∘</mo></msup>`` degree shape
    (see :func:`_is_degree_circle_msup`) with the number followed by a
    baseline ``<mo>°</mo>``.

    This converges ``144^\\circ`` on the exact same tree as ``144\\degree``,
    so the backend renders it through its existing degree-sign mapping
    (``°`` -> ⠐⠴) with no superscript marker and no ``MATH_UNKNOWN_SYMBOL`` —
    keeping the "what does this input mean" decision in the frontend instead
    of teaching the backend a second, latex2mathml-specific path.
    """
    for child in list(elem):
        _rewrite_degree_circle(child)
    new_children: list[ET.Element] = []
    changed = False
    for child in list(elem):
        if _is_degree_circle_msup(child):
            degree = ET.Element("mo")
            degree.text = "°"
            new_children.append(child[0])  # the numeric base
            new_children.append(degree)
            changed = True
        else:
            new_children.append(child)
    if changed:
        for c in list(elem):
            elem.remove(c)
        for c in new_children:
            elem.append(c)


def _is_digit_run_mn(node: ET.Element) -> bool:
    """True for an ``<mn>`` whose text is a plain ASCII digit run."""
    text = (node.text or "").strip()
    return node.tag == "mn" and text.isdigit() and text.isascii()


def _tag_thousands_separators(elem: ET.Element) -> None:
    """In-place: tag a thousands-grouping comma with ``data-bk-tight`` so the
    backend drops its trailing space.

    ``1,000`` is one quantity, not a list, so its comma must read tight
    (``⠼⠁⠐⠚⠚⠚``) rather than spaced like a coordinate / list comma (``a, b``
    → ``⠰⠁⠐⠀⠰⠃``). latex2mathml splits ``1,000`` into
    ``<mn>1</mn><mo>,</mo><mn>000</mn>``; a comma counts as a thousands
    separator when a digit run precedes it and a bare three-digit run follows.
    A list / coordinate comma (``(x, y)``, ``(1, 2)``) is left untouched —
    its following group isn't a three-digit number — and keeps its space.
    """
    for child in elem:
        _tag_thousands_separators(child)
    kids = list(elem)
    for i in range(1, len(kids) - 1):
        node = kids[i]
        nxt = kids[i + 1]
        if (
            node.tag == "mo"
            and (node.text or "").strip() == ","
            and _is_digit_run_mn(kids[i - 1])
            and _is_digit_run_mn(nxt)
            and len((nxt.text or "").strip()) == 3
        ):
            node.set("data-bk-tight", "1")
