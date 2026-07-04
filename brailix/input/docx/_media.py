"""Embedded-image extraction for the docx adapter.

Word stores a picture's bytes as a package part under ``word/media/``
and references it from the body indirectly: a modern inline / floating
picture is ``<w:drawing>`` wrapping DrawingML whose ``<a:blip>`` carries
an ``r:embed`` relationship id; the legacy VML form is ``<w:pict>``
whose ``<v:imagedata>`` carries ``r:id``. Both ids resolve through
``document.xml.rels`` to the media part.

This module turns either element into an :class:`~brailix.ir.document.
ImageAlt` placeholder block — alt text from ``wp:docPr`` (or the VML
shape's ``alt``), ``target`` naming the media asset — and pre-indexes
the image relationships once per document (mirroring
:func:`.._ole._build_ole_blob_map`), so the paragraph walker stays free
of python-docx specifics. The bytes themselves ride out of the parse on
:attr:`~brailix.ir.document.DocumentIR.assets` under the same asset
name, per the "binary payloads decode eagerly at the input boundary"
rule (ARCHITECTURE §1); whether an image then *becomes* a tactile
graphic is the user's explicit, per-image decision
(ARCHITECTURE.md) — this layer only preserves it.

Deliberately out of scope (still skipped, as before): DrawingML with no
``<a:blip>`` (charts, SmartArt, shapes, text boxes — no raster to
preserve) and the preview picture inside a non-equation ``<w:object>``
OLE. A picture whose relationship is broken or external (linked, not
embedded) still yields a placeholder — alt text with no ``target`` — so
the image's *existence* survives even when its bytes can't.

DAG position: depends only on :mod:`._xml`, like :mod:`._ole`.
"""

from __future__ import annotations

from brailix.input.docx._xml import _R_PREFIX, Element, _local, _ns_attr
from brailix.ir.document import ImageAlt

# The relationship id attribute differs between the two forms —
# ``<a:blip r:embed="...">`` vs ``<v:imagedata r:id="...">`` — but both
# live in the officeDocument-relationships namespace (``_R_NS``).
_BLIP_RID_ATTRS = ("embed", "link")


def _build_image_blob_map(document: object) -> dict[str, tuple[str, bytes]]:
    """Index every embedded-image relationship: rId → (asset name, bytes).

    The asset name is the package part name with the ``word/`` container
    prefix stripped — ``/word/media/image1.png`` → ``media/image1.png`` —
    which is what :attr:`ImageAlt.target` carries and what
    :attr:`DocumentIR.assets` is keyed by, so the reference written into
    the editable source and the stored bytes can never disagree on
    naming. Linked (external) images have no local part and are skipped;
    they surface as a target-less placeholder instead.
    """
    try:
        from docx.opc.constants import RELATIONSHIP_TYPE as RT
    except ImportError:  # pragma: no cover — defensive
        return {}
    out: dict[str, tuple[str, bytes]] = {}
    for rid, rel in document.part.rels.items():  # type: ignore[attr-defined]
        if rel.reltype != RT.IMAGE:
            continue
        if rel.is_external:
            continue
        try:
            part = rel.target_part
            blob = part.blob
        except (AttributeError, ValueError):
            continue
        if not blob:
            continue
        name = str(part.partname).lstrip("/")
        if name.startswith("word/"):
            name = name[len("word/") :]
        out[rid] = (name, blob)
    return out


def _convert_drawing(
    drawing: Element, image_blobs: dict[str, tuple[str, bytes]]
) -> ImageAlt | None:
    """``<w:drawing>`` → :class:`ImageAlt`, or ``None`` when it holds no
    picture.

    Only DrawingML that actually references a raster (an ``<a:blip>``
    anywhere under the drawing — Word nests it ``wp:inline|wp:anchor >
    a:graphic > a:graphicData > pic:pic > pic:blipFill``) produces a
    placeholder; charts / SmartArt / shapes / text boxes have no blip
    and stay out of scope. Alt text comes from ``<wp:docPr>`` — ``descr``
    is the user-authored alt text, ``title`` the older Word 2010 field,
    ``name`` Word's automatic "Picture 1" — falling back to the media
    file's stem so the placeholder is never blank.
    """
    blip_rid: str | None = None
    doc_pr: Element | None = None
    for elem in drawing.iter():
        tag = _local(elem.tag)
        if tag == "blip" and blip_rid is None:
            for attr in _BLIP_RID_ATTRS:
                rid = _ns_attr(elem, _R_PREFIX, attr)
                if rid:
                    blip_rid = rid
                    break
        elif tag == "docPr" and doc_pr is None:
            doc_pr = elem
    if blip_rid is None:
        return None
    alt = ""
    if doc_pr is not None:
        alt = (
            doc_pr.get("descr") or doc_pr.get("title") or doc_pr.get("name") or ""
        ).strip()
    return _image_alt(alt, image_blobs.get(blip_rid))


def _convert_pict(
    pict: Element, image_blobs: dict[str, tuple[str, bytes]]
) -> ImageAlt | None:
    """``<w:pict>`` (legacy VML picture) → :class:`ImageAlt`, or ``None``.

    The raster reference is ``<v:imagedata r:id="...">``; alt text is the
    enclosing ``<v:shape>``'s ``alt`` attribute. Word still writes this
    form for pictures pasted into old-format documents, and LibreOffice
    conversions produce it too.
    """
    rid: str | None = None
    alt = ""
    for elem in pict.iter():
        tag = _local(elem.tag)
        if tag == "imagedata" and rid is None:
            rid = _ns_attr(elem, _R_PREFIX, "id")
        elif tag == "shape" and not alt:
            alt = (elem.get("alt") or "").strip()
    if rid is None:
        return None
    return _image_alt(alt, image_blobs.get(rid))


def _image_alt(alt: str, asset: tuple[str, bytes] | None) -> ImageAlt:
    """Build the placeholder, defaulting empty alt text to the asset's
    file stem (``media/image1.png`` → ``image1``) so a picture the author
    never described still reads as *something* in the braille flow."""
    target = asset[0] if asset is not None else None
    if not alt and target:
        stem = target.rsplit("/", 1)[-1]
        alt = stem.rsplit(".", 1)[0]
    return ImageAlt(text=alt, target=target)
