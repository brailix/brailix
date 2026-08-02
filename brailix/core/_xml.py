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
# controls except tab / newline / carriage-return, the lone surrogates, and
# U+FFFE / U+FFFF — everything the ``Char`` production leaves out
# (``#x9 | #xA | #xD | [#x20-#xD7FF] | [#xE000-#xFFFD] |
# [#x10000-#x10FFFF]``; the noncharacters above U+FFFF and the U+FDD0 block
# are discouraged but legal, and expat accepts them). ``escape`` /
# ``quoteattr`` only handle ``& < > " '``, so a vendor-malformed source
# string echoed back into a soft-failure ``<merror>`` / ``<music-error>``
# document would otherwise make the downstream ``ET.fromstring`` re-parse
# raise — breaking the "normalizer never raises" contract, which is exactly
# what a stray U+FFFE did. See :func:`strip_xml_invalid_chars`.
_XML_INVALID_CHARS = re.compile(
    "[\x00-\x08\x0b\x0c\x0e-\x1f\ud800-\udfff\ufffe\uffff]"
)


# An XML entity *declaration* — the billion-laughs / quadratic-blowup vector.
# Nested ``<!ENTITY>`` definitions inside a DOCTYPE internal subset let a tiny
# document expand to gigabytes at parse time and OOM the process. Expansion is
# impossible without a declaration: the 5 predefined entities (lt/gt/amp/apos/
# quot) and numeric refs are single characters and never go through one. An
# *external* ``<!DOCTYPE ... PUBLIC ...>`` (as real MusicXML files carry) has no
# ``<!ENTITY`` and is left to parse — expat does not fetch external DTDs by
# default, so it can't smuggle a bomb either.
#
# Note the LE and BE patterns below overlap by a one-byte phase shift on real
# input: ``<!ENTITY`` always sits between NUL-padded ASCII (a ``[`` or space),
# so the BE byte stream contains the LE pattern one byte in, and vice versa —
# in practice EITHER pattern alone flags BOTH encodings (mutation testing
# surfaced this). Both are kept deliberately: the overlap breaks exactly when
# the keyword has no NUL-adjacent neighbour (stream boundary), and two anchored
# spellings are cheaper than proving that edge unreachable forever.
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


def strip_xml_prolog(text: str) -> str:
    """Remove a leading ``<?xml ...?>`` declaration and an optional
    ``<!DOCTYPE ...>``.

    :func:`safe_fromstring` accepts the XML declaration but trips on a
    DOCTYPE that references an external DTD — which the exporters real
    documents come from still emit (older Finale / Sibelius for MusicXML,
    Inkscape / Illustrator for SVG). Both source families need exactly this,
    which is why it sits here beside :func:`safe_fromstring` rather than
    being written out once per format: it is XML plumbing, with nothing in
    it that knows which format it is cleaning.

    The DOCTYPE scan balances ``[`` / ``]`` so an internal subset (which may
    contain ``>`` inside its entity declarations) doesn't end the scan early.
    """
    out = text
    if out.startswith("<?xml"):
        end = out.find("?>")
        if end != -1:
            out = out[end + 2:].lstrip()
    if out.startswith("<!DOCTYPE"):
        depth = 0
        for i, ch in enumerate(out):
            if ch == "[":
                depth += 1
            elif ch == "]":
                depth = max(0, depth - 1)
            elif ch == ">" and depth == 0:
                out = out[i + 1:].lstrip()
                break
    return out


def strip_xml_invalid_chars(text: str) -> str:
    """Drop characters illegal in XML 1.0 from ``text``.

    Used before embedding a (possibly malformed) vendor string into a
    soft-failure document, so the result stays well-formed and can be
    re-parsed without raising. Escaping alone is not enough — control
    characters are invalid in XML *content* regardless of escaping.
    """
    return _XML_INVALID_CHARS.sub("", text)


# Not every node an ElementTree walk visits is an *element*. A comment and a
# processing instruction are also ``Element`` instances and also children of
# their parent, but their ``tag`` is the factory **function** that made them
# (``ET.Comment`` / ``ET.ProcessingInstruction``) rather than a tag name, and
# their ``text`` is the comment / instruction body rather than markup content.
# So the two walkers below — which rewrite tags and null out whitespace text —
# must recognise them and pass them by: ``node.tag.startswith("{")`` on a
# comment raised ``AttributeError: 'function' object has no attribute
# 'startswith'``, and nulling a whitespace-only comment's ``text`` made the
# tree unserialisable (``ET.tostring`` wrote a literal ``<!--None-->``).
#
# ``ET.fromstring`` drops both node types, so the string path never produced
# one — but a **pre-parsed** ``ET.Element`` is an equally supported way to hand
# a tree to :class:`~brailix.ir.inline.MathInline` / ``MusicInline`` /
# ``GraphicInline``, and that path strips namespaces on whatever the caller
# built. An ``AttributeError`` there is also the one class of exception the
# soft-failure boundaries deliberately re-raise (``PROGRAMMING_ERRORS``), so it
# crashed the compile rather than degrading.
#
# Typeshed declares ``Element.tag`` as ``str``, so the ``isinstance`` guards
# below read as redundant to a type checker; they are the runtime truth.


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

    Comment and processing-instruction nodes are walked past untouched —
    see the note above on why a tag is not always a string.
    """
    stack: list[ET.Element] = [elem]
    while stack:
        node = stack.pop()
        tag = node.tag
        if isinstance(tag, str) and tag.startswith("{"):
            close = tag.find("}")
            if close != -1:
                node.tag = tag[close + 1:]
        stack.extend(node)


def strip_whitespace_text(elem: ET.Element) -> None:
    """Null out pure-whitespace ``text`` / ``tail`` strings, which
    otherwise confuse children iteration in the IR builders.

    Iterative (explicit stack) for the same depth-safety as
    :func:`strip_namespace`.

    A comment's / processing instruction's ``text`` is its *body*, not
    markup content, so it is left alone (see the note above); the ``tail``
    beside one is ordinary inter-element whitespace and is nulled like any
    other.
    """
    stack: list[ET.Element] = [elem]
    while stack:
        node = stack.pop()
        if (
            isinstance(node.tag, str)
            and node.text is not None
            and not node.text.strip()
        ):
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
