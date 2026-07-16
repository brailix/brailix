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
# ...but the raw-byte scan only sees ``<!ENTITY`` when the source is a single-
# byte encoding (UTF-8 / ISO-8859-1 / US-ASCII — the ASCII bytes appear
# verbatim). UTF-16 encodes each ASCII character of the keyword as a two-byte
# code unit — the ASCII byte beside a NUL, low-byte-first (UTF-16LE) or high-
# byte-first (UTF-16BE) — so the interleaved NULs break the contiguous ASCII run
# and the scan above misses it. expat still auto-detects UTF-16 from the BOM /
# declaration and expands the entity, so a UTF-16 document would smuggle a bomb
# straight past the filter (this is a real, verified bypass, not a theoretical
# one). Match the two interleaved forms directly. UTF-32 needs no pattern: expat
# cannot decode it at all, so such a document fails as not-well-formed before any
# entity can expand.
_ENTITY_DECL_RE_UTF16LE = re.compile(rb"<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y\x00")
_ENTITY_DECL_RE_UTF16BE = re.compile(rb"\x00<\x00!\x00E\x00N\x00T\x00I\x00T\x00Y")


def _declares_entity_bytes(data: bytes | bytearray) -> bool:
    """Whether ``data`` carries an XML ``<!ENTITY`` declaration in any encoding
    expat will decode and then expand: a single-byte encoding (the plain ASCII
    bytes) or UTF-16 LE / BE (the same bytes interleaved with NULs). See the
    module-level patterns for why UTF-16 needs its own forms and UTF-32 does
    not."""
    return (
        _ENTITY_DECL_RE_BYTES.search(data) is not None
        or _ENTITY_DECL_RE_UTF16LE.search(data) is not None
        or _ENTITY_DECL_RE_UTF16BE.search(data) is not None
    )


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
    at worst soft-fail an exotic document — never silently mistranslate. The
    byte scan covers every encoding expat will actually decode: single-byte
    encodings carry the keyword as literal ASCII, and UTF-16 (LE / BE) carries
    it in the NUL-interleaved forms the scan also checks — so the encoding
    trick that would otherwise slip a declaration past a plain ASCII scan is
    closed.
    """
    if isinstance(text, (bytes, bytearray)):
        has_entity_decl = _declares_entity_bytes(text)
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
    """Drop any ``{namespace}local`` Clark-notation prefix from every
    element tag, leaving the bare local name.

    Iterative (explicit stack) rather than recursive so an adversarially
    deep tree — thousands of nested elements in an untrusted MathML /
    MusicXML payload or a ``.blx`` round-trip — can't overflow Python's
    recursion limit here: the IR-deserialization and MathML-normalizer
    boundaries both rely on this strip being depth-safe.

    A normalized MathML / MusicXML tree only ever carries its own
    namespace, so the generic strip is equivalent to a prefix-specific
    one for valid input while also tidying any stray foreign-namespaced
    tag a vendor might have left behind.
    """
    stack: list[ET.Element] = [elem]
    while stack:
        node = stack.pop()
        if node.tag.startswith("{"):
            close = node.tag.find("}")
            if close != -1:
                node.tag = node.tag[close + 1:]
        stack.extend(node)


def strip_whitespace_text(elem: ET.Element) -> None:
    """Null out pure-whitespace ``text`` / ``tail`` strings, which
    otherwise confuse children iteration in the IR builders.

    Iterative (explicit stack) for the same depth-safety as
    :func:`strip_namespace`.
    """
    stack: list[ET.Element] = [elem]
    while stack:
        node = stack.pop()
        if node.text is not None and not node.text.strip():
            node.text = None
        for child in node:
            if child.tail is not None and not child.tail.strip():
                child.tail = None
            stack.append(child)


def tree_depth_exceeds(elem: ET.Element, limit: int) -> bool:
    """Whether ``elem``'s element-nesting depth exceeds ``limit`` levels
    (``elem`` itself is depth 1).

    Iterative (explicit stack carrying each node's depth) and short-circuits
    as soon as a node past ``limit`` is reached, so the probe is itself
    depth-safe. Used to guard the recursive-descent boundaries that aren't
    easily made iterative (the math backend's tag dispatch, the MathML
    normalizer's passes): a tree past the cap degrades to a soft failure
    instead of overflowing the stack and crashing the pipeline.
    """
    stack: list[tuple[ET.Element, int]] = [(elem, 1)]
    while stack:
        node, depth = stack.pop()
        if depth > limit:
            return True
        for child in node:
            stack.append((child, depth + 1))
    return False


def local_name(tag: str) -> str:
    """Bare local name of an ElementTree tag, dropping any
    ``{namespace}`` Clark-notation prefix. The single-tag counterpart to
    :func:`strip_namespace` — used where a caller looks up one tag's name
    without rewriting the whole tree (the OMML / docx converters)."""
    if tag.startswith("{"):
        return tag.partition("}")[2]
    return tag
