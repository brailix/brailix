"""Footnote extraction for the docx adapter.

Word stores a document's footnotes in a ``word/footnotes.xml`` package
part: one ``<w:footnote>`` element per note, keyed by ``w:id``, holding
the note's paragraphs. The body references one with
``<w:r><w:footnoteReference w:id="N"/></w:r>`` at the point the marker
sits. python-docx does not model any of this, so this module pre-indexes
the part once per document (mirroring :func:`.._ole._build_ole_blob_map`
and :func:`.._media._build_image_blob_map`) and hands the walker a plain
``{id: element}`` map, keeping python-docx / lxml specifics out of
:mod:`.._blocks`.

The separator / continuationSeparator footnotes Word writes first
(``w:id="-1"`` / ``"0"``) are page-layout machinery, not content — they
carry no text and are never referenced by a ``w:footnoteReference`` —
so they are left out of the map entirely.

A document with no footnotes part (the common case: the relationship
is absent) yields an empty map and the parse is byte-for-byte the
parse it always was. A footnotes part that does not parse is degraded
to that same empty map rather than failing the whole document — the
same "a broken rel must not crash an otherwise-readable document" rule
:mod:`.._rels` applies — at the cost of the notes, which a body
reference then finds nothing for.

DAG position: a leaf like :mod:`._media` and :mod:`._ole` — the
python-docx import it needs stays lazy, so this module imports nothing
beyond :mod:`brailix.input.docx._xml` / :mod:`brailix.input.docx._rels`
and the stdlib.
"""

from __future__ import annotations

from brailix.input.docx._rels import iter_rel_parts
from brailix.input.docx._xml import _W_PREFIX, Element, _local, _ns_attr

# ``w:type`` values that mark a footnote as layout machinery, not content.
_SEPARATOR_FOOTNOTE_TYPES = frozenset({"separator", "continuationSeparator"})


def _build_footnote_map(document: object) -> dict[str, Element]:
    """Index every content footnote: ``w:id`` → ``<w:footnote>`` element.

    Built once per document, before the body walk, like the caller's other
    blob maps. Empty when the package has no footnotes part (the common
    case) or the part is unreadable.
    """
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
        from lxml import etree as lxml_etree
    except ImportError:  # pragma: no cover — defensive
        return {}
    out: dict[str, Element] = {}
    for _rid, part in iter_rel_parts(document, RT.FOOTNOTES):
        try:
            root = lxml_etree.fromstring(part.blob)
        except lxml_etree.XMLSyntaxError:
            continue  # corrupt part → no footnotes, but the body still parses
        if _local(root.tag) != "footnotes":
            continue
        for fn in root:
            if _local(fn.tag) != "footnote":
                continue
            ftype = _ns_attr(fn, _W_PREFIX, "type")
            if ftype in _SEPARATOR_FOOTNOTE_TYPES:
                continue
            fid = _ns_attr(fn, _W_PREFIX, "id")
            if fid:
                out[fid] = fn
    return out
