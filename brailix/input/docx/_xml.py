r"""Low-level XML helpers + OOXML namespace constants for the docx adapter.

This is the leaf of the docx subpackage DAG — it imports nothing from
its siblings (:mod:`._ole`, :mod:`._blocks`) and depends only on neutral
:mod:`brailix.core` helpers (:func:`brailix.core._xml.local_name` and the
:mod:`brailix.core.inline_math` island codec). It imports no frontend at
all: inline OMML is *deferred*, not converted here (see
:func:`_inline_math_as_text`).

Provides:

* OOXML namespace constants (``_W_NS`` / ``_M_NS`` / ``_R_NS`` / ``_O_NS``
  / ``_MC_NS``) and their Clark-notation prefixes (``_W_PREFIX`` /
  ``_M_PREFIX`` / ``_R_PREFIX``).
* Tag helpers (:func:`_local`, :func:`_first`, :func:`_first_local`).
* Serialisation helpers (:func:`_serialize`, :func:`_flatten_xml`).
* Inline-math wrapping (:func:`_wrap_inline_math` — the one place the
  ``$<math>...</math>$`` markers are produced, with inner-``$`` escaping;
  still used by the eagerly-decoded MTEF / script-cluster paths).
* Inline OMML deferral (:func:`_inline_math_as_text` — emits a
  source-tagged island for the frontend to convert).
"""

from __future__ import annotations

import re
from typing import Any

from brailix.core import inline_math
from brailix.core._xml import local_name

# python-docx exposes the underlying OOXML as :mod:`lxml.etree`
# elements; we use lxml.tostring directly so OMML serialisation
# round-trips byte-perfect, and keep a typing alias so the rest of
# the module reads naturally. The import lives behind the
# python-docx availability check in :func:`parse_docx`; this module
# may be imported without python-docx as long as no one calls
# :func:`parse_docx`. ``Element`` is therefore aliased to ``Any``.
Element = Any

# ---------------------------------------------------------------------------
# Namespace constants
# ---------------------------------------------------------------------------

_W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
_M_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
_R_NS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_O_NS = "urn:schemas-microsoft-com:office:office"
_MC_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
_W_PREFIX = "{" + _W_NS + "}"
_M_PREFIX = "{" + _M_NS + "}"
_R_PREFIX = "{" + _R_NS + "}"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _local(tag: str) -> str:
    """Strip XML namespace from a tag name — shared
    :func:`brailix.core._xml.local_name`."""
    return local_name(tag)


def _serialize(elem: Element) -> str:
    """Serialise ``elem`` to an XML string regardless of backing library.

    python-docx hands us lxml elements; the math frontend's OMML
    adapter parses with stdlib :mod:`xml.etree.ElementTree`. Both
    libraries' ``tostring`` accept slightly different signatures, so
    we centralise the dispatch — lxml when present, stdlib as fallback
    (which won't be hit because python-docx pulls lxml in transitively
    but is correct nonetheless).
    """
    try:
        from lxml import etree as lxml_etree

        if isinstance(elem, lxml_etree._Element):
            return lxml_etree.tostring(elem, encoding="unicode")
    except ImportError:
        pass
    import xml.etree.ElementTree as ET

    return ET.tostring(elem, encoding="unicode")


def _first(elem: Element, qname: str) -> Element | None:
    """First direct child with the given Clark-notation tag, or None."""
    for c in elem:
        if c.tag == qname:
            return c
    return None


def _first_local(elem: Element, local_name: str) -> Element | None:
    """First direct child whose local tag (ignoring namespace) matches."""
    for c in elem:
        if _local(c.tag) == local_name:
            return c
    return None


def _ns_attr(elem: Element, prefix: str, name: str) -> str | None:
    """Read an OOXML attribute, qualified form first then the bare fallback.

    Word normally writes attributes Clark-qualified (``{ns}name``), but some
    emitters drop the prefix and write a bare ``name`` (seen on ``w:val`` /
    ``r:id`` / ``w:fldCharType`` ...). Every attribute read in the docx
    adapter wants both, so centralising the two-step lookup here keeps a call
    site from silently forgetting the unprefixed fallback. An empty-string
    value is treated as absent — matching the old inline ``a or b`` form.
    """
    return elem.get(prefix + name) or elem.get(name)


def _inline_math_as_text(omath: Element) -> str:
    """Encode an inline ``<m:oMath>`` as a deferred ``omml`` inline-math
    island.

    The raw OMML is *not* converted here — it is serialised and wrapped
    as a source-tagged island (:func:`brailix.core.inline_math.wrap`), so
    the OMML→MathML conversion runs later in the frontend's math pass
    (``FrontendDriver.attach_math``), exactly as a display ``MathBlock`` with
    ``source="omml"`` defers. Keeping the conversion out of the input
    layer is what lets this module take no math-frontend dependency; see
    the docx ``__init__`` "Math handling" note and ARCHITECTURE §1.
    """
    return inline_math.wrap("omml", _serialize(omath))


# Every inline-math island :func:`_wrap_inline_math` produces opens with
# ``$<math`` and closes with ``</math>$`` — the ``$`` wrappers plus the
# flattened MathML, which always starts ``<math`` and ends ``</math>``.
# Consumers that *detect* such an island (``_blocks._is_inline_math``) or
# *scan* a paragraph for them (``__init__._mtef_recovery_needed``) key off
# these two markers; defining them next to the sole producer stops the
# open / close literals from drifting apart across modules.
_INLINE_MATH_OPEN = "$<math"
_INLINE_MATH_CLOSE = "</math>$"


def _wrap_inline_math(mathml: str) -> str:
    """Wrap flattened MathML in the ``$...$`` inline-math markers.

    Any literal ``$`` inside the MathML (a Word formula can carry one —
    e.g. currency text in an ``<mo>`` / ``<mtext>``) is escaped to the
    XML character reference ``&#36;`` first.  The frontend re-scans the
    paragraph text for ``$...$`` pairs, and a raw inner dollar would
    terminate the span early — corrupting the formula and leaking XML
    fragments into the prose.  The character reference parses back to
    the same ``$`` when the math frontend re-reads the span, so the
    formula content is unchanged.  Every producer of the ``$<math>``
    inline form must route through here.
    """
    return "$" + _flatten_xml(mathml).replace("$", "&#36;") + "$"


def _flatten_xml(xml: str) -> str:
    """Collapse all runs of whitespace (including inside text nodes) to a
    single space so the inline-math regex matches.

    The segmenter's inline-math scan (``_iter_inline_math_spans``) rejects
    newlines inside ``$...$``; collapsing them lets longer formulas live on a
    single text line.  This
    also folds whitespace inside ``<mtext>`` — only reachable via OMML's
    ``itertext()`` fallback for unknown constructs, and braille ignores
    such whitespace, so the MathML parse is unaffected in practice.
    """
    return re.sub(r"\s+", " ", xml).strip()
