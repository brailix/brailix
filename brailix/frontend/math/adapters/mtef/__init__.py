"""MTEF (MathType Equation Format) adapter.

Converts the binary equation format that MathType (and the legacy
Microsoft Equation 3.0) embed as an OLE object inside ``.docx`` files
into the project's normalisation mediator: MathML.

Word stores MathType / Equation 3.0 formulas as an embedded OLE
Compound File at ``word/embeddings/oleObjectN.bin``. The MTEF data
lives in that compound file's ``"Equation Native"`` stream, preceded by
a 28-byte ``EQNOLEFILEHDR``. The first byte after the header is the
MTEF version (``0x02``..``0x05``); we dispatch:

* ``v3``/``v4``  → Microsoft Equation 3.0 / older MathType. Tag byte
  carries the record type in the low nibble and option flags in the
  high nibble. CHAR records use the typeface byte + 16-bit char value.
  Used by most old textbook scans.
* ``v5``         → MathType 4+ (current). Type and option byte are
  separate; CHAR records carry an MTCode value with optional 8/16-bit
  font positions; the prelude is ≥11 bytes (version, platform, product,
  product version, product subversion, app-key string, equation
  options).

This adapter is intentionally pure-stdlib — it never imports
``python-docx``, ``lxml`` or ``olefile``. Extracting the MTEF payload
out of the OLE compound document is the docx input adapter's job
(:mod:`brailix.input.docx`); this module only does the dialect
translation, mirroring how :mod:`brailix.frontend.math.adapters.omml`
is decoupled from :mod:`docx`.

Coverage is the **common Word equation editor subset** — fractions,
sub/sup/subsup, radicals, n-ary (sum / product / integral / ...),
delimiters, matrices, equation arrays, limits, accents (bar / hat /
tilde / vector). Constructs outside this subset emit ``<mtext>``
carrying a marker so the document keeps parsing; a follow-up
``<merror>`` wrapper is produced for truly malformed input.

The byte-level parsing is split across this package: :mod:`._reader`
holds the low-level reader and record-type constants, :mod:`._mathml`
the MathML element builders and TMPL/fence translation, and
:mod:`._v5` / :mod:`._v3` the version-specific reader walks.

References:

* `MTEF v.5 <https://docs.wiris.com/mathtype/en/mathtype-office-tools/mathtype-7-for-windows-and-mac/mathtype-sdk/mathtype-mtef-v-5--mathtype-4-0-and-later-.html>`_
* `MTEF v.3 <https://docs.wiris.com/mathtype/en/mathtype-office-tools/mathtype-7-for-windows-and-mac/mathtype-sdk/mathtype-mtef-v-3--equation-editor-3-x-.html>`_
* `EQNOLEFILEHDR <https://docs.wiris.com/mathtype/en/mathtype-office-tools/mathtype-7-for-windows-and-mac/mathtype-sdk/how-mtef-is-stored-in-files-and-objects.html>`_
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.context import MathContext
from brailix.frontend.math.adapters.mtef._reader import _MtefParseError
from brailix.frontend.math.adapters.mtef._v3 import _convert_v3
from brailix.frontend.math.adapters.mtef._v5 import _convert_v5
from brailix.frontend.math.utils import merror_wrap


@dataclass(slots=True)
class MtefMathSourceAdapter:
    """Convert an MTEF binary payload to MathML.

    The ``formula`` argument can be either:

    * :class:`bytes` — the raw MTEF payload. If the first 28 bytes look
      like an :data:`EQNOLEFILEHDR` (``cbHdr=28``, ``version=0x00020000``),
      they are stripped automatically so callers can pass the full
      ``"Equation Native"`` stream unchanged.
    * :class:`str` — interpreted as a hex string. Whitespace is
      ignored. This form is mostly a convenience for tests; production
      callers should pass bytes directly.
    """

    source: str = "mtef"

    def to_mathml(
        self, formula: bytes | str, ctx: MathContext | None = None
    ) -> str:
        if isinstance(formula, str):
            cleaned = "".join(formula.split())
            if not cleaned:
                return merror_wrap("", reason="empty mtef payload")
            try:
                data = bytes.fromhex(cleaned)
            except ValueError as e:
                return merror_wrap(cleaned, reason=f"mtef hex decode error: {e}")
        else:
            data = formula
        if not data:
            return merror_wrap("", reason="empty mtef payload")

        # Strip 28-byte EQNOLEFILEHDR if present. The header is
        # identified by its first field, ``cbHdr=28`` (little-endian
        # WORD), followed by version ``0x00020000`` (little-endian
        # DWORD). We accept the lighter heuristic — ``cbHdr=28`` —
        # because some emitters fill the version field with zeros.
        if len(data) >= 28 and data[0] == 0x1C and data[1] == 0x00:
            data = data[28:]
        if not data:
            return merror_wrap("", reason="empty after stripping OLE header")

        version = data[0]
        try:
            if version >= 5:
                mathml = _convert_v5(data)
            elif version >= 2:
                mathml = _convert_v3(data)
            else:
                return merror_wrap(
                    data.hex(), reason=f"unsupported mtef version: {version}"
                )
        except _MtefParseError as e:
            return merror_wrap(data.hex(), reason=f"mtef parse error: {e}")
        except Exception as e:  # noqa: BLE001 — adapter must soft-fail
            return merror_wrap(data.hex(), reason=f"mtef convert error: {e}")
        return mathml


def _load() -> MtefMathSourceAdapter:
    return MtefMathSourceAdapter()


__all__ = ("MtefMathSourceAdapter", "_load")
