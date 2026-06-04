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
    Word stores formulas as OMML.  This adapter:

    * Top-level / paragraph-only display equations
      (``m:oMathPara``)        →  :class:`MathBlock` with
                                  ``source="omml"`` carrying the OMML
                                  XML string. The math frontend's OMML
                                  adapter then converts to MathML at
                                  Pipeline time.
    * Inline equations (``m:oMath`` inside a paragraph)
                                →  converted to MathML synchronously
                                   here, then embedded into the
                                   paragraph's text as ``$<math>...</math>$``.
                                   The frontend's segmenter recognises
                                   the ``$...$`` wrapping; a tiny
                                   normalize tweak detects the MathML
                                   payload and sets ``source="mathml"``
                                   on the resulting :class:`MathInline`.

The split lets inline math stay inline (no spurious paragraph break)
while keeping the docx adapter's OMML→MathML conversion path identical
to the path used for display equations — both go through
:mod:`brailix.frontend.math.adapters.omml`.

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
    ``_all_mtef_failed`` / ``_resolve_doc_converter`` live here together
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
from pathlib import Path

from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.core.errors import MissingExtraError, ParseError
from brailix.input.docx._blocks import _iter_body_blocks
from brailix.input.docx._ole import _build_ole_blob_map
from brailix.ir.document import DocumentIR

__all__ = (
    "parse_docx",
    "parse_doc",
    "_build_ole_blob_map",
)


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def parse_docx(
    path: str | os.PathLike[str],
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
    mathtype_fallback: str = "off",
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
            p, language=language, profile=profile
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

    try:
        document = Document(str(p))
    except bad_docx as e:
        raise ParseError(f"not a valid .docx file: {p} ({e})") from e

    ole_blobs = _build_ole_blob_map(document)
    body = document.element.body
    blocks = list(_iter_body_blocks(body, ole_blobs=ole_blobs))
    result = DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=blocks,
    )

    if (
        mathtype_fallback == "auto"
        and ole_blobs
        and _all_mtef_failed(result)
    ):
        try:
            return _parse_docx_via_libreoffice(
                p, language=language, profile=profile
            )
        except ParseError:
            # LibreOffice unavailable or refused — silently keep the
            # native-adapter result. The user explicitly asked for
            # "auto", which means "try the safety net but don't fail
            # if it's not there".
            return result

    return result


def _parse_docx_via_libreoffice(
    p: Path,
    *,
    language: str,
    profile: str,
    converter: str | None = None,
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
    with tempfile.TemporaryDirectory(prefix="brailix-mtef-") as out_dir:
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
                f"LibreOffice timed out converting {p.name!r} (60s)."
            ) from e
        converted = Path(out_dir) / (p.stem + ".docx")
        if not converted.exists():
            raise ParseError(
                f"LibreOffice did not produce a .docx for {p.name!r}."
            )
        # Re-parse the converted file with fallback OFF so we don't
        # recurse infinitely if the conversion failed to remove OLE
        # equations.
        return parse_docx(
            converted,
            language=language,
            profile=profile,
            mathtype_fallback="off",
        )


def _all_mtef_failed(result: DocumentIR) -> bool:
    """Return True iff every inline-math span looks like a soft failure.

    The MTEF adapter emits ``<merror>`` wrappers when it can't decode
    something. If the document had OLE equations but every resulting
    inline-math surface contains ``merror``, the native path is
    effectively useless for this file — that's the signal for
    ``mathtype_fallback="auto"`` to retry through LibreOffice. A
    document with zero inline math spans returns ``True`` only when
    OLE objects existed AND none of them produced any inline math
    (i.e. they were all skipped); the caller already gates on
    ``ole_blobs`` being non-empty, so this is safe.
    """
    saw_inline = False
    saw_ok = False
    for blk in result.blocks:
        text = getattr(blk, "text", None)
        if not text:
            continue
        idx = 0
        while True:
            start = text.find("$<math", idx)
            if start < 0:
                break
            end = text.find("</math>$", start)
            if end < 0:
                break
            span = text[start:end + len("</math>$")]
            saw_inline = True
            if "merror" not in span:
                saw_ok = True
            idx = end + 1
    if not saw_inline:
        # No inline math at all → fall back, since the caller already
        # confirmed OLE objects existed.
        return True
    return not saw_ok


def parse_doc(
    path: str | os.PathLike[str],
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
    converter: str | None = None,
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

    with tempfile.TemporaryDirectory(prefix="brailix-doc-") as out_dir:
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
                f"LibreOffice timed out converting {p.name!r} "
                "(60s); the file may be corrupt or password-protected."
            ) from e

        converted = Path(out_dir) / (p.stem + ".docx")
        if not converted.exists():
            raise ParseError(
                f"LibreOffice did not produce a .docx for {p.name!r}; "
                "the file may be unreadable."
            )
        return parse_docx(converted, language=language, profile=profile)


def _resolve_doc_converter(override: str | None) -> str | None:
    """Find the LibreOffice executable, or ``None`` if absent."""
    if override is not None:
        return shutil.which(override) or override if Path(override).exists() else None
    for candidate in ("soffice", "libreoffice"):
        path = shutil.which(candidate)
        if path is not None:
            return path
    return None
