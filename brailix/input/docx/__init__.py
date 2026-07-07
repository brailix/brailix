r"""Microsoft Word input adapter — ``.docx`` / ``.docm`` (modern OOXML)
and ``.doc`` (legacy binary, via external conversion).

`.docx` / `.docm` (modern, ZIP + XML)
    Parsed directly with `python-docx` for paragraph + table walking,
    plus raw lxml access to pick up math (``m:oMath`` /
    ``m:oMathPara``) and list / heading style hints that ``python-docx``
    doesn't model.

`.doc` (legacy, OLE binary)
    Cannot be read in pure Python — we shell out to LibreOffice
    (``soffice --headless --convert-to docx``) to upgrade the file to
    ``.docx`` and then delegate. If LibreOffice (or any registered
    converter) isn't available, :func:`parse_doc` raises a clear
    ``ParseError`` directing the caller to convert manually.

Math handling
    Word stores formulas as OMML (and, for older documents, OLE
    MathType or legacy EQ fields). This adapter follows one rule, the
    same one ARCHITECTURE §1 states for the input/frontend boundary: a
    math source that arrives as **text** is left raw and *deferred* to
    the frontend's math pass; a source that arrives as **binary** is
    decoded here, because the text IR carries no binary payload. So:

    * Display equations (``m:oMathPara``) become a :class:`MathBlock`
      with ``source="omml"`` carrying the raw OMML; the math frontend
      converts it at Pipeline time.
    * Inline equations (``m:oMath`` inside a paragraph) and legacy Word
      EQ fields are wrapped as *deferred* source-tagged inline-math
      islands (:func:`brailix.core.inline_math.wrap`, ``source="omml"``
      / ``"eq_field"``) embedded in the paragraph's text. The frontend's
      segmenter recognises the island as a ``$...$`` region and the
      normalizer decodes the tag, so the matching adapter runs at
      Pipeline time — the same deferral the display path uses, and the
      reason this adapter imports no math frontend for these paths.
    * OLE MathType (``Equation.DSMT4`` / ``Equation.3``) is the binary
      case: its MTEF payload cannot live in the text IR, so it is decoded
      at the input boundary (via the MTEF adapter) and embedded as an
      eager ``$<math>...</math>$`` island. See :mod:`._ole`.
    * Sub/superscript runs (``<w:vertAlign>`` — the Ctrl+= / Ctrl+Shift+=
      shortcuts, or the Font dialog) are not a foreign source dialect at
      all: a maximal cluster of script-bearing runs is *synthesised* into
      an ``<msup>`` / ``<msub>`` MathML tree built here and embedded as a
      ``$<math>...</math>$`` island, so a formula typed as formatted text
      (``x²``, ``H₂O``) is no longer flattened to ``x2`` / ``H2O``. With
      ``chem_detection`` on, a cluster that conservatively reads as a
      chemical formula is tagged ``data-bk-chem`` so the backend applies
      chemistry rules instead of generic math.

The split lets inline math stay inline (no spurious paragraph break)
while making every OMML path — display and inline — defer through the
identical frontend route (:mod:`brailix.frontend.math.adapters.omml`).

To handle both legacy and modern files through one entry point, use
:func:`parse_file` (suffix dispatch); call :func:`parse_docx` or
:func:`parse_doc` directly when the format is already known.

Subpackage layout
    The adapter is split into cohesive helper modules, with the
    orchestration (public entry points + the LibreOffice converter
    resolution that tests monkeypatch) kept here in the package
    ``__init__`` so ``brailix.input.docx.<name>`` stays the stable
    namespace:

    * :mod:`._xml`    — OOXML namespace constants + low-level XML / tag
      / serialisation helpers (the DAG leaf).
    * :mod:`._ole`    — OLE / MTEF payload extraction
      (``_build_ole_blob_map`` and friends); depends on ``._xml``.
    * :mod:`._blocks` — body / paragraph / run / table walking;
      depends on ``._ole`` + ``._xml``.

    ``parse_docx`` / ``parse_doc`` / ``_parse_docx_via_libreoffice`` /
    ``_mtef_recovery_needed`` / ``_resolve_doc_converter`` live here together
    because they call each other as module globals — and the tests
    patch ``brailix.input.docx._resolve_doc_converter`` /
    ``brailix.input.docx.subprocess.run`` in *this* namespace, so the
    patched function and its callers must share it.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import tempfile
import zipfile
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

from brailix.core.errors import MissingExtraError, ParseError
from brailix.input.docx._blocks import _iter_body_blocks
from brailix.input.docx._media import _build_image_blob_map
from brailix.input.docx._ole import _build_ole_blob_map, _is_equation_ole
from brailix.input.docx._xml import _INLINE_MATH_CLOSE, _INLINE_MATH_OPEN
from brailix.ir.document import Block, DocumentIR

__all__ = (
    "parse_docx",
    "parse_doc",
    "_build_ole_blob_map",
)


# ---------------------------------------------------------------------------
# Archive resource caps (zip-bomb / OOM guard)
# ---------------------------------------------------------------------------
#
# python-docx — and the media / OLE indexers that run right after it — pull
# every package part into memory, so an unbounded ``.docx`` is a local
# denial-of-service: a few-KB archive can declare (or inflate to) gigabytes of
# parts and OOM or freeze the process before any parse. The ``.mxl`` adapter
# and the MTEF OLE reader already cap their inputs (64 MiB members); DOCX was
# the last unbounded container. A real document — even a long, image-heavy
# book — sits far under these; they only reject pathological input. Since every
# embedded image is itself a ``word/media/*`` archive member, the per-member
# cap doubles as the per-image cap and the total cap bounds all image bytes at
# once, so nothing downstream needs its own image budget.
_MAX_DOCX_FILE_BYTES = 256 * 1024 * 1024  # the .docx on disk
_MAX_DOCX_MEMBERS = 8192  # parts in the package
_MAX_DOCX_MEMBER_BYTES = 64 * 1024 * 1024  # one decompressed part
_MAX_DOCX_TOTAL_BYTES = 512 * 1024 * 1024  # all parts, decompressed
_DOCX_READ_CHUNK = 1024 * 1024


def _preflight_docx_archive(p: Path) -> None:
    """Reject an over-large or bomb-like ``.docx`` before python-docx reads it.

    Counts the *actual* decompressed bytes of each member (chunked, then
    discarded — peak cost is one chunk, not the whole part) and aborts
    mid-inflate once a member or the running total crosses its cap, mirroring
    :func:`brailix.frontend.music.adapters.mxl._read_member_capped`.
    ``ZipInfo.file_size`` is only advisory — a crafted archive can understate
    it — so the count is on the real stream, not the metadata.

    A non-ZIP / corrupt file is *not* rejected here: it falls through so the
    ``Document(...)`` call below raises the canonical "not a valid .docx"
    :class:`ParseError` with its usual message, keeping one error surface.
    """
    if p.stat().st_size > _MAX_DOCX_FILE_BYTES:
        raise ParseError(
            f"not a valid .docx file: {p} (archive is over the "
            f"{_MAX_DOCX_FILE_BYTES}-byte limit)"
        )
    try:
        with zipfile.ZipFile(p) as zf:
            members = [i for i in zf.infolist() if not i.is_dir()]
            if len(members) > _MAX_DOCX_MEMBERS:
                raise ParseError(
                    f"not a valid .docx file: {p} ({len(members)} members, "
                    f"over the {_MAX_DOCX_MEMBERS} limit)"
                )
            total = 0
            for info in members:
                member = 0
                with zf.open(info) as fh:
                    while chunk := fh.read(_DOCX_READ_CHUNK):
                        member += len(chunk)
                        total += len(chunk)
                        if member > _MAX_DOCX_MEMBER_BYTES:
                            raise ParseError(
                                f"not a valid .docx file: {p} (member "
                                f"{info.filename!r} inflates past the "
                                f"{_MAX_DOCX_MEMBER_BYTES}-byte limit)"
                            )
                        if total > _MAX_DOCX_TOTAL_BYTES:
                            raise ParseError(
                                f"not a valid .docx file: {p} (parts inflate "
                                f"past the {_MAX_DOCX_TOTAL_BYTES}-byte total "
                                f"limit)"
                            )
    except zipfile.BadZipFile:
        return  # not a zip → Document() raises the canonical ParseError


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_docx(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
    mathtype_fallback: str = "off",
    chem_detection: bool = False,
) -> DocumentIR:
    """Parse a ``.docx`` (or ``.docm``) file into :class:`DocumentIR`.

    Walks ``document.xml`` in body order so paragraphs, headings,
    lists, tables, and math blocks come out in the same order Word
    shows them. OMML math is preserved either as a dedicated
    :class:`MathBlock` (display equations) or inline ``$<math>...</math>$``
    inside the surrounding paragraph (inline equations). MathType /
    Microsoft Equation 3.0 OLE objects go through
    :class:`~brailix.frontend.math.adapters.mtef.MtefMathSourceAdapter`
    by default; see ``mathtype_fallback`` for the LibreOffice escape
    hatch.

    Parameters
    ----------
    mathtype_fallback
        Strategy for MathType OLE equations the native MTEF adapter
        can't decode:

        * ``"off"`` (default) — accept whatever the native adapter
          produces, including ``<merror>`` placeholders.
        * ``"libreoffice"`` — convert the whole document through
          ``soffice --headless --convert-to docx`` *before* parsing,
          which rewrites MathType OLE into native OMML. Slower and
          requires LibreOffice on PATH (or override via the
          environment-resolved converter).
        * ``"auto"`` — parse natively first; if the document contains
          OLE equations AND all of them failed, retry once through
          LibreOffice. Combines the speed of the native adapter with
          a safety net for old MTEF v3 / v4 files our coverage misses.
    chem_detection
        When ``True``, a sub/superscript cluster that conservatively reads
        as a chemical formula (real element signature: a multi-letter
        element, ≥2 elements, a charge, or a state label) is translated as
        chemistry (``data-bk-chem``) instead of generic math. Off by
        default because a lone single-letter variable subscript (``V₁``)
        coincides with an element symbol and can't be told apart from a
        formula by structure alone — the caller (Pipeline) turns it on from
        the ``input.docx.detect_chemistry`` profile feature.

    Raises
    ------
    MissingExtraError
        If ``python-docx`` (and its lxml dependency) is not installed.
    ParseError
        If the file is not a valid OOXML document, or
        ``mathtype_fallback="libreoffice"`` is requested but the
        converter is missing.
    FileNotFoundError
        If ``path`` does not exist.
    """
    if mathtype_fallback not in ("off", "libreoffice", "auto"):
        raise ValueError(
            f"mathtype_fallback must be 'off' | 'libreoffice' | 'auto', "
            f"got {mathtype_fallback!r}"
        )

    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    if mathtype_fallback == "libreoffice":
        return _parse_docx_via_libreoffice(
            p, language=language, profile=profile, chem_detection=chem_detection
        )

    try:
        import docx  # noqa: F401 — kept for clearer error
        from docx import Document
    except ImportError as e:
        raise MissingExtraError(
            adapter="docx",
            extra="docx",
            hint="Install with: pip install brailix[docx]",
        ) from e

    # python-docx raises ``docx.opc.exceptions.PackageNotFoundError`` for
    # anything that isn't a valid ZIP / OOXML container; older paths still
    # surface ``zipfile.BadZipFile`` / ``KeyError``. Gather them into one
    # exception tuple so the caller always sees a brailix :class:`ParseError`.
    # The tuple is typed as a generic exception tuple, so it type-checks the
    # same whether or not python-docx is installed (the import resolves to the
    # real class with the extra, or to ``Any`` without it).
    bad_docx: tuple[type[BaseException], ...] = (zipfile.BadZipFile, KeyError)
    try:
        from docx.opc.exceptions import PackageNotFoundError
    except ImportError:  # pragma: no cover — defensive
        bad_docx += (Exception,)
    else:
        bad_docx += (PackageNotFoundError,)

    # Bound the archive before python-docx materialises every part in memory
    # (zip-bomb / OOM guard). A non-ZIP falls through to the canonical error.
    _preflight_docx_archive(p)

    try:
        document = Document(str(p))
    except bad_docx as e:
        raise ParseError(f"not a valid .docx file: {p} ({e})") from e

    ole_blobs = _build_ole_blob_map(document)
    image_blobs = _build_image_blob_map(document)
    body = document.element.body
    try:
        blocks = list(
            _iter_body_blocks(
                body,
                ole_blobs=ole_blobs,
                chem_detection=chem_detection,
                image_blobs=image_blobs,
            )
        )
    except RecursionError as e:
        # The block walkers recurse through wrapper elements (ins/del/sdt/
        # customXml/hyperlink/AlternateContent) with no depth cap, so a docx
        # nesting those thousands deep blows the Python stack. That escapes as
        # a raw RecursionError, bypassing parse_docx's "malformed docx →
        # ParseError" contract; convert it.
        raise ParseError(
            f"not a valid .docx file: {p} (pathologically nested content)"
        ) from e
    result = DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=blocks,
        # The pictures the body references, keyed by the asset name each
        # ImageAlt.target carries — decoded eagerly here because the text IR
        # carries no binary payload (ARCHITECTURE §1, same rule as MTEF).
        assets={name: blob for name, blob in image_blobs.values()},
    )

    if mathtype_fallback == "auto":
        # Count the equation OLE objects the body actually contains —
        # NOT "any OLE relationship exists" (charts and Excel sheets are
        # OLE too) and NOT "ole_blobs is non-empty" (a broken
        # relationship leaves the map empty while the equation is still
        # sitting in the body, silently unreadable).
        equation_oles = _count_equation_oles(body)
        if equation_oles and _mtef_recovery_needed(result, equation_oles):
            try:
                return _parse_docx_via_libreoffice(
                    p, language=language, profile=profile,
                    chem_detection=chem_detection,
                )
            except ParseError:
                # LibreOffice unavailable or refused — silently keep the
                # native-adapter result. The user explicitly asked for
                # "auto", which means "try the safety net but don't fail
                # if it's not there".
                return result

    return result


def _convert_via_libreoffice_and_parse(
    p: Path,
    exe: str,
    *,
    language: str,
    profile: str,
    chem_detection: bool,
    prefix: str,
    timeout_hint: str = "",
    produce_hint: str = "",
) -> DocumentIR:
    """Convert ``p`` to .docx with LibreOffice in a temp dir, then parse it.

    Shared by the legacy ``.doc`` entry point and the ``mathtype_fallback``
    MathType-recovery path — both shell out to the same
    ``soffice --headless --convert-to docx`` and re-parse the result. ``exe``
    is the already-resolved converter; ``prefix`` names the temp dir;
    ``timeout_hint`` / ``produce_hint`` append path-specific diagnostics to
    the timeout / missing-output errors.

    Re-parses with ``mathtype_fallback="off"`` so a conversion that fails to
    strip OLE equations can't recurse back into LibreOffice.
    """
    with tempfile.TemporaryDirectory(prefix=prefix) as out_dir:
        try:
            subprocess.run(
                [exe, "--headless", "--convert-to", "docx",
                 "--outdir", out_dir, str(p)],
                check=True,
                capture_output=True,
                timeout=60,
            )
        except subprocess.CalledProcessError as e:
            raise ParseError(
                f"LibreOffice failed to convert {p.name!r}: "
                f"{e.stderr.decode('utf-8', 'replace').strip() or e}"
            ) from e
        except subprocess.TimeoutExpired as e:
            raise ParseError(
                f"LibreOffice timed out converting {p.name!r} (60s){timeout_hint}."
            ) from e
        converted = Path(out_dir) / (p.stem + ".docx")
        if not converted.exists():
            raise ParseError(
                f"LibreOffice did not produce a .docx for {p.name!r}{produce_hint}."
            )
        return parse_docx(
            converted,
            language=language,
            profile=profile,
            mathtype_fallback="off",
            chem_detection=chem_detection,
        )


def _parse_docx_via_libreoffice(
    p: Path,
    *,
    language: str,
    profile: str,
    converter: str | None = None,
    chem_detection: bool = False,
) -> DocumentIR:
    """Round-trip the document through LibreOffice and re-parse.

    LibreOffice's docx importer converts most MathType / Equation 3.0
    OLE objects to native OMML, after which our normal parser handles
    them. Used by ``mathtype_fallback="libreoffice"`` and as the safety
    net for ``"auto"``.

    Raises :class:`ParseError` if no converter is available — same
    diagnostic the legacy ``.doc`` path uses, so callers see one shape
    of error for both ``.doc → .docx`` and the MathType fallback.
    """
    exe = _resolve_doc_converter(converter)
    if exe is None:
        raise ParseError(
            f"cannot apply LibreOffice mathtype_fallback for {p.name!r}: "
            "no converter found. Install LibreOffice (provides 'soffice') "
            "or use mathtype_fallback='off'."
        )
    return _convert_via_libreoffice_and_parse(
        p, exe, language=language, profile=profile,
        chem_detection=chem_detection, prefix="brailix-mtef-",
    )


def _count_equation_oles(body: Any) -> int:
    """Count the ``<o:OLEObject>`` elements whose ProgID marks an equation.

    Shares its recognition rule with the per-object resolver via
    ``._ole._is_equation_ole`` — ProgID starting with ``"Equation"`` covers
    MathType's ``Equation.DSMT4`` and the legacy ``Equation.3``.  This is
    what the ``mathtype_fallback="auto"`` decision keys on: equation OLEs
    are the only thing the LibreOffice retry can recover, so the body's
    actual equation count — not the mere presence of *some* OLE
    relationship — is the signal.
    """
    return sum(1 for elem in body.iter() if _is_equation_ole(elem))


def _iter_block_texts(blocks: Iterable[Block]) -> Iterator[str]:
    """Yield the raw ``text`` of every block, descending into container
    blocks (``List.items``, ``Table.rows`` / ``TableRow.cells``).

    Inline math nested in a list item or table cell lives in a *child*
    block's ``text``, not on a top-level block, so a flat ``result.blocks``
    walk would miss it — which is exactly what made the
    ``mathtype_fallback="auto"`` span count under-count equations sitting in
    a table and trigger a needless LibreOffice round-trip even when the
    native MTEF decode had succeeded.
    """
    for blk in blocks:
        text = getattr(blk, "text", None)
        if text:
            yield text
        for attr in ("items", "cells", "rows"):
            nested = getattr(blk, attr, None)
            if nested:
                yield from _iter_block_texts(nested)


def _mtef_recovery_needed(result: DocumentIR, equation_oles: int) -> bool:
    """Decide whether ``mathtype_fallback="auto"`` should retry via LibreOffice.

    ``equation_oles`` is the number of equation OLE objects the body
    contains (:func:`_count_equation_oles`); callers only ask when it's
    non-zero.  Two independent signals mean the native MTEF path failed:

    * **Fewer inline-math spans than equation OLEs** — at least one
      equation vanished without a trace.  The extraction path returns
      ``None`` (no span, not even an ``<merror>``) for a third-party
      ProgID variant it half-recognises, a broken relationship, a
      corrupt CFB container, or a missing ``olefile`` — exactly the
      silent-loss class that already shipped once in a frozen build.
      ``spans`` counts only *eager* ``$<math>...$`` islands, which only
      the OLE→MTEF path emits (:func:`._xml._wrap_inline_math`); native
      OMML, EQ fields and script-run clusters defer as source-tagged
      islands (:func:`brailix.core.inline_math.wrap`) that don't open
      with ``$<math`` and so aren't counted here. The count therefore
      reflects decoded OLE equations only and is directly comparable to
      ``equation_oles`` — a silently-dropped OLE shows up as a shortfall
      that fires the retry, with no risk of a non-OLE span masking it.
    * **Spans exist but every one is an ``<merror>`` soft failure** —
      nothing decoded successfully (the original "all failed" rule).
    """
    spans: list[str] = []
    for text in _iter_block_texts(result.blocks):
        idx = 0
        while True:
            start = text.find(_INLINE_MATH_OPEN, idx)
            if start < 0:
                break
            end = text.find(_INLINE_MATH_CLOSE, start)
            if end < 0:
                break
            spans.append(text[start:end + len(_INLINE_MATH_CLOSE)])
            idx = end + 1
    if len(spans) < equation_oles:
        return True
    return bool(spans) and all("merror" in s for s in spans)


def parse_doc(
    path: str | os.PathLike[str],
    *,
    language: str,
    profile: str,
    converter: str | None = None,
    chem_detection: bool = False,
) -> DocumentIR:
    """Parse a legacy ``.doc`` file by converting to ``.docx`` first.

    ``.doc`` is an OLE compound document; no pure-Python library reads
    it well enough to extract OMML math, so we delegate the conversion
    to **LibreOffice headless** (``soffice``). The converted ``.docx``
    is parsed via :func:`parse_docx` and the temporary file is cleaned
    up before returning.

    ``converter`` overrides the executable name; the default tries
    ``soffice`` then ``libreoffice`` (the two common LibreOffice
    launchers across platforms). Both accept the same headless flags.

    Raises
    ------
    ParseError
        If no converter is available, or the converter fails to
        produce a ``.docx`` output. The error message hints at the
        manual fix ("convert to .docx first").
    """
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(p)

    exe = _resolve_doc_converter(converter)
    if exe is None:
        raise ParseError(
            f"cannot read legacy .doc file {p.name!r}: no converter found. "
            "Install LibreOffice (provides 'soffice') or save the document "
            "as .docx in Word first."
        )

    return _convert_via_libreoffice_and_parse(
        p, exe, language=language, profile=profile,
        chem_detection=chem_detection, prefix="brailix-doc-",
        timeout_hint="; the file may be corrupt or password-protected",
        produce_hint="; the file may be unreadable",
    )


def _resolve_doc_converter(override: str | None) -> str | None:
    """Find the LibreOffice executable, or ``None`` if absent."""
    if override is not None:
        # shutil.which resolves both a bare command name (searched on PATH)
        # and an explicit path, returning None if neither is runnable. The
        # old ``... if Path(override).exists() else None`` bound looser than
        # ``or`` and so skipped the PATH search entirely for a command name
        # (the common case), always yielding None.
        return shutil.which(override)
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path is not None:
            return path
    return None
