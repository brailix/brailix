"""Shared ElementTree helpers — generic, format-independent.

These handle the parts of reading XML that come before any format knows it is
being read: turn a byte payload into text the way XML's own encoding rules say
to (:func:`decode_xml_bytes`), walk a document's prologue to find where its
root element starts (:func:`strip_xml_prolog` / :func:`xml_root_element`), and
tidy a parsed :class:`~xml.etree.ElementTree.Element` tree at a layer boundary
— drop XML namespaces so a backend can match bare local tags, null out
pure-whitespace ``text`` / ``tail`` nodes that confuse element iteration, and
scrub characters illegal in XML 1.0 before a (possibly malformed) vendor
string is echoed back into a soft-failure document.

Every one of those is a fact about *XML*, not about MathML, MusicXML, SVG or
OOXML, and each was implemented more than once before it landed here — which
is how the byte decode came to reject legal UTF-16 in three adapters and the
prologue scan came to have a correct implementation in the input layer and a
broken one here. What stays with each caller is its **policy**: which soft
failure to build, what to put in the error, whether to degrade at all. Same
split as :data:`~brailix.core.errors.UNREADABLE_ZIP_MEMBER_ERRORS`, which is
one shared fact about :mod:`zipfile` under two different reactions to it.

They depend only on the standard library, so they live in :mod:`brailix.core`
— the frontend normalizers (MathML / MusicXML / SVG) and the input layer's
docx converters and format sniffing all use them without either layer
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


class XmlDecodeError(ValueError):
    """An XML byte payload that cannot be decoded under XML's own encoding
    rules — see :func:`decode_xml_bytes`.

    A :class:`ValueError` rather than an
    :class:`~xml.etree.ElementTree.ParseError` because nothing has been parsed
    yet: the bytes never became text at all, which is a different diagnosis
    from "the text is not well-formed" and deserves to read differently in
    whatever soft failure the caller builds from it.
    """


# XML's own encoding autodetection (XML 1.0 §4.3.3 + Appendix F). A byte
# stream announces its encoding in one of three ways, checked in this order: a
# byte order mark; the byte *pattern* of the ``<?xml`` declaration, which
# reveals UTF-16 / UTF-32 even with no mark; and that declaration's own
# ``encoding`` pseudo-attribute. UTF-8 is the default when none of them speaks.
#
# What this replaces is a plain ``data.decode("utf-8")`` in each pass-through
# adapter, which refuses a perfectly legal UTF-16 document. The input layer's
# own file reader already accepts one (``InputLimits.read_bounded_text``), so
# the same score parsed from disk and soft-failed when the identical bytes
# were handed to ``to_musicxml`` — and MathML and SVG had the same split.

# Longest mark first: the UTF-32LE mark *begins with* the UTF-16LE mark, so
# testing UTF-16 first decodes a UTF-32 document into garbage. The ``utf-32`` /
# ``utf-16`` / ``utf-8-sig`` codecs are the BOM-consuming ones — the mark is
# metadata, and leaving a U+FEFF at the head of the text puts a stray character
# before the root element.
_XML_BOMS: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\xfe\xff", "utf-32"),
    (b"\xff\xfe\x00\x00", "utf-32"),
    (b"\xef\xbb\xbf", "utf-8-sig"),
    (b"\xfe\xff", "utf-16"),
    (b"\xff\xfe", "utf-16"),
)

# No mark: the first four bytes of a ``<?xml`` declaration in each multi-byte
# family, which is the only thing that can identify one. An ASCII-compatible
# stream (UTF-8, ISO-8859-x, Shift_JIS, ...) shows ``3C 3F 78 6D`` and is
# decided by the declaration's ``encoding`` instead. A UTF-16 document with
# neither a mark nor a declaration is not well-formed XML — the spec requires
# one of the two — so there is nothing left to detect and UTF-8 is the answer.
_XML_DECL_SIGNATURES: tuple[tuple[bytes, str], ...] = (
    (b"\x00\x00\x00\x3c", "utf-32-be"),
    (b"\x3c\x00\x00\x00", "utf-32-le"),
    (b"\x00\x3c\x00\x3f", "utf-16-be"),
    (b"\x3c\x00\x3f\x00", "utf-16-le"),
)

# The ``encoding`` pseudo-attribute, read off the raw bytes of an
# ASCII-compatible stream. Searched only within the declaration itself: a scan
# that ran past ``?>`` would happily read an ``encoding=`` attribute out of the
# document body and decode the whole file by it.
_XML_DECL_ENCODING = re.compile(
    rb"""\bencoding\s*=\s*["']([A-Za-z][A-Za-z0-9._-]*)["']"""
)
_XML_DECL_SCAN_BYTES = 1024


def _decode_as(raw: bytes, codec: str, how: str) -> str:
    """``raw.decode(codec)``, reporting a failure as an :class:`XmlDecodeError`
    that says which rule picked the codec (``how``)."""
    try:
        return raw.decode(codec)
    except (UnicodeDecodeError, LookupError) as e:
        raise XmlDecodeError(f"not decodable as {how}: {e}") from e


def _declared_encoding(raw: bytes) -> str | None:
    """The encoding named by ``raw``'s XML declaration, or ``None``."""
    if not raw.startswith(b"<?xml"):
        return None
    end = raw.find(b"?>", 0, _XML_DECL_SCAN_BYTES)
    if end < 0:
        return None
    match = _XML_DECL_ENCODING.search(raw, 0, end)
    return match.group(1).decode("ascii") if match else None


def decode_xml_bytes(data: bytes | bytearray) -> str:
    """Decode an XML byte payload to text the way an XML processor would.

    The :class:`~brailix.core.protocols.MathSourceAdapter` /
    ``MusicSourceAdapter`` / ``GraphicSourceAdapter` contracts all take
    ``str | bytes``, and *bytes of XML* are self-describing: a BOM, the byte
    pattern of the ``<?xml`` declaration, or the ``encoding`` that declaration
    names says what they are, and only in their absence is UTF-8 the answer.
    Deciding it here means one rule for all three normalized intermediate
    formats instead of three near-copies of ``decode("utf-8")``.

    Returns the decoded text with any byte order mark consumed. Raises
    :class:`XmlDecodeError` when the bytes do not decode under the rule that
    was selected — including an ``encoding`` naming a codec Python does not
    have. Never raises anything else, so a caller that soft-fails can catch
    exactly one thing.

    Text handed in as ``str`` needs none of this and does not come here; each
    caller checks that first, because what it puts in its own error message
    (the ``repr`` of the undecodable bytes) is the payload it still holds.
    """
    raw = bytes(data)
    for bom, codec in _XML_BOMS:
        if raw.startswith(bom):
            return _decode_as(raw, codec, f"{codec} (byte order mark)")
    for signature, codec in _XML_DECL_SIGNATURES:
        if raw.startswith(signature):
            return _decode_as(raw, codec, f"{codec} (XML declaration bytes)")
    declared = _declared_encoding(raw)
    if declared is not None:
        return _decode_as(raw, declared, f"declared encoding {declared!r}")
    return _decode_as(raw, "utf-8", "UTF-8 (no BOM, no encoding declaration)")


def _skip_quoted(text: str, i: int) -> int:
    """Index just past the string literal opening at ``text[i]`` (a quote),
    or the end of the text when it is never closed."""
    end = text.find(text[i], i + 1)
    return len(text) if end < 0 else end + 1


def _skip_internal_subset(text: str, i: int) -> int:
    """Index just past the ``]`` closing the internal DTD subset that opens at
    ``text[i] == "["``.

    Not ``text.find("]")``: a subset is markup, and a ``]`` inside one of its
    declarations closes nothing. It can sit in an attribute default
    (``<!ATTLIST part id CDATA "a]b">``), in an entity value, or in a comment
    — and taking the first one for the end of the subset then leaves the scan
    inside the DOCTYPE, where the next quoted ``>`` reads as the end of the
    declaration and the root element is never seen. So quotes, comments and
    processing instructions are skipped whole, and bracket depth is counted
    (a ``<![INCLUDE[ … ]]>`` conditional section nests one level).
    """
    n = len(text)
    depth = 0
    while i < n:
        ch = text[i]
        if ch in "\"'":
            i = _skip_quoted(text, i)
        elif text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            i = n if end < 0 else end + 3
        elif text.startswith("<?", i):
            end = text.find("?>", i + 2)
            i = n if end < 0 else end + 2
        elif ch == "[":
            depth += 1
            i += 1
        elif ch == "]":
            depth -= 1
            i += 1
            if depth == 0:
                return i
        else:
            i += 1
    return n


def _root_element_start(text: str) -> int:
    """Index of the ``<`` opening ``text``'s first start element, or ``-1``.

    A deliberately small hand-written prologue scanner rather than a real
    parser. Everything a document may put before its root element is
    skippable — whitespace, the ``<?xml?>`` declaration and other processing
    instructions, comments, and a ``<!DOCTYPE …>`` whose internal ``[…]``
    subset may itself contain ``>`` or ``]`` inside quotes and comments
    (:func:`_skip_internal_subset`) — and once the first ``<name`` is reached
    there is nothing left to decide. Feeding the document to :mod:`xml.etree`
    to find out instead would expand entities declared in that internal subset
    before this function ever returned, which is a parser to point at
    untrusted input only deliberately; this reads the head and stops.

    ``-1`` for anything with no start element to find — an empty string,
    character data before the first ``<``, an unterminated comment or DOCTYPE.
    Both callers treat that as "leave it alone", which keeps a malformed
    document intact for whoever diagnoses it.
    """
    i, n = 0, len(text)
    while i < n:
        while i < n and text[i].isspace():
            i += 1
        if i >= n or text[i] != "<":
            return -1
        if text.startswith("<!--", i):
            end = text.find("-->", i + 4)
            if end < 0:
                return -1
            i = end + 3
        elif text.startswith("<?", i):
            end = text.find("?>", i + 2)
            if end < 0:
                return -1
            i = end + 2
        elif text.startswith("<!", i):
            # DOCTYPE (or any other markup declaration): find its closing
            # ``>``, skipping over quoted strings — a public identifier can
            # contain one — and over a whole internal subset if present.
            i += 2
            while i < n and text[i] != ">":
                if text[i] in "\"'":
                    i = _skip_quoted(text, i)
                elif text[i] == "[":
                    i = _skip_internal_subset(text, i)
                else:
                    i += 1
            i += 1
        else:
            return i
    return -1


def strip_xml_prolog(text: str) -> str:
    """Return ``text`` from its root element on, dropping the prologue.

    Its whole job is to *locate the root element* (:func:`_root_element_start`)
    — not to guess where a DOCTYPE ends with a rule of its own. The rule it
    used to have counted ``[`` / ``]`` and nothing else, so a legal
    ``<!ATTLIST part id CDATA "a]b>c">`` inside an internal subset ended the
    scan at the quoted ``]`` and the caller was handed the fragment ``c">]>…``
    — a document ElementTree parses fine, soft-failing as a parse error.

    What the strip is *for*, given :func:`safe_fromstring` parses a DOCTYPE
    perfectly well (external identifier and all — expat does not fetch
    external DTDs, so there is nothing there to refuse): it is the
    ``<!ENTITY`` refusal that needs it. A DOCTYPE with an internal subset is
    turned away wholesale by that guard, and real exporters ship one —
    Illustrator writes ``[<!ENTITY ns_extend "http://ns.adobe.com/…">]`` into
    every SVG it saves. Dropping the prologue drops the declarations with it,
    so such a document parses; an entity *reference* left behind in the body
    is then simply undefined and fails as an ordinary parse error, which is
    the one outcome the guard exists to guarantee (nothing expands).

    That is also why the scan skips comments rather than stopping at the first
    one: Illustrator's ``<!-- Generator: Adobe Illustrator … -->`` sits between
    the declaration and the DOCTYPE, and the old "declaration then DOCTYPE, in
    that order or not at all" walk stopped dead at it, leaving the ``<!ENTITY``
    in place for the guard to reject. Every Illustrator SVG soft-failed.
    """
    start = _root_element_start(text)
    return text if start < 0 else text[start:]


def xml_root_element(text: str) -> str:
    """The name of ``text``'s first start element, or ``""``.

    Reads the name at :func:`_root_element_start`, so "where does the prologue
    end" is answered once for both this and :func:`strip_xml_prolog`. Used to
    sniff what an ambiguous container actually holds (a generic ``.xml`` that
    may or may not be a score) without parsing it.

    A namespace prefix is dropped (``<mx:score-partwise>`` reports
    ``score-partwise``).
    """
    start = _root_element_start(text)
    if start < 0:
        return ""
    n = len(text)
    j = start + 1
    while j < n and not (text[j].isspace() or text[j] in "/>"):
        j += 1
    return text[start + 1:j].rsplit(":", 1)[-1]


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
