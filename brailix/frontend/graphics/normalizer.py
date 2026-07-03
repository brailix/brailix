"""SVG normalization.

After a source adapter produces an SVG string, the normalizer parses it
into an :class:`xml.etree.ElementTree.Element` tree and tidies it so the
tactile backend can match on bare local tags:

* drop the SVG XML namespace from every element tag (so the backend
  matches ``rect`` / ``circle`` instead of Clark-notation
  ``{http://www.w3.org/2000/svg}rect``);
* null out pure-whitespace ``text`` / ``tail`` nodes that confuse child
  iteration.

The normalizer never raises — malformed input (or a tree nested past the
depth cap) degrades to an empty ``<svg>`` carrying a ``data-bk-error``
attribute, so the backend produces a blank raster and the pipeline keeps
running. This mirrors the math / music normalizers' soft-failure
contract.

Attribute preservation: the normalizer rewrites ``elem.tag`` but never
touches ``elem.attrib``, so geometry attributes (``x``, ``cx``,
``points``, ...) and any ``data-bk-*`` provenance survive untouched.

Deeper SVG handling (resolving ``transform`` matrices, flattening
``<use>`` / ``<defs>``, CSS ``<style>``) is intentionally out of scope
for this first increment — see ``ARCHITECTURE.md``
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.core._xml import (
    safe_fromstring,
    strip_namespace,
    strip_whitespace_text,
    tree_depth_exceeds,
)

# An SVG tree deeper than this would overflow the recursive backend walk
# downstream. Real drawings nest well under this; a corrupt / adversarial
# tree past the cap degrades to a soft failure here instead of risking a
# stack overflow. The depth probe is iterative, so the guard is itself
# depth-safe.
_MAX_SVG_DEPTH = 200


def _error_svg(reason: str) -> ET.Element:
    """An empty ``<svg>`` carrying a soft-failure marker (rasterizes to a
    blank page)."""
    root = ET.Element("svg")
    root.set("data-bk-error", reason)
    return root


def _assign_gids(root: ET.Element) -> None:
    """Tag every element with a stable pre-order id ``data-bk-gid``.

    The backend's raster provenance keys pixels by this id and the editor's
    SVG object tree reads the same attribute, so the two agree on "which
    element is which" for cross-pane highlight (ARCHITECTURE.md). Read off the element, so iteration order between producer / consumer
    needn't match. Skipped for the rare non-``str`` tag (comments / PIs)."""
    for i, elem in enumerate(root.iter()):
        if isinstance(elem.tag, str):
            elem.set("data-bk-gid", str(i))


def normalize(svg: str) -> ET.Element:
    """Parse an SVG string and return a normalized :class:`Element` tree
    with the SVG namespace stripped.

    Soft-failure contract: invalid XML or an over-deep tree yields an
    empty ``<svg data-bk-error="...">`` so the caller always gets a tree
    rooted at ``<svg>``.
    """
    try:
        root = safe_fromstring(svg)
    except ET.ParseError as e:
        return _error_svg(f"parse error: {e}")
    if tree_depth_exceeds(root, _MAX_SVG_DEPTH):
        return _error_svg(f"drawing nested deeper than {_MAX_SVG_DEPTH} levels")
    strip_namespace(root)
    strip_whitespace_text(root)
    _assign_gids(root)
    return root
