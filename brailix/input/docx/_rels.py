"""Shared python-docx relationship walk for the docx adapter's blob maps.

Both :func:`.._ole._build_ole_blob_map` and
:func:`.._media._build_image_blob_map` index a document's relationships
by rId, and the walk around their differences — iterate rels, keep one
reltype, skip external links, fetch the local part — was two copies that
had already drifted: one caught only ``AttributeError`` from
``target_part`` while its own comment documented that python-docx raises
``ValueError`` there for a problem relationship, so a corrupt rel crashed
the whole OLE parse while the media parse sailed past it. One walk, one
exception set.

DAG position: a leaf like ``_xml`` — the python-docx import needed by
callers stays lazy in *them*, so this module imports nothing beyond the
stdlib.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import Any


def iter_rel_parts(
    document: Any, reltype: str
) -> Iterator[tuple[str, Any]]:
    """Yield ``(rId, part)`` for every non-external relationship of
    ``reltype`` (pass ``docx.opc.constants.RELATIONSHIP_TYPE.OLE_OBJECT``
    / ``.IMAGE`` — the caller owns the lazy import of that constant).

    External (linked, not embedded) relationships and ones whose
    ``target_part`` raises (``AttributeError`` / ``ValueError`` — the two
    python-docx raises from its part resolution) are skipped: a broken
    rel must not crash the whole parse of an otherwise-readable document.
    """
    for rid, rel in document.part.rels.items():
        if rel.reltype != reltype:
            continue
        if rel.is_external:
            continue
        try:
            yield rid, rel.target_part
        except (AttributeError, ValueError):
            continue
