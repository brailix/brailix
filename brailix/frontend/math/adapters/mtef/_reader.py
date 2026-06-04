"""Low-level byte reader and record-type constants for the MTEF adapter.

Both v3 and v5 share the same little-endian byte reader and the bulk of
the record-type tag values; the v5-only definition records add tags
``0x0F``..``0x13``. See :mod:`brailix.frontend.math.adapters.mtef` for the
format overview.
"""

from __future__ import annotations

_MATHML_NS: str = "http://www.w3.org/1998/Math/MathML"

# ---------------------------------------------------------------------------
# Record type constants (shared by v3 and v5; v5 adds 8/15-19)
# ---------------------------------------------------------------------------

_REC_END = 0x00
_REC_LINE = 0x01
_REC_CHAR = 0x02
_REC_TMPL = 0x03
_REC_PILE = 0x04
_REC_MATRIX = 0x05
_REC_EMBELL = 0x06
_REC_RULER = 0x07
_REC_FONT_OR_STYLE_DEF = 0x08  # v3: FONT; v5: FONT_STYLE_DEF
_REC_SIZE = 0x09
_REC_FULL = 0x0A
_REC_SUB = 0x0B
_REC_SUB2 = 0x0C
_REC_SYM = 0x0D
_REC_SUBSYM = 0x0E
# v5-only records:
_REC_COLOR = 0x0F
_REC_COLOR_DEF = 0x10
_REC_FONT_DEF = 0x11
_REC_EQN_PREFS = 0x12
_REC_ENCODING_DEF = 0x13


# ---------------------------------------------------------------------------
# Low-level byte reader
# ---------------------------------------------------------------------------


class _MtefParseError(Exception):
    """Raised on truncated / inconsistent MTEF data."""


class _Reader:
    """Byte-stream reader with the encodings MTEF needs.

    Both v3 and v5 are little-endian for multi-byte integers. v5 adds a
    variable-length integer encoding (the "extended" form) used by
    record fields like ``color_def_index`` and ``enc_def_index``; v3
    never uses it.
    """

    __slots__ = ("data", "pos")

    def __init__(self, data: bytes, pos: int = 0) -> None:
        self.data = data
        self.pos = pos

    def remaining(self) -> int:
        return len(self.data) - self.pos

    def peek(self) -> int:
        if self.pos >= len(self.data):
            raise _MtefParseError("unexpected end of MTEF data")
        return self.data[self.pos]

    def u8(self) -> int:
        if self.pos >= len(self.data):
            raise _MtefParseError("unexpected end of MTEF data")
        b = self.data[self.pos]
        self.pos += 1
        return b

    def i8_biased(self) -> int:
        """One byte biased by +128 (used for typeface, dx, dy in nudges)."""
        return self.u8() - 128

    def u16(self) -> int:
        if self.pos + 2 > len(self.data):
            raise _MtefParseError("unexpected end of MTEF data (u16)")
        lo, hi = self.data[self.pos], self.data[self.pos + 1]
        self.pos += 2
        return (hi << 8) | lo

    def i16(self) -> int:
        v = self.u16()
        if v & 0x8000:
            v -= 0x10000
        return v

    def u_extended(self) -> int:
        """v5 unsigned: 1 byte if < 0xFF, else ``0xFF + u16``."""
        b = self.u8()
        if b != 0xFF:
            return b
        return self.u16()

    def s_extended(self) -> int:
        """v5 signed: 1 byte (biased +128) if in range, else ``0xFF + i16``."""
        b = self.u8()
        if b != 0xFF:
            return b - 128
        return self.i16()

    def nstr(self) -> str:
        """Null-terminated byte string, decoded as Latin-1.

        MTEF font / encoding / color names are ASCII in practice;
        Latin-1 is a safe superset that never raises.
        """
        end = self.data.find(b"\x00", self.pos)
        if end < 0:
            raise _MtefParseError("unterminated null-terminated string")
        s = self.data[self.pos:end].decode("latin-1")
        self.pos = end + 1
        return s


# ---------------------------------------------------------------------------
# Shared byte-skip helpers (used by both the v3 and v5 readers)
# ---------------------------------------------------------------------------


def _skip_partitions(r: _Reader, n_lines: int) -> None:
    """Skip a 2-bit partition stream covering ``n_lines`` boundaries."""
    bytes_needed = (n_lines + 3) // 4  # 4 partitions per byte
    for _ in range(bytes_needed):
        r.u8()


def _skip_ruler(r: _Reader) -> None:
    n_stops = r.u8()
    for _ in range(n_stops):
        r.u8()   # type
        r.u16()  # offset
