"""MTEF v5 (MathType 4+) parsing.

v5 separates the record type and option bytes (unlike v3, which packs
them into one tag nibble pair), uses the extended variable-length integer
encoding for definition-record indices, and carries CHAR records as an
MTCode value with optional 8/16-bit font positions. The byte layout is
fiddly; this module owns the v5 reader walk and delegates MathML
construction to :mod:`brailix.frontend.math.adapters.mtef._mathml`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable

from brailix.frontend.math.adapters.mtef._mathml import (
    _attach_preceding_base,
    _build_tmpl,
    _char_to_mathml,
)
from brailix.frontend.math.adapters.mtef._reader import (
    _MATHML_NS,
    _REC_CHAR,
    _REC_COLOR,
    _REC_COLOR_DEF,
    _REC_EMBELL,
    _REC_ENCODING_DEF,
    _REC_END,
    _REC_EQN_PREFS,
    _REC_FONT_DEF,
    _REC_FONT_OR_STYLE_DEF,
    _REC_FULL,
    _REC_LINE,
    _REC_MATRIX,
    _REC_PILE,
    _REC_RULER,
    _REC_SIZE,
    _REC_SUBSYM,
    _REC_TMPL,
    _MtefParseError,
    _Reader,
    _skip_partitions,
    _skip_ruler,
)


def _convert_v5(data: bytes) -> str:
    r = _Reader(data)
    _read_v5_prelude(r)
    children: list[ET.Element] = []
    _read_object_list_v5(r, children, depth=0)
    math = ET.Element("math", {"xmlns": _MATHML_NS})
    for c in children:
        math.append(c)
    return ET.tostring(math, encoding="unicode")


def _read_v5_prelude(r: _Reader) -> None:
    version = r.u8()
    if version != 5:
        raise _MtefParseError(f"expected v5 prelude, got version {version}")
    r.u8()  # platform
    r.u8()  # product
    r.u8()  # product_version
    r.u8()  # product_subversion
    r.nstr()  # app key — e.g. "DSMT7"
    r.u8()  # equation options


def _read_object_list_v5(
    r: _Reader, sink: list[ET.Element], *, depth: int
) -> None:
    """Read records until END or EOF, appending MathML children to ``sink``.

    Definition records (FONT_STYLE_DEF, COLOR_DEF, FONT_DEF, EQN_PREFS,
    ENCODING_DEF) and styling records (SIZE, COLOR, FULL..SUBSYM) emit
    nothing — braille rendering is style-agnostic.
    """
    if depth > 64:
        raise _MtefParseError("MTEF nesting too deep")
    while r.remaining() > 0:
        rec = r.u8()
        if rec == _REC_END:
            return
        if rec == _REC_LINE:
            opts = r.u8()
            elems = _read_line_v5(r, opts, depth + 1)
            sink.extend(elems)
        elif rec == _REC_CHAR:
            opts = r.u8()
            sink.extend(_read_char_v5(r, opts, depth + 1))
        elif rec == _REC_TMPL:
            opts = r.u8()
            built = _read_tmpl_v5(r, opts, depth + 1)
            _attach_preceding_base(sink, built)
            sink.extend(built)
        elif rec == _REC_PILE:
            opts = r.u8()
            sink.append(_read_pile_v5(r, opts, depth + 1))
        elif rec == _REC_MATRIX:
            opts = r.u8()
            sink.append(_read_matrix_v5(r, opts, depth + 1))
        elif rec == _REC_EMBELL:
            # Stray embellishment outside a CHAR: skip.
            r.u8()
            r.u8()
        elif rec == _REC_RULER:
            _skip_ruler(r)
        elif rec == _REC_FONT_OR_STYLE_DEF:
            # FONT_STYLE_DEF in v5
            r.u_extended()  # font_def_index
            r.u8()  # char_style
        elif rec == _REC_SIZE:
            _skip_size_v5(r)
        elif _REC_FULL <= rec <= _REC_SUBSYM:
            # Typesize shorthand: tag byte only.
            pass
        elif rec == _REC_COLOR:
            r.u_extended()
        elif rec == _REC_COLOR_DEF:
            _skip_color_def_v5(r)
        elif rec == _REC_FONT_DEF:
            r.u_extended()
            r.nstr()
        elif rec == _REC_EQN_PREFS:
            _skip_eqn_prefs_v5(r)
        elif rec == _REC_ENCODING_DEF:
            r.nstr()
        elif rec >= 0x64:
            length = r.u_extended()
            r.pos = min(r.pos + length, len(r.data))
        else:
            raise _MtefParseError(f"unknown v5 record type 0x{rec:02x}")


def _read_line_v5(
    r: _Reader, opts: int, depth: int
) -> list[ET.Element]:
    """v5 LINE — flags bit 0x01=null, 0x02=ruler, 0x04=lspace, 0x08=nudge."""
    if opts & 0x08:
        _read_nudge_v5(r)
    if opts & 0x04:
        r.u16()  # line spacing
    if opts & 0x01:
        # Null line — no object list. Still consume the ruler if flagged.
        if opts & 0x02:
            _consume_ruler_v5(r)
        return []

    def _body() -> list[ET.Element]:
        children: list[ET.Element] = []
        _read_object_list_v5(r, children, depth=depth)
        return children

    if opts & 0x02:
        return _read_ruler_then_v5(r, _body)
    return _body()


def _read_char_v5(r: _Reader, opts: int, depth: int) -> list[ET.Element]:
    """v5 CHAR — typeface + MTCode (and/or font positions) + optional embell."""
    if opts & 0x08:
        _read_nudge_v5(r)
    r.s_extended()  # typeface (signed, biased+128 in 1-byte form)
    mtcode: int | None = None
    if not (opts & 0x20):
        mtcode = r.u16()
    if opts & 0x04:
        r.u8()  # 8-bit font position
    if opts & 0x10:
        r.u16()  # 16-bit font position
    embell_list: list[int] = []
    if opts & 0x01:
        _collect_embell_v5(r, embell_list, depth + 1)
    return _char_to_mathml(mtcode, embell_list)


def _collect_embell_v5(
    r: _Reader, embell_list: list[int], depth: int
) -> None:
    """Read a sequence of EMBELL records terminated by END."""
    if depth > 64:
        raise _MtefParseError("MTEF embell nesting too deep")
    while r.remaining() > 0:
        rec = r.u8()
        if rec == _REC_END:
            return
        if rec == _REC_EMBELL:
            opts = r.u8()
            if opts & 0x08:
                _read_nudge_v5(r)
            embell_list.append(r.u8())
        else:
            # Style records can appear inside an embell list — skip
            # back the tag and run the main dispatcher with a throwaway
            # sink, then keep going. Simpler: skip 1 byte. The common
            # case is a SIZE followed by EMBELL.
            if rec == _REC_SIZE:
                _skip_size_v5(r)
            elif rec == _REC_COLOR:
                r.u_extended()
            elif _REC_FULL <= rec <= _REC_SUBSYM:
                pass
            elif rec == _REC_FONT_OR_STYLE_DEF:
                r.u_extended()
                r.u8()
            else:
                raise _MtefParseError(
                    f"unexpected record 0x{rec:02x} in v5 embell list"
                )


def _read_tmpl_v5(
    r: _Reader, opts: int, depth: int
) -> list[ET.Element]:
    """v5 TMPL — selector + variation + options + slot LINE/CHAR records."""
    if opts & 0x08:
        _read_nudge_v5(r)
    selector = r.u8()
    variation = r.u8()
    if variation & 0x80:
        # The high bit on variation means "another variation byte
        # follows" (v5 extension for templates with more than 256
        # variations). Read it but otherwise ignore — our handlers
        # branch on the low byte only.
        r.u8()
    r.u8()  # template-specific options
    slots: list[list[ET.Element]] = []
    _read_tmpl_slots_v5(r, slots, depth + 1)
    return _build_tmpl(selector, variation & 0x7F, slots, version=5)


def _read_tmpl_slots_v5(
    r: _Reader,
    slots: list[list[ET.Element]],
    depth: int,
) -> None:
    """Read TMPL subobject list — sequence of LINE records ending in END.

    Each LINE record becomes one "slot" (e.g. numerator + denominator
    for a fraction). v5 also allows a bare CHAR inside a TMPL slot
    list; we treat it as a single-element slot.
    """
    if depth > 64:
        raise _MtefParseError("MTEF tmpl nesting too deep")
    while r.remaining() > 0:
        rec = r.u8()
        if rec == _REC_END:
            return
        if rec == _REC_LINE:
            opts = r.u8()
            slots.append(_read_line_v5(r, opts, depth))
        elif rec == _REC_CHAR:
            opts = r.u8()
            slots.append(_read_char_v5(r, opts, depth))
        elif rec == _REC_TMPL:
            opts = r.u8()
            slots.append(_read_tmpl_v5(r, opts, depth))
        elif rec == _REC_PILE:
            opts = r.u8()
            slots.append([_read_pile_v5(r, opts, depth)])
        elif rec == _REC_MATRIX:
            opts = r.u8()
            slots.append([_read_matrix_v5(r, opts, depth)])
        elif rec == _REC_SIZE:
            _skip_size_v5(r)
        elif rec == _REC_COLOR:
            r.u_extended()
        elif _REC_FULL <= rec <= _REC_SUBSYM:
            pass
        elif rec == _REC_FONT_OR_STYLE_DEF:
            r.u_extended()
            r.u8()
        elif rec == _REC_RULER:
            _skip_ruler(r)
        elif rec == _REC_COLOR_DEF:
            _skip_color_def_v5(r)
        elif rec == _REC_FONT_DEF:
            r.u_extended()
            r.nstr()
        elif rec == _REC_EQN_PREFS:
            _skip_eqn_prefs_v5(r)
        elif rec == _REC_ENCODING_DEF:
            r.nstr()
        else:
            raise _MtefParseError(
                f"unexpected record 0x{rec:02x} in v5 tmpl slot list"
            )


def _read_pile_v5(r: _Reader, opts: int, depth: int) -> ET.Element:
    """v5 PILE — vertical stack of LINE records → ``<mtable>``."""
    if opts & 0x08:
        _read_nudge_v5(r)
    r.u8()  # halign
    r.u8()  # valign

    def _rows() -> ET.Element:
        mtable = ET.Element("mtable")
        while r.remaining() > 0:
            rec = r.u8()
            if rec == _REC_END:
                break
            if rec == _REC_LINE:
                line_opts = r.u8()
                row_children = _read_line_v5(r, line_opts, depth + 1)
                mtr = ET.Element("mtr")
                mtd = ET.Element("mtd")
                for c in row_children:
                    mtd.append(c)
                mtr.append(mtd)
                mtable.append(mtr)
            else:
                raise _MtefParseError(
                    f"unexpected record 0x{rec:02x} in v5 pile"
                )
        return mtable

    if opts & 0x02:
        return _read_ruler_then_v5(r, _rows)
    return _rows()


def _read_matrix_v5(r: _Reader, opts: int, depth: int) -> ET.Element:
    """v5 MATRIX → ``<mtable>`` of ``<mtr>``/``<mtd>``."""
    if opts & 0x08:
        _read_nudge_v5(r)
    r.u8()  # valign
    r.u8()  # h_just
    r.u8()  # v_just
    rows = r.u8()
    cols = r.u8()
    # row partitions: 2-bit values per (rows+1) line, rounded up.
    _skip_partitions(r, rows + 1)
    _skip_partitions(r, cols + 1)
    mtable = ET.Element("mtable")
    for _row in range(rows):
        mtr = ET.Element("mtr")
        for _col in range(cols):
            # Each cell is one LINE record.
            rec = r.u8()
            if rec == _REC_END:
                # Truncated matrix — pad with empty cells.
                mtd = ET.Element("mtd")
                mtr.append(mtd)
                continue
            if rec != _REC_LINE:
                raise _MtefParseError(
                    f"expected LINE in matrix cell, got 0x{rec:02x}"
                )
            opts2 = r.u8()
            cell_children = _read_line_v5(r, opts2, depth + 1)
            mtd = ET.Element("mtd")
            for c in cell_children:
                mtd.append(c)
            mtr.append(mtd)
        mtable.append(mtr)
    # Consume terminating END.
    if r.remaining() > 0 and r.peek() == _REC_END:
        r.u8()
    return mtable


def _read_nudge_v5(r: _Reader) -> None:
    """v5 nudge: 2 bytes (small) or 6 bytes (large, leader = 128,128)."""
    dx = r.u8()
    dy = r.u8()
    if dx == 128 and dy == 128:
        r.i16()  # dx
        r.i16()  # dy


def _skip_size_v5(r: _Reader) -> None:
    """v5 SIZE payload — three encodings keyed by the first byte (WIRIS
    MTEF spec, lsize/dsize): 101 = explicit point size (16-bit); 100 =
    large delta (lsize typesize byte, then 16-bit dsize); otherwise the
    byte is the lsize typesize of a small delta (then dsize+128). Matches
    ``_skip_size_v3`` — v3 and v5 share this encoding.
    """
    b = r.u8()
    if b == 101:
        r.u16()  # -point_size
    elif b == 100:
        r.u8()  # lsize (typesize)
        r.i16()  # dsize
    else:
        r.u8()  # dsize+128


def _skip_color_def_v5(r: _Reader) -> None:
    opts = r.u8()
    components = 4 if (opts & 0x01) else 3
    for _ in range(components):
        r.u16()
    if opts & 0x04:
        r.nstr()


def _skip_eqn_prefs_v5(r: _Reader) -> None:
    """Skip the EQN_PREFS record (sizes + spaces + styles).

    Layout per the v5 spec:

    * opts (1 byte, always 0)
    * sizes: 1-byte count + ``count`` nibble streams (each = unit nibble
      + value nibbles + ``0xF`` terminator, packed high-nibble first)
    * spaces: same shape as sizes
    * styles: 1-byte count + ``count`` per-style entries, where each
      entry is an extended-uint font-def index, followed by a 1-byte
      character-style byte iff the index is nonzero

    Treating styles as a nibble stream (the old behaviour) consumed the
    entire rest of the buffer — the equation body then looked empty.
    """
    r.u8()  # opts byte (always 0 in v5)
    size_count = r.u8()
    _skip_nibble_streams(r, size_count)
    space_count = r.u8()
    _skip_nibble_streams(r, space_count)
    style_count = r.u8()
    for _ in range(style_count):
        if r.remaining() <= 0:
            return
        idx = r.u_extended()
        if idx != 0 and r.remaining() > 0:
            r.u8()  # character-style byte


def _skip_nibble_streams(r: _Reader, n: int) -> None:
    """Read ``n`` nibble streams, each terminated by a 0xF nibble."""
    remaining = n
    high_pending = False
    pending_nibble = 0
    while remaining > 0 and r.remaining() > 0:
        if high_pending:
            nib = pending_nibble
            high_pending = False
        else:
            b = r.u8()
            nib = b & 0x0F
            pending_nibble = (b >> 4) & 0x0F
            high_pending = True
        if nib == 0x0F:
            remaining -= 1
    # Discard any half-byte still buffered.


def _consume_ruler_v5(r: _Reader) -> None:
    """Consume a ruler payload when there is no following body to validate.

    Used for null lines, which carry a ruler but no object list to
    disambiguate against. The ruler is normally inline (``count`` +
    stops); a leading ``0x07`` is taken as the spec RULER tag. When an
    inline ``count`` is exactly 7 this is genuinely ambiguous, but a null
    line offers no signal to resolve it — non-null lines / piles use
    :func:`_read_ruler_then_v5` instead.
    """
    if r.remaining() > 0 and r.peek() == _REC_RULER:
        r.u8()  # consume RULER tag
    _skip_ruler(r)


def _read_ruler_then_v5[T](r: _Reader, body: Callable[[], T]) -> T:
    """Consume the opts=0x02 ruler, then parse the body via ``body``.

    The ruler is written either inline (``count`` + stops, what MathType
    6+ emits) or as a tagged RULER record (``0x07`` + ``count`` + stops,
    the WIRIS spec form). The two are byte-identical when the inline
    ``count`` equals the RULER tag value (``0x07`` == 7), so they cannot
    be distinguished locally. Resolve at runtime: try the inline reading
    first and fall back to the tagged reading when the body then fails to
    parse. (Previously a leading ``0x07`` was always taken as the tag,
    which silently dropped the body of any 7-stop inline ruler.)

    ``body`` must build and return a fresh result on each call — it is
    retried into a new structure when the inline attempt desyncs.
    """
    if r.remaining() > 0 and r.peek() == _REC_RULER:
        start = r.pos
        try:
            _skip_ruler(r)  # inline: the 0x07 is a stop count of 7
            return body()
        except _MtefParseError:
            r.pos = start
            r.u8()  # the 0x07 was the RULER tag after all
            _skip_ruler(r)
            return body()
    _skip_ruler(r)  # unambiguous inline count (first byte != 0x07)
    return body()
