"""Music layout schemes — the BANA 2015 layout *formats* as a registry
of strategies.

A score's cells can be arranged on the page in more than one way; BANA
2015 defines distinct **formats**, and which one a transcription uses is
an editorial choice.  Rather than branch on the format inside the
renderer, each format is a :class:`MusicLayoutScheme` registered by
name; :class:`brailix.renderer.layout.LayoutRenderer` looks the active
scheme up by :attr:`LayoutOptions.music_scheme` and delegates (registry,
not ``if/else`` — see ``ARCHITECTURE.md`` §7 and
``ARCHITECTURE.md``).

Schemes (BANA 2015):

* ``single_line`` — **§24.1** instrumental solo / single melodic part.
  Measures run on and wrap only at measure boundaries; the first line of
  a segment begins at the margin, run-over lines indent to the third
  cell (§24.1.1).  Implemented here.
* ``bar_over_bar`` — **§28 / §29 / §33** keyboard / ensemble.  Parts are
  stacked into *parallels* with measures aligned vertically.
  Implemented here as :class:`BarOverBarScheme` (first increment — see
  its docstring for the deliberate omissions), splitting on the
  backend's ``music_part_sep`` / ``music_measure_sep`` boundary cells.
* ``line_by_line`` — **§35** vocal music: a word (lyric) line paired with
  the music line below it.  Needs lyric pairing, not registered yet.

The orthogonal page knobs (``line_width`` = cells per line,
``page_height`` = lines per page) are *not* part of the scheme — they
apply within whatever format is chosen.

Formats in **Part IV** of the code (line-over-line, section-by-section,
vertical, bar-by-bar, substitution, note-for-note) are marked "not in
use by BANA" and are intentionally not offered.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from brailix.ir.braille import BLANK_CELL, BrailleCell

if TYPE_CHECKING:
    from brailix.renderer.layout import LayoutOptions


class MusicLayoutScheme(Protocol):
    """A BANA layout format: turn a music block's cell stream into
    laid-out display lines (lists of cells)."""

    @property
    def name(self) -> str: ...

    def lay_out(
        self, cells: list[BrailleCell], options: LayoutOptions
    ) -> list[list[BrailleCell]]: ...


_SCHEMES: dict[str, MusicLayoutScheme] = {}
_FALLBACK = "single_line"


def register_scheme(scheme: MusicLayoutScheme) -> None:
    """Register (or replace) a scheme under its :attr:`name`."""
    _SCHEMES[scheme.name] = scheme


def get_scheme(name: str | None) -> MusicLayoutScheme:
    """Look up a scheme by name, falling back to ``single_line`` for an
    unknown / unimplemented name (e.g. ``line_by_line``, or a scheme
    from a newer version in an old install) so layout never hard-fails
    on a stale profile / setting."""
    scheme = _SCHEMES.get(name or _FALLBACK)
    if scheme is not None:
        return scheme
    return _SCHEMES[_FALLBACK]


def scheme_names() -> tuple[str, ...]:
    """Registered scheme names — what a settings dropdown can offer."""
    return tuple(_SCHEMES)


# ---------------------------------------------------------------------------
# Shared measure-aware wrap (used by single_line; reusable by future schemes)
# ---------------------------------------------------------------------------


def wrap_measures(
    cells: list[BrailleCell],
    options: LayoutOptions,
    *,
    first_indent: int,
    cont_indent: int,
) -> list[list[BrailleCell]]:
    """Wrap a music cell stream, breaking **only** at measure boundaries.

    The music backend separates measures with a single blank
    ``music_measure_sep`` cell (role in
    ``options.measure_break_roles``).  Measures stay indivisible: BANA
    Pars. 11 / 17 forbid splitting an in-accord or repeat sequence across
    a line, so we never break mid-measure and never emit a continuation
    hyphen.  A measure wider than the whole line is placed on its own
    line and allowed to run over — there is no BANA-valid place to break
    it, and the user sees the overflow and restructures the source.

    Adjacent measures sharing a line are joined by the original separator
    cell (provenance preserved); a separator consumed by a line break is
    dropped, like the blank between two words.  ``first_indent`` blank
    cells lead the first line; ``cont_indent`` every continuation line.
    """
    if not cells:
        return []
    if options.line_width <= 0:
        # Defensive — a non-positive width would loop / mis-place.
        return [list(cells)]
    break_roles = options.measure_break_roles

    # Split into measures, remembering the separator cell between each
    # adjacent pair so its source span survives when the two measures it
    # joins land on the same line.
    measures: list[list[BrailleCell]] = [[]]
    seps: list[BrailleCell] = []
    for cell in cells:
        if cell.role in break_roles:
            seps.append(cell)
            measures.append([])
        else:
            measures[-1].append(cell)

    lines: list[list[BrailleCell]] = []
    cur: list[BrailleCell] = [BLANK_CELL] * first_indent
    cur_indent = first_indent

    def flush() -> None:
        nonlocal cur, cur_indent
        while cur and cur[-1].is_blank:
            cur.pop()
        lines.append(cur)
        cur = [BLANK_CELL] * cont_indent
        cur_indent = cont_indent

    for i, measure in enumerate(measures):
        if not measure:
            # Defensive: empty run from a stray / leading separator.
            continue
        if len(cur) > cur_indent:
            # A measure already sits on this line — try to append the
            # next one after its separator blank.
            sep = seps[i - 1] if 0 < i <= len(seps) else BLANK_CELL
            if len(cur) + 1 + len(measure) <= options.line_width:
                cur.append(sep)
                cur.extend(measure)
                continue
            flush()  # doesn't fit — break here (separator dropped)
        # Fresh line: place the measure.  If it overflows on its own it
        # cannot be broken (BANA) — let it run over.
        cur.extend(measure)
    if len(cur) > cur_indent or not lines:
        flush()
    return lines


# ---------------------------------------------------------------------------
# single_line — BANA §24.1
# ---------------------------------------------------------------------------

# §24.1.1: a segment's first line begins at the margin (the measure
# number, a space, then the music); succeeding lines are indented to the
# third cell.  We model that as a hanging indent — first line flush at
# cell 1, run-over lines at cell 3 (two leading blank cells).
#
# Not yet modelled (need backend support / editorial heuristics, tracked
# in ARCHITECTURE.md): splitting a part into phrase-based *segments*
# and printing each segment's opening measure number at the margin.
_SINGLE_LINE_FIRST_INDENT = 0
_SINGLE_LINE_RUNOVER_INDENT = 2


@dataclass(frozen=True, slots=True)
class SingleLineScheme:
    """BANA §24.1 single-line format for a single melodic part."""

    name: str = "single_line"

    def lay_out(
        self, cells: list[BrailleCell], options: LayoutOptions
    ) -> list[list[BrailleCell]]:
        return wrap_measures(
            cells,
            options,
            first_indent=_SINGLE_LINE_FIRST_INDENT,
            cont_indent=_SINGLE_LINE_RUNOVER_INDENT,
        )


register_scheme(SingleLineScheme())


# ---------------------------------------------------------------------------
# bar_over_bar — BANA §28.1
# ---------------------------------------------------------------------------


def _split_on_role(
    cells: list[BrailleCell], role: str
) -> list[list[BrailleCell]]:
    """Split a cell run on boundary cells of ``role`` (markers dropped)."""
    segments: list[list[BrailleCell]] = [[]]
    for cell in cells:
        if cell.role == role:
            segments.append([])
        else:
            segments[-1].append(cell)
    return segments


@dataclass(frozen=True, slots=True)
class BarOverBarScheme:
    """BANA §28.1 bar-over-bar: stack parts into measure-aligned parallels.

    Splits the stream into parts (on ``music_part_sep``) and each part
    into measures (on ``music_measure_sep``), groups consecutive measures
    into *parallels* that fit the line, and emits one line per part per
    parallel.  Within a parallel, each measure index is padded to the
    widest part so measure starts align vertically across parts (§28.1:
    "the first music elements of all of the parts in each measure are
    aligned vertically").  Parallels are separated by a blank line.

    First increment — deliberately omits (tracked in
    ``ARCHITECTURE.md``): the per-part hand-sign / name prefix
    (needs Table 25 + ``<staff>`` splitting), dividing a measure between
    parallels, run-over lines, and in-accord handling inside a parallel.
    """

    name: str = "bar_over_bar"

    def lay_out(
        self, cells: list[BrailleCell], options: LayoutOptions
    ) -> list[list[BrailleCell]]:
        if not cells:
            return []
        parts = _split_on_role(cells, "music_part_sep")
        part_measures = [
            _split_on_role(part, "music_measure_sep") for part in parts
        ]
        n = max((len(pm) for pm in part_measures), default=0)
        if n == 0:
            return []
        # Pad short parts so every part has the same measure count — a
        # well-formed multi-part score already agrees; padding keeps a
        # malformed one from mis-aligning the rest.
        for pm in part_measures:
            if len(pm) < n:
                pm.extend([] for _ in range(n - len(pm)))
        # Column width per measure index = widest part for that measure.
        col_w = [max(len(pm[k]) for pm in part_measures) for k in range(n)]

        # Group measures into parallels that fit ``line_width``.
        groups: list[tuple[int, int]] = []
        if options.line_width <= 0:
            groups = [(0, n)]
        else:
            k = 0
            while k < n:
                w = col_w[k]
                end = k + 1
                while end < n and w + 1 + col_w[end] <= options.line_width:
                    w += 1 + col_w[end]
                    end += 1
                groups.append((k, end))
                k = end

        lines: list[list[BrailleCell]] = []
        for gi, (start, end) in enumerate(groups):
            if gi > 0:
                lines.append([BLANK_CELL])  # blank line between parallels
            for pm in part_measures:
                line: list[BrailleCell] = []
                for k in range(start, end):
                    if k > start:
                        line.append(BLANK_CELL)  # space between measures
                    measure = pm[k]
                    line.extend(measure)
                    pad = col_w[k] - len(measure)
                    if pad > 0:
                        line.extend([BLANK_CELL] * pad)
                # Trailing pad on the last measure is never needed.
                while line and line[-1].is_blank:
                    line.pop()
                lines.append(line)
        return lines


register_scheme(BarOverBarScheme())


__all__ = (
    "BarOverBarScheme",
    "MusicLayoutScheme",
    "SingleLineScheme",
    "get_scheme",
    "register_scheme",
    "scheme_names",
    "wrap_measures",
)
