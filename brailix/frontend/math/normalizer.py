"""MathML normalization.

After a source adapter produces a MathML string, the normalizer cleans
it up so downstream consumers (the math backend) have fewer special
cases to handle:

* strip leading / trailing whitespace text nodes inside element runs;
* collapse single-child ``<mrow>`` wrappers (``<mrow><mi>x</mi></mrow>``
  → ``<mi>x</mi>``);
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

from brailix.frontend._xml import strip_namespace, strip_whitespace_text
from brailix.frontend.math.utils import merror_wrap


def normalize(mathml: str) -> ET.Element:
    """Parse a MathML string and return a normalized :class:`Element`
    tree with the MathML namespace stripped.

    Soft-failure contract: invalid XML is wrapped into a ``<merror>``
    document via :func:`merror_wrap` so the caller always gets a tree
    rooted at ``<math>``.
    """
    try:
        root = ET.fromstring(mathml)
    except ET.ParseError as e:
        root = ET.fromstring(merror_wrap(mathml, reason=f"parse error: {e}"))
    strip_namespace(root)
    _collapse_singleton_mrows(root)
    strip_whitespace_text(root)
    return root


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------


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
            new_children.append(grand)
        else:
            new_children.append(child)
    # Replace the element's children, preserving order.
    if new_children != list(elem):
        for c in list(elem):
            elem.remove(c)
        for c in new_children:
            elem.append(c)
