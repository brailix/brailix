"""Shared ElementTree helpers — generic, format-independent.

These tidy a parsed :class:`~xml.etree.ElementTree.Element` tree at a
layer boundary: drop XML namespaces so a backend can match bare local
tags, null out pure-whitespace ``text`` / ``tail`` nodes that confuse
element iteration, and scrub characters illegal in XML 1.0 before a
(possibly malformed) vendor string is echoed back into a soft-failure
document. They depend only on the standard library, so they live in
:mod:`brailix.core` — the frontend normalizers (MathML / MusicXML) and
the input layer's docx converters both use them without either layer
depending on the other.
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


# An XML entity *declaration* — the billion-laughs / quadratic-blowup vector.
# Nested ``<!ENTITY>`` definitions inside a DOCTYPE internal subset let a tiny
# document expand to gigabytes at parse time and OOM the process. Expansion is
# impossible without a declaration: the 5 predefined entities (lt/gt/amp/apos/
# quot) and numeric refs are single characters and never go through one. An
# *external* ``<!DOCTYPE ... PUBLIC ...>`` (as real MusicXML files carry) has no
# ``<!ENTITY`` and is left to parse — expat does not fetch external DTDs by
# default, so it can't smuggle a bomb either.
_ENTITY_DECL_RE = re.compile(r"<!ENTITY")
_ENTITY_DECL_RE_BYTES = re.compile(rb"<!ENTITY")


def safe_fromstring(text: str | bytes) -> ET.Element:
    """Parse untrusted XML, refusing entity-declaration expansion bombs.

    A drop-in for :func:`xml.etree.ElementTree.fromstring` at every
    boundary that parses externally-supplied XML (MathML / MusicXML
    payloads, ``.mxl`` container, ``.blx`` round-trip). Raises
    :class:`~xml.etree.ElementTree.ParseError` if the source declares any
    ``<!ENTITY>`` — so a malformed/malicious file soft-fails the same way
    any other parse error does, rather than exhausting memory.

    Scans the raw source rather than hooking expat's entity handler:
    ElementTree does not expose the underlying expat parser portably, and
    a literal ``<!ENTITY`` never appears in legitimate MathML / MusicXML
    content (only inside a DOCTYPE internal subset). A false reject would
    at worst soft-fail an exotic document — never silently mistranslate.
    """
    if isinstance(text, (bytes, bytearray)):
        has_entity_decl = _ENTITY_DECL_RE_BYTES.search(text) is not None
    else:
        has_entity_decl = _ENTITY_DECL_RE.search(text) is not None
    if has_entity_decl:
        raise ET.ParseError(
            "XML entity declarations are not allowed "
            "(possible billion-laughs expansion bomb)"
        )
    return ET.fromstring(text)


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
