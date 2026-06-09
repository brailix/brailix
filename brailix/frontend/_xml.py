"""Shared ElementTree helpers for the frontend normalizers.

The MathML and MusicXML normalizers both parse a vendor string into an
:class:`~xml.etree.ElementTree.Element` tree and then (a) drop XML
namespaces so the backend can match bare local tags and (b) null out
pure-whitespace ``text`` / ``tail`` nodes that confuse element
iteration. Both steps are format-independent, so they live here once
rather than in two near-identical copies.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET

# Code points illegal in XML 1.0 even after entity-escaping: the C0
# controls except tab / newline / carriage-return, plus the lone
# surrogates. ``escape`` / ``quoteattr`` only handle ``& < > " '``,
# so a vendor-malformed source string echoed back into a soft-failure
# ``<merror>`` / ``<music-error>`` document would otherwise make the
# downstream ``ET.fromstring`` re-parse raise — breaking the
# "normalizer never raises" contract. See :func:`strip_xml_invalid_chars`.
_XML_INVALID_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff]"
)


def strip_xml_invalid_chars(text: str) -> str:
    """Drop characters illegal in XML 1.0 from ``text``.

    Used before embedding a (possibly malformed) vendor string into a
    soft-failure document, so the result stays well-formed and can be
    re-parsed without raising. Escaping alone is not enough — control
    characters are invalid in XML *content* regardless of escaping.
    """
    return _XML_INVALID_CHARS.sub("", text)


def strip_namespace(elem: ET.Element) -> None:
    """Recursively drop any ``{namespace}local`` Clark-notation prefix
    from every element tag, leaving the bare local name.

    A normalized MathML / MusicXML tree only ever carries its own
    namespace, so the generic strip is equivalent to a prefix-specific
    one for valid input while also tidying any stray foreign-namespaced
    tag a vendor might have left behind.
    """
    if elem.tag.startswith("{"):
        close = elem.tag.find("}")
        if close != -1:
            elem.tag = elem.tag[close + 1:]
    for child in list(elem):
        strip_namespace(child)


def strip_whitespace_text(elem: ET.Element) -> None:
    """Recursively null out pure-whitespace ``text`` / ``tail`` strings,
    which otherwise confuse children iteration in the IR builders."""
    if elem.text is not None and not elem.text.strip():
        elem.text = None
    for child in list(elem):
        if child.tail is not None and not child.tail.strip():
            child.tail = None
        strip_whitespace_text(child)


def local_name(tag: str) -> str:
    """Bare local name of an ElementTree tag, dropping any
    ``{namespace}`` Clark-notation prefix. The single-tag counterpart to
    :func:`strip_namespace` — used where a caller looks up one tag's name
    without rewriting the whole tree (the OMML / docx converters)."""
    if tag.startswith("{"):
        return tag.partition("}")[2]
    return tag
