"""Tiny MTEF byte-stream builder for unit tests.

This module is **not** part of ``brailix`` — it lives under tests only.
The real MtefMathSourceAdapter consumes binary input that, in production,
comes from the ``Equation Native`` OLE stream inside a Word ``.docx``.
We don't have a way to generate that programmatically without owning a
MathType install, so the tests synthesise the same bytes by hand using
helpers that mirror the MTEF v3 / v5 record layouts directly.

The two ``v3`` and ``v5`` namespaces emit slightly different wire forms:

* v3 packs ``record type`` (low 4 bits) and ``option flags`` (high 4
  bits) into a single tag byte; CHAR uses a 1-byte typeface + 16-bit
  char.
* v5 uses one byte for record type, one for options, and CHAR carries
  an MTCode 16-bit + optional font-position bytes.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Generic helpers
# ---------------------------------------------------------------------------


def u16_le(v: int) -> bytes:
    """Little-endian unsigned 16-bit."""
    return bytes([v & 0xFF, (v >> 8) & 0xFF])


# ---------------------------------------------------------------------------
# v5 helpers
# ---------------------------------------------------------------------------


def v5_prelude(app_key: str = "DSMT7", *, options: int = 0) -> bytes:
    """11+ byte v5 prelude. ``app_key`` is null-terminated."""
    return (
        bytes([5, 1, 0, 11, 0])  # version, win, MathType, 11.0
        + app_key.encode("ascii")
        + b"\x00"
        + bytes([options])
    )


def v5_end() -> bytes:
    return bytes([0x00])


def v5_line(body: bytes, *, opts: int = 0x00) -> bytes:
    """v5 LINE record. ``body`` is the inner object list; we append
    the terminating END."""
    return bytes([0x01, opts]) + body + v5_end()


def v5_null_line() -> bytes:
    """v5 LINE with the null flag set — no object list, no END."""
    return bytes([0x01, 0x01])


def v5_char(mtcode: int, *, typeface: int = 0, embells: list[int] | None = None) -> bytes:
    """v5 CHAR record: one MTCode glyph, optionally with embellishments.

    ``typeface`` is the signed-extended typeface (defaults to 0). The
    ``opts`` byte is set to include an embellishment list iff
    ``embells`` is non-empty.
    """
    opts = 0x01 if embells else 0x00
    out = bytearray([0x02, opts])
    # typeface: signed extended (1 byte if in range; we always stay in range)
    out.append((typeface + 128) & 0xFF)
    # MTCode 16-bit
    out.extend(u16_le(mtcode))
    if embells:
        for e in embells:
            out.extend(bytes([0x06, 0x00, e]))
        out.append(0x00)  # END of embell list
    return bytes(out)


def v5_tmpl(
    selector: int,
    slots: list[bytes],
    *,
    variation: int = 0,
    tmpl_options: int = 0,
) -> bytes:
    """v5 TMPL record. Each entry in ``slots`` should already be a
    full LINE / CHAR / TMPL / PILE record sequence."""
    out = bytearray([0x03, 0x00, selector, variation, tmpl_options])
    for s in slots:
        out.extend(s)
    out.append(0x00)  # END
    return bytes(out)


def v5_pile(rows: list[bytes]) -> bytes:
    """v5 PILE — vertical stack. Each row should be a complete LINE
    record."""
    out = bytearray([0x04, 0x00, 1, 0])  # halign=left, valign=top
    for r in rows:
        out.extend(r)
    out.append(0x00)
    return bytes(out)


def v5_matrix(
    rows: int,
    cols: int,
    cells: list[bytes],
) -> bytes:
    """v5 MATRIX with no partition lines. ``cells`` is row-major list
    of complete LINE records (length must equal rows*cols)."""
    assert len(cells) == rows * cols, "wrong cell count"
    row_parts = bytes((rows + 1 + 3) // 4)  # zeros
    col_parts = bytes((cols + 1 + 3) // 4)
    out = bytearray([0x05, 0x00, 0, 0, 0, rows, cols])
    out.extend(row_parts)
    out.extend(col_parts)
    for c in cells:
        out.extend(c)
    out.append(0x00)
    return bytes(out)


def v5_simple_char_line(mtcode: int) -> bytes:
    """Shortcut: a LINE containing a single CHAR."""
    return v5_line(v5_char(mtcode))


def v5_eqn_prefs(
    *,
    opts: int = 0,
    sizes: list[bytes] | None = None,
    spaces: list[bytes] | None = None,
    styles: list[tuple[int, int | None]] | None = None,
) -> bytes:
    """Synthesise a v5 EQN_PREFS record.

    ``sizes`` and ``spaces`` are each a list of pre-encoded nibble
    streams — each stream should end with a high or low ``0xF`` nibble
    and be packed two-per-byte (high-nibble first). The helper
    concatenates them and pads to a byte boundary if needed.

    ``styles`` is a list of ``(font_def_idx, style_byte)`` pairs. If the
    index is zero, ``style_byte`` must be ``None`` (per spec: only a
    nonzero index carries a style byte).
    """
    out = bytearray([0x12, opts & 0xFF])
    sizes = sizes or []
    spaces = spaces or []
    styles = styles or []
    out.append(len(sizes) & 0xFF)
    for s in sizes:
        out.extend(s)
    out.append(len(spaces) & 0xFF)
    for s in spaces:
        out.extend(s)
    out.append(len(styles) & 0xFF)
    for idx, style_byte in styles:
        # u_extended: 1 byte if <0xFF, else 0xFF + 2-byte LE
        if idx < 0xFF:
            out.append(idx & 0xFF)
        else:
            out.append(0xFF)
            out.extend(u16_le(idx))
        if idx != 0:
            assert style_byte is not None, "nonzero style index needs style byte"
            out.append(style_byte & 0xFF)
        else:
            assert style_byte is None, "zero style index must not have style byte"
    return bytes(out)


def v5_inline_ruler_line(body: bytes, *, stops: list[tuple[int, int]] | None = None) -> bytes:
    """v5 LINE with opts=0x02 and ruler data emitted inline (no 0x07 tag).

    Mirrors what MathType 6+ actually writes — the spec says a separate
    RULER record follows, but the real emitter omits the tag byte.

    ``stops`` is a list of ``(type, offset)`` tab stops. Defaults to a
    single left-aligned stop at offset 0 so the body is well-formed.
    """
    if stops is None:
        stops = [(0, 0)]
    payload = bytearray([0x01, 0x02])
    payload.append(len(stops) & 0xFF)
    for stop_type, offset in stops:
        payload.append(stop_type & 0xFF)
        payload.extend(u16_le(offset & 0xFFFF))
    payload.extend(body)
    payload.append(0x00)  # END
    return bytes(payload)


# ---------------------------------------------------------------------------
# v3 helpers
# ---------------------------------------------------------------------------


def v3_prelude() -> bytes:
    """5-byte v3 prelude: version 3, Windows, MathType, 3.5."""
    return bytes([3, 1, 0, 3, 5])


def v3_end() -> bytes:
    return bytes([0x00])


def _tag(rec: int, opts: int) -> int:
    return (opts << 4) | (rec & 0x0F)


def v3_line(body: bytes, *, opts: int = 0x0) -> bytes:
    """v3 LINE: tag byte (low=1, high=opts) + body + END."""
    return bytes([_tag(0x01, opts)]) + body + v3_end()


def v3_null_line() -> bytes:
    """v3 LINE with xfNULL — no body, no END."""
    return bytes([_tag(0x01, 0x01)])


def v3_char(char: int, *, typeface: int = 128, embells: list[int] | None = None) -> bytes:
    """v3 CHAR record. typeface is raw biased byte (default 128 = 0).

    Embellishment list, when present, is terminated by an END record.
    """
    opts = 0x02 if embells else 0x00
    out = bytearray([_tag(0x02, opts), typeface])
    out.extend(u16_le(char))
    if embells:
        for e in embells:
            out.extend(bytes([_tag(0x06, 0), e]))
        out.append(0x00)
    return bytes(out)


def v3_tmpl(
    selector: int,
    slots: list[bytes],
    *,
    variation: int = 0,
    tmpl_options: int = 0,
) -> bytes:
    """v3 TMPL: tag + selector + variation + options + slots + END."""
    out = bytearray([_tag(0x03, 0), selector, variation, tmpl_options])
    for s in slots:
        out.extend(s)
    out.append(0x00)
    return bytes(out)


def v3_matrix(
    rows: int,
    cols: int,
    cells: list[bytes],
) -> bytes:
    row_parts = bytes((rows + 1 + 3) // 4)
    col_parts = bytes((cols + 1 + 3) // 4)
    out = bytearray([_tag(0x05, 0), 0, 0, 0, rows, cols])
    out.extend(row_parts)
    out.extend(col_parts)
    for c in cells:
        out.extend(c)
    out.append(0x00)
    return bytes(out)


def v3_simple_char_line(char: int) -> bytes:
    return v3_line(v3_char(char))
