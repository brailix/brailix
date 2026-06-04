"""MTEF v3 / v4 (Microsoft Equation 3.0 / older MathType) parsing.

In v3 the record tag byte carries the record type in its low nibble and
the option flags in its high nibble (unlike v5, which uses two separate
bytes). CHAR records use a typeface byte plus a 16-bit character value.
This module owns the v3 reader walk and delegates MathML construction to
:mod:`brailix.frontend.math.adapters.mtef._mathml`.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.frontend.math.adapters.mtef._mathml import (
    _attach_preceding_base,
    _build_tmpl,
    _char_to_mathml,
)
from brailix.frontend.math.adapters.mtef._reader import (
    _MATHML_NS,
    _REC_CHAR,
    _REC_EMBELL,
    _REC_END,
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


def _convert_v3(data: bytes) -> str:
    r = _Reader(data)
    _read_v3_prelude(r)
    children: list[ET.Element] = []
    _read_object_list_v3(r, children, depth=0)
    math = ET.Element("math", {"xmlns": _MATHML_NS})
    for c in children:
        math.append(c)
    return ET.tostring(math, encoding="unicode")


def _read_v3_prelude(r: _Reader) -> None:
    version = r.u8()
    if version not in (2, 3, 4):
        raise _MtefParseError(f"expected v2/3/4 prelude, got version {version}")
    r.u8()  # platform
    r.u8()  # product
    r.u8()  # product_version
    r.u8()  # product_subversion


def _read_object_list_v3(
    r: _Reader, sink: list[ET.Element], *, depth: int
) -> None:
    if depth > 64:
        raise _MtefParseError("MTEF nesting too deep")
    while r.remaining() > 0:
        tag = r.u8()
        rec = tag & 0x0F
        opts = (tag >> 4) & 0x0F
        if rec == _REC_END:
            return
        if rec == _REC_LINE:
            sink.extend(_read_line_v3(r, opts, depth + 1))
        elif rec == _REC_CHAR:
            sink.extend(_read_char_v3(r, opts, depth + 1))
        elif rec == _REC_TMPL:
            built = _read_tmpl_v3(r, opts, depth + 1)
            _attach_preceding_base(sink, built)
            sink.extend(built)
        elif rec == _REC_PILE:
            sink.append(_read_pile_v3(r, opts, depth + 1))
        elif rec == _REC_MATRIX:
            sink.append(_read_matrix_v3(r, opts, depth + 1))
        elif rec == _REC_EMBELL:
            if opts & 0x08:
                _read_nudge_v3(r)
            r.u8()
        elif rec == _REC_RULER:
            _skip_ruler(r)
        elif rec == _REC_FONT_OR_STYLE_DEF:
            # v3 FONT record: typeface + style + null-terminated name.
            r.u8()
            r.u8()
            r.nstr()
        elif rec == _REC_SIZE:
            _skip_size_v3(r)
        elif _REC_FULL <= rec <= _REC_SUBSYM:
            pass
        else:
            raise _MtefParseError(f"unknown v3 record type 0x{rec:02x}")


def _read_line_v3(
    r: _Reader, opts: int, depth: int
) -> list[ET.Element]:
    """v3 LINE — flags bit 0x1=null, 0x2=ruler, 0x4=lspace, 0x8=nudge."""
    if opts & 0x08:
        _read_nudge_v3(r)
    if opts & 0x04:
        r.u8()  # line spacing (1 byte in v3)
    if opts & 0x02:
        _expect_v3(r, _REC_RULER)
        _skip_ruler(r)
    if opts & 0x01:
        return []
    children: list[ET.Element] = []
    _read_object_list_v3(r, children, depth=depth)
    return children


def _read_char_v3(
    r: _Reader, opts: int, depth: int
) -> list[ET.Element]:
    """v3 CHAR — typeface + 16-bit char value + optional embell list."""
    if opts & 0x08:
        _read_nudge_v3(r)
    r.u8()  # typeface (biased +128)
    char = r.u16()
    embell_list: list[int] = []
    if opts & 0x02:
        _collect_embell_v3(r, embell_list, depth + 1)
    return _char_to_mathml(char, embell_list)


def _collect_embell_v3(
    r: _Reader, embell_list: list[int], depth: int
) -> None:
    """Read EMBELL records (v3 layout) until END."""
    if depth > 64:
        raise _MtefParseError("MTEF embell nesting too deep")
    while r.remaining() > 0:
        tag = r.u8()
        rec = tag & 0x0F
        opts = (tag >> 4) & 0x0F
        if rec == _REC_END:
            return
        if rec == _REC_EMBELL:
            if opts & 0x08:
                _read_nudge_v3(r)
            embell_list.append(r.u8())
        elif rec == _REC_SIZE:
            _skip_size_v3(r)
        elif _REC_FULL <= rec <= _REC_SUBSYM:
            pass
        elif rec == _REC_FONT_OR_STYLE_DEF:
            r.u8()
            r.u8()
            r.nstr()
        else:
            raise _MtefParseError(
                f"unexpected record 0x{rec:02x} in v3 embell list"
            )


def _read_tmpl_v3(
    r: _Reader, opts: int, depth: int
) -> list[ET.Element]:
    """v3 TMPL — selector + variation + options + slot LINE records."""
    if opts & 0x08:
        _read_nudge_v3(r)
    selector = r.u8()
    variation = r.u8()
    r.u8()  # template-specific options
    slots: list[list[ET.Element]] = []
    _read_tmpl_slots_v3(r, slots, depth + 1)
    return _build_tmpl(selector, variation, slots, version=3)


def _read_tmpl_slots_v3(
    r: _Reader,
    slots: list[list[ET.Element]],
    depth: int,
) -> None:
    if depth > 64:
        raise _MtefParseError("MTEF tmpl nesting too deep")
    while r.remaining() > 0:
        tag = r.u8()
        rec = tag & 0x0F
        opts = (tag >> 4) & 0x0F
        if rec == _REC_END:
            return
        if rec == _REC_LINE:
            slots.append(_read_line_v3(r, opts, depth))
        elif rec == _REC_CHAR:
            slots.append(_read_char_v3(r, opts, depth))
        elif rec == _REC_TMPL:
            slots.append(_read_tmpl_v3(r, opts, depth))
        elif rec == _REC_PILE:
            slots.append([_read_pile_v3(r, opts, depth)])
        elif rec == _REC_MATRIX:
            slots.append([_read_matrix_v3(r, opts, depth)])
        elif rec == _REC_SIZE:
            _skip_size_v3(r)
        elif _REC_FULL <= rec <= _REC_SUBSYM:
            pass
        elif rec == _REC_FONT_OR_STYLE_DEF:
            r.u8()
            r.u8()
            r.nstr()
        else:
            raise _MtefParseError(
                f"unexpected record 0x{rec:02x} in v3 tmpl slot list"
            )


def _read_pile_v3(r: _Reader, opts: int, depth: int) -> ET.Element:
    if opts & 0x08:
        _read_nudge_v3(r)
    r.u8()  # halign
    r.u8()  # valign
    if opts & 0x02:
        _expect_v3(r, _REC_RULER)
        _skip_ruler(r)
    mtable = ET.Element("mtable")
    while r.remaining() > 0:
        tag = r.u8()
        rec = tag & 0x0F
        opts2 = (tag >> 4) & 0x0F
        if rec == _REC_END:
            break
        if rec == _REC_LINE:
            row_children = _read_line_v3(r, opts2, depth + 1)
            mtr = ET.Element("mtr")
            mtd = ET.Element("mtd")
            for c in row_children:
                mtd.append(c)
            mtr.append(mtd)
            mtable.append(mtr)
        else:
            raise _MtefParseError(
                f"unexpected record 0x{rec:02x} in v3 pile"
            )
    return mtable


def _read_matrix_v3(r: _Reader, opts: int, depth: int) -> ET.Element:
    if opts & 0x08:
        _read_nudge_v3(r)
    r.u8()  # valign
    r.u8()  # h_just
    r.u8()  # v_just
    rows = r.u8()
    cols = r.u8()
    _skip_partitions(r, rows + 1)
    _skip_partitions(r, cols + 1)
    mtable = ET.Element("mtable")
    for _row in range(rows):
        mtr = ET.Element("mtr")
        for _col in range(cols):
            tag = r.u8()
            rec = tag & 0x0F
            opts2 = (tag >> 4) & 0x0F
            if rec == _REC_END:
                mtd = ET.Element("mtd")
                mtr.append(mtd)
                continue
            if rec != _REC_LINE:
                raise _MtefParseError(
                    f"expected LINE in v3 matrix cell, got 0x{rec:02x}"
                )
            cell_children = _read_line_v3(r, opts2, depth + 1)
            mtd = ET.Element("mtd")
            for c in cell_children:
                mtd.append(c)
            mtr.append(mtd)
        mtable.append(mtr)
    if r.remaining() > 0 and r.peek() == _REC_END:
        r.u8()
    return mtable


def _read_nudge_v3(r: _Reader) -> None:
    dx = r.u8()
    dy = r.u8()
    if dx == 128 and dy == 128:
        r.i16()
        r.i16()


def _skip_size_v3(r: _Reader) -> None:
    b = r.u8()
    if b == 101:
        r.u16()  # -lsize
    elif b == 100:
        r.u8()
        r.i16()
    else:
        r.u8()  # dsize+128


def _expect_v3(r: _Reader, rec: int) -> None:
    tag = r.u8()
    actual = tag & 0x0F
    if actual != rec:
        raise _MtefParseError(
            f"expected v3 record 0x{rec:02x}, got 0x{actual:02x}"
        )
