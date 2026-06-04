"""M3.3 barline: bar styles (Par. 1.10) + repeats (Table 17) + volta
endings (Par. 17.2).

A single ``<barline>`` can carry an inline ``<repeat>``, an
``<ending>``, and its own ``<bar-style>`` — each handled by an
independent helper; emit order is repeat → volta → bar style.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.music.context import MusicBrailleContext
from brailix.backend.music.utils import emit_cells_for_entity, first_child_text
from brailix.ir.braille import BrailleCell


def _emit_barline(
    cells: list[BrailleCell], mctx: MusicBrailleContext, elem: ET.Element
) -> None:
    """Process one ``<barline>`` element.

    Sub-elements emit independently — a single barline can carry both
    a ``<repeat>`` and an ``<ending>`` plus its own ``<bar-style>``,
    and each has its own cell mapping (Table 17 repeats are
    independent of the visual bar style).

    Emit order: repeats first, then volta marker, then the bar style.
    This matches BANA's notational convention of placing the volta
    indicator at a section start and the repeat sign at a section
    end; mixed cases are rare and the order within a single barline
    is not load-bearing in practice.
    """
    repeat = elem.find("repeat")
    if repeat is not None:
        _emit_repeat_sign(cells, mctx, repeat)

    ending = elem.find("ending")
    if ending is not None:
        _emit_volta(cells, mctx, ending)

    bar_style = first_child_text(elem, "bar-style") or "regular"
    _emit_bar_style(cells, mctx, bar_style)


def _emit_bar_style(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    bar_style: str,
) -> None:
    """BANA Par. 1.10 / 1.10.1 / 1.10.3: emit cells for the visual
    bar line style.

    Always-emit bar styles (semantic, not subject to the
    ``bar_line_print`` feature):

    * ``light-heavy`` → final double bar (``<k``)
    * ``light-light`` → sectional double bar (``<k'``)
    * ``dotted``      → print dotted bar line (``k``)
    * ``tick`` / ``short`` → bar line for unusual circumstances (``l``)

    For ``regular`` / ``none`` / unknown styles, the
    ``music.bar_line_print`` feature decides:

    * ``"skip"`` (default, BANA convention) → no cells
    * ``"dotted"`` → emit dotted bar line
    * ``"space"`` → emit one blank cell (Par. 1.10 literal)
    """
    style = bar_style.strip().lower()
    semantic = _BAR_STYLE_ENTITY.get(style)
    if semantic is not None:
        emit_cells_for_entity(
            cells, mctx,
            topic="general", entity=semantic,
            role="music_bar_line",
            source_text=f"bar:{style}",
        )
        return

    if style == "none":
        return

    mode = mctx.profile.feature("music.bar_line_print", "skip")
    if mode == "skip":
        return
    if mode == "dotted":
        emit_cells_for_entity(
            cells, mctx,
            topic="general", entity="print_dotted_bar_line",
            role="music_bar_line",
            source_text=f"bar:{style}",
        )
        return
    if mode == "space":
        # BANA literal: print bar line = ASCII space = empty cell, sourced
        # via the c_blank sentinel in general.json (kept on the resource
        # path like every other music cell, not an inline dots=()).
        emit_cells_for_entity(
            cells, mctx,
            topic="general", entity="print_bar_space",
            role="music_bar_line",
            source_text=f"bar:{style}",
        )
        return
    mctx.backend.warnings.warn(
        code="MUSIC_UNSUPPORTED_NOTATION",
        message=(
            f"music.bar_line_print={mode!r} not implemented "
            f"(M3.3 covers 'skip' / 'dotted' / 'space'); skipped"
        ),
        source="backend.music",
    )


_BAR_STYLE_ENTITY: dict[str, str] = {
    "light-heavy":  "final_double_bar",
    "light-light":  "sectional_double_bar",
    "dotted":       "print_dotted_bar_line",
    "tick":         "bar_line_unusual",
    "short":        "bar_line_unusual",
}


def _emit_repeat_sign(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    repeat_elem: ET.Element,
) -> None:
    """BANA Table 17: emit the repeat indicator based on ``direction``.

    * ``"forward"`` (section start)  → ``double_bar_dots_after``  (``<7``)
    * ``"backward"`` (section end)   → ``double_bar_dots_before`` (``<2``)

    Honours the ``music.expand_repeats`` feature: when on, M3.3 warns
    that expansion isn't implemented yet and falls back to the
    cell-marker form. M3+ may replace this with real measure-level
    expansion at the part / score layer.
    """
    if mctx.profile.feature("music.expand_repeats", False):
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                "music.expand_repeats=true not implemented (M3.3 covers "
                "marker form only); falling back to braille repeat sign"
            ),
            source="backend.music",
        )
    direction = repeat_elem.attrib.get("direction", "backward").strip().lower()
    if direction == "forward":
        entity = "double_bar_dots_after"
    elif direction == "backward":
        entity = "double_bar_dots_before"
    else:
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=f"unknown repeat direction {direction!r}",
            source="backend.music",
        )
        return
    emit_cells_for_entity(
        cells, mctx,
        topic="print_repeats", entity=entity,
        role="music_repeat",
        source_text=f"repeat:{direction}",
    )


def _emit_volta(
    cells: list[BrailleCell],
    mctx: MusicBrailleContext,
    ending_elem: ET.Element,
) -> None:
    """BANA Par. 17.2: emit a volta indicator at the ``start`` of an
    ending. ``stop`` / ``discontinue`` types produce no cells (BANA
    relies on the next bar line / new ending to terminate the volta).

    Number mapping (M3.3 minimum):

    * ``"1"`` → ``prima_volta``  (``#1``)
    * ``"2"`` → ``seconda_volta`` (``#2``)
    * anything else → warn ``MUSIC_UNSUPPORTED_NOTATION``; the 3+ /
      combined ``"1,2"`` forms aren't pre-built as named entries.

    The ``music.volta_style`` feature is reserved for a future
    ``"letter"`` (a / b) form per Table 17 alt; M3.3 only implements
    the numeric form.
    """
    ending_type = ending_elem.attrib.get("type", "start").strip().lower()
    if ending_type != "start":
        return

    style = mctx.profile.feature("music.volta_style", "numeric")
    if style != "numeric":
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"music.volta_style={style!r} not implemented "
                f"(M3.3 covers 'numeric' only); falling back"
            ),
            source="backend.music",
        )

    number = ending_elem.attrib.get("number", "1").strip()
    entity = _VOLTA_NUMBER_ENTITY.get(number)
    if entity is None:
        mctx.backend.warnings.warn(
            code="MUSIC_UNSUPPORTED_NOTATION",
            message=(
                f"volta ending number={number!r} not supported "
                f"(M3.3 covers '1' / '2'); skipped"
            ),
            source="backend.music",
        )
        return
    emit_cells_for_entity(
        cells, mctx,
        topic="print_repeats", entity=entity,
        role="music_volta",
        source_text=f"volta:{number}",
    )


_VOLTA_NUMBER_ENTITY: dict[str, str] = {
    "1": "prima_volta",
    "2": "seconda_volta",
}


_DISPATCH_PARTIAL = {
    # M3.3: barline handler — handles bar-style + <repeat> + <ending>.
    "barline": _emit_barline,
}
