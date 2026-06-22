r"""OLE / MTEF payload extraction for the docx adapter.

Word stores MathType / Microsoft Equation 3.0 formulas as
``Equation.DSMT4`` / ``Equation.3`` OLE objects. This module pulls the
raw ``"Equation Native"`` MTEF stream out of the package and hands it
to the MTEF source adapter, surfacing the result as inline
``$<math>...</math>$`` text.

Unlike inline OMML / EQ fields — which defer to the frontend as raw
source-tagged islands (:mod:`brailix.core.inline_math`) — MTEF is
**binary** and cannot ride the text IR, so it is decoded here at the
input boundary. That is the deliberate exception to the "text math
defers" rule; see the package ``__init__`` "Math handling" note and
ARCHITECTURE §1.

DAG position: depends only on :mod:`._xml`. The python-docx / olefile
imports stay lazy (inside function bodies) so this module imports
without the ``docx`` extra installed.
"""

from __future__ import annotations

from typing import Any

from brailix.input.docx._xml import (
    _R_PREFIX,
    Element,
    _local,
    _ns_attr,
    _wrap_inline_math,
)

# Hard ceiling on a single ``Equation Native`` stream. A real MathType /
# Equation 3.0 formula is KB-scale; anything past this in an untrusted embed is
# treated as non-math and skipped, so a hostile / corrupt .docx can't inflate
# memory through one oversized OLE stream.
_MAX_MTEF_BYTES = 4 * 1024 * 1024  # 4 MiB


def _build_ole_blob_map(document: Any) -> dict[str, bytes]:
    """Index every OLE-object relationship by its rId.

    Word stores MathType / Microsoft Equation 3.0 formulas as
    ``Equation.DSMT4`` / ``Equation.3`` OLE objects whose data lives in
    ``word/embeddings/oleObjectN.bin``. The OOXML structure references
    them indirectly via a ``r:id`` attribute that resolves through
    ``document.xml.rels``.

    Pre-indexing the map once is cheaper than walking the rels for
    every ``<w:object>`` we encounter, and keeps the per-paragraph
    walker free of any python-docx-specific imports.
    """
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
    except ImportError:  # pragma: no cover — defensive
        return {}
    out: dict[str, bytes] = {}
    for rid, rel in document.part.rels.items():
        if rel.reltype != RT.OLE_OBJECT:
            continue
        # A linked (non-embedded) OLE object is an *external* relationship
        # with no local part. python-docx raises ``ValueError`` — not
        # ``AttributeError`` — from ``target_part`` for those, so skip them
        # up front; letting that escape would crash the whole parse on an
        # otherwise-readable document.
        if rel.is_external:
            continue
        try:
            blob = rel.target_part.blob
        except AttributeError:
            continue
        if blob:
            out[rid] = blob
    return out


def _ole_object_to_inline_math(
    obj_elem: Element, ole_blobs: dict[str, bytes]
) -> str | None:
    """Convert a ``<w:object>`` with an OLE equation to ``$<math>...$``.

    Looks for ``<o:OLEObject>`` inside ``obj_elem`` whose ``ProgID``
    starts with ``"Equation"`` (covers ``Equation.DSMT4`` for MathType
    4-7 and ``Equation.3`` for the legacy Microsoft Equation Editor).
    The OLE compound document blob is opened via ``olefile``; the
    ``"Equation Native"`` stream is the MTEF payload (with its 28-byte
    ``EQNOLEFILEHDR`` prefix). The MTEF adapter strips the header
    itself, so we hand the stream over unchanged.

    Returns ``None`` if no recognisable equation OLE object is found,
    the relationship has no blob, or ``olefile`` is missing. The
    paragraph walker treats ``None`` as "skip" so a stray OLE picture
    doesn't blow up parsing.
    """
    rid = _find_ole_rid(obj_elem)
    if rid is None or rid not in ole_blobs:
        return None
    payload = _extract_mtef_payload(ole_blobs[rid])
    if payload is None:
        return None
    from brailix.frontend.math.registry import math_source_registry

    mathml = math_source_registry.get("mtef").to_mathml(payload)
    return _wrap_inline_math(mathml)


def _is_equation_ole(elem: Element) -> bool:
    """True if ``elem`` is an ``<o:OLEObject>`` whose ProgID marks a math
    equation — ``Equation.DSMT4`` (MathType 4-7) or ``Equation.3`` (the
    legacy Microsoft Equation Editor).

    The single recognition rule, shared by the per-object resolver
    (:func:`_find_ole_rid`) and the document-wide counter
    (``_count_equation_oles`` in the package ``__init__``) so the two
    can't drift apart.
    """
    if _local(elem.tag) != "OLEObject":
        return False
    return (elem.get("ProgID") or "").startswith("Equation")


def _find_ole_rid(obj_elem: Element) -> str | None:
    """Return the ``r:id`` of the ``<o:OLEObject>`` child, if it's a math one.

    Word writes the relationship id on the ``OLEObject`` element under
    the ``r:`` (officeDocument relationships) namespace. ``v:imagedata``
    siblings use the same attribute name for the preview image; we
    ignore those.
    """
    for child in obj_elem:
        if not _is_equation_ole(child):
            continue
        # ``r:id`` lives in the relationships namespace; some emitters
        # also write it without a prefix. Try both forms.
        rid = _ns_attr(child, _R_PREFIX, "id")
        if rid:
            return rid
    return None


def _extract_mtef_payload(blob: bytes) -> bytes | None:
    """Open the OLE compound blob and return the ``"Equation Native"`` stream.

    The stream still has the 28-byte ``EQNOLEFILEHDR`` at the start —
    that's what :class:`~brailix.frontend.math.adapters.mtef.MtefMathSourceAdapter`
    expects.

    Falls back to a raw-MTEF interpretation if the blob is not a CFB
    container at all: some tooling (and our tests) ship the equation
    bytes inline without the OLE wrapper. The fallback only triggers
    when the leading bytes look like an EQNOLEFILEHDR (``cbHdr=28``) or
    a known MTEF prelude (``version=2..5``), so an unrelated binary
    won't accidentally be interpreted as math.

    Returns ``None`` when olefile is missing AND the raw heuristic
    fails — the caller treats ``None`` as "not a math object" and
    skips it.
    """
    # An oversized embed is not a KB-scale formula; skip it (treat as non-math)
    # rather than hand a huge untrusted blob to the OLE / MTEF parsers. The OLE
    # "Equation Native" stream read below is a substring of this blob, so
    # bounding the blob bounds the stream read too.
    if len(blob) > _MAX_MTEF_BYTES:
        return None
    import io

    try:
        import olefile
    except ImportError:
        return _try_raw_mtef(blob)

    stream = io.BytesIO(blob)
    if not olefile.isOleFile(stream):
        return _try_raw_mtef(blob)
    stream.seek(0)
    try:
        with olefile.OleFileIO(stream) as ole:
            if not ole.exists("Equation Native"):
                return _try_raw_mtef(blob)
            return ole.openstream("Equation Native").read()
    except Exception:  # noqa: BLE001 — defensive
        return _try_raw_mtef(blob)


def _try_raw_mtef(blob: bytes) -> bytes | None:
    """Recognise a raw / unwrapped MTEF payload by its prelude.

    Two shapes are accepted:

    * ``EQNOLEFILEHDR`` directly (``cbHdr=28`` little-endian) followed
      by MTEF — what an OLE ``Equation Native`` stream looks like with
      no CFB wrapper around it.
    * Bare MTEF starting with a version byte ``2``..``5``.

    Anything else returns ``None`` so the caller can leave the OLE
    object alone (e.g. it's a chart or some other non-math embed).
    """
    if not blob:
        return None
    if len(blob) >= 28 and blob[0] == 0x1C and blob[1] == 0x00:
        return blob
    if blob[0] in (2, 3, 4, 5):
        return blob
    return None
