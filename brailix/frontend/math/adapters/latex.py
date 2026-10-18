"""LaTeX adapter backed by the ``latex2mathml`` package.

The adapter calls :func:`latex2mathml.converter.convert` and returns
the resulting MathML string. Errors from the converter are reported
as a soft failure (``<merror>`` wrapped MathML) rather than re-raising,
so a malformed snippet never breaks a whole document.

**Embedded mhchem**: a formula may mix maths and ``\\ce{...}`` segments
(``$\\ce{H2O} + x$``). latex2mathml has no mhchem support — it would
render a stray ``<mi>\\ce</mi>`` — so every ``\\ce{...}`` in the formula
is routed to the chemistry converter (which emits ``data-bk-chem``
MathML for the backend's chem rules) and the remaining segments through
latex2mathml, re-joined as one ``<mrow>`` in source order. A formula
that is exactly one top-level ``\\ce{...}`` keeps its historical
whole-formula chemistry output.

**Source-span granularity**: ``latex2mathml`` does not emit token
positions, so the MathML this adapter produces never carries
``data-bk-span`` attributes. Every braille cell emitted downstream
therefore inherits the formula-level :class:`~brailix.ir.inline.MathInline`
span. Future LaTeX adapters (or wrappers around this one) can opt
into finer-grained spans by post-processing the MathML tree and
filling ``data-bk-span="start,end"`` on tokens; the backend will
honour those per-element.
"""

from __future__ import annotations

import xml.etree.ElementTree as _ET
from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core._xml import safe_fromstring, strip_namespace
from brailix.core.context import MathContext
from brailix.frontend.math.adapters import chem
from brailix.frontend.math.utils import (
    _MATHML_NS,
    decode_source_bytes,
    merror_wrap,
)
from brailix.frontend.math.utils import (
    _strip_math_delimiters as _shared_strip,
)

if _TYPE_CHECKING:
    from collections.abc import Callable


@_dataclass(slots=True)
class LatexMathSourceAdapter:
    """Wraps a ``latex → MathML`` converter callable.

    ``converter`` is exposed as a constructor argument so tests can
    inject a fake without installing latex2mathml. The real one is
    plugged in by :func:`_load`.
    """

    source: str = "latex"
    converter: Callable[[str], str] = _field(default=None)  # type: ignore[assignment]

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        decoded = decode_source_bytes(formula)
        if decoded is None:
            return merror_wrap(repr(formula), reason="non-utf8 bytes")
        text = decoded.strip()
        if not text:
            return merror_wrap("", reason="empty input")
        # ``$...$`` / ``\(...\)` wrappers leak into segment surfaces;
        # strip them so latex2mathml sees just the inner expression.
        text = _strip_math_delimiters(text)
        # mhchem ``\ce{...}`` is LaTeX syntax latex2mathml can't parse.
        # Split the formula into alternating maths / chemistry segments
        # so EVERY ``\ce{...}`` in it goes through the chemistry
        # converter (which emits ``data-bk-chem`` MathML for the
        # backend's chem rules) while the rest stays with latex2mathml.
        segments = _split_ce_segments(text)
        if segments is not None:
            if len(segments) == 1 and segments[0][0]:
                # The whole formula is one top-level ``\ce{...}`` — the
                # historical whole-formula chemistry output, unchanged.
                return chem.convert_ce(segments[0][1])
            return _convert_mixed_segments(self.converter, segments)
        try:
            return self.converter(text)
        except Exception as e:  # noqa: BLE001 — third-party failures vary
            return merror_wrap(text, reason=f"latex2mathml error: {e}")


def _strip_math_delimiters(text: str) -> str:
    """Peel the inline-math delimiters that the segmenter leaves attached.

    Delegates to the shared helper in
    :mod:`brailix.frontend.math.utils` so the LaTeX and MathML adapters
    stay in sync on delimiter rules; re-exported under the local name to
    preserve the historical import path for tests.
    """
    return _shared_strip(text)


# ---------------------------------------------------------------------------
# Embedded \ce segmentation
# ---------------------------------------------------------------------------

_CE_TOKEN = "\\ce"


def _find_ce_start(text: str, start: int) -> int:
    """Index of the next ``\\ce`` *control sequence* at/after ``start``.

    The macro name must end at ``ce``: a ``\\ce`` immediately followed by
    a letter is a longer command (``\\centering``), not mhchem, so it is
    skipped. Returns ``-1`` when none is found."""
    i = text.find(_CE_TOKEN, start)
    while i != -1:
        j = i + len(_CE_TOKEN)
        if j >= len(text) or not text[j].isalpha():
            return i
        i = text.find(_CE_TOKEN, j)
    return -1


def _match_ce_braces(text: str, scan_from: int) -> tuple[int, int] | None:
    """``(arg_open, end)`` for a ``\\ce{...}`` whose argument opens at or
    after ``scan_from`` (skipping whitespace), where ``end`` is the index
    just past the closing ``}``. ``None`` when no brace opens there or
    the braces never close — a brace-matched scan, so a nested group
    (``\\ce{... ->[{cat}] ...}``) extracts whole."""
    i = scan_from
    n = len(text)
    while i < n and text[i].isspace():
        i += 1
    if i >= n or text[i] != "{":
        return None
    arg_open = i
    depth = 0
    while i < n:
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return arg_open, i + 1
        i += 1
    return None


def _split_ce_segments(
    text: str,
) -> list[tuple[bool, str]] | None:
    """Split ``text`` into ordered ``(is_chem, content)`` segments around
    every ``\\ce{...}`` — the chem segments hold the brace argument,
    the maths segments the literal text between them.

    Returns ``None`` when the formula contains no ``\\ce{...}`` at all
    (the overwhelmingly common case — the caller then keeps its plain
    latex2mathml path). A ``\\ce`` whose braces never close (or never
    open) is left in the maths text for latex2mathml to fail on, matching
    how a stray ``\\ce`` behaved before segmentation existed."""
    segments: list[tuple[bool, str]] = []
    plain_start = 0
    i = _find_ce_start(text, 0)
    found = False
    while i != -1:
        matched = _match_ce_braces(text, i + len(_CE_TOKEN))
        if matched is None:
            # Unbraced or unbalanced \ce — not a usable mhchem argument.
            i = _find_ce_start(text, i + len(_CE_TOKEN))
            continue
        found = True
        arg_open, end = matched
        plain = text[plain_start:i]
        if plain.strip():
            segments.append((False, plain))
        segments.append((True, text[arg_open + 1 : end - 1]))
        plain_start = end
        i = _find_ce_start(text, end)
    if not found:
        return None
    trailing = text[plain_start:]
    if trailing.strip():
        segments.append((False, trailing))
    return segments


def _convert_mixed_segments(
    converter: Callable[[str], str], segments: list[tuple[bool, str]]
) -> str:
    """Convert maths / chem segments and re-join them into ONE ``<math>``
    document with the segments' children wrapped per-segment in ``<mrow>``
    elements, in source order.

    A chem segment's ``<mrow>`` carries ``data-bk-chem="1"`` — the same
    ABI attribute a whole-formula ``\\ce`` output sets on its root — so
    the backend switches to chemistry rules for exactly that subtree and
    back for the maths around it. Each segment's MathML is parsed and its
    namespace stripped before merging, so the combined document keeps one
    clean default namespace (written literally on the root, the OMML
    adapter's serialisation pattern). A maths segment that fails to
    convert degrades to the standard ``<merror>`` wrap for that segment
    alone; the chem converter already soft-fails internally."""
    math_root = _ET.Element("math", {"xmlns": _MATHML_NS})
    wrapper = _ET.SubElement(math_root, "mrow")
    for is_chem, seg in segments:
        if is_chem:
            seg_ml = chem.convert_ce(seg)
        else:
            stripped = seg.strip()
            if not stripped:
                continue  # pure filler between adjacent \ce segments
            try:
                seg_ml = converter(stripped)
            except Exception as e:  # noqa: BLE001 — third-party failures vary
                seg_ml = merror_wrap(stripped, reason=f"latex2mathml error: {e}")
        try:
            seg_root = safe_fromstring(seg_ml)
        except _ET.ParseError as e:
            # chem.convert_ce output is always well-formed (it wraps its
            # own failures in <merror>), so this is a latex2mathml
            # output that isn't XML — degrade that segment, not the formula.
            seg_root = safe_fromstring(
                merror_wrap(seg, reason=f"latex2mathml output not XML: {e}")
            )
        strip_namespace(seg_root)
        holder = _ET.Element("mrow")
        if is_chem:
            # Preserve the chemistry ABI on the subtree (convert_ce puts
            # it on its own <math> root, which is discarded here).
            holder.set("data-bk-chem", "1")
        for child in seg_root:
            holder.append(child)
        wrapper.append(holder)
    return _ET.tostring(math_root, encoding="unicode")


def _load() -> LatexMathSourceAdapter:
    """Lazy-import the converter and return a configured adapter."""
    from latex2mathml.converter import convert  # noqa: WPS433 — lazy by design

    return LatexMathSourceAdapter(converter=convert)
