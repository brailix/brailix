"""Property-based tests for the measure-aware music wrap (BANA §24.1).

``wrap_measures`` is the music counterpart of the text wrap, with a harder
rule: BANA Pars. 11 / 17 forbid splitting a measure across lines, ever.
Over generated measure streams (any measure sizes, stray / doubled
separators, custom break roles, any widths and indents) the wrap must
uphold:

* **conservation** — the non-blank output cells are exactly the non-blank
  input cells, in order, by object identity (nothing lost, duplicated,
  reordered — and nothing synthesized: music never takes a continuation
  hyphen);
* **measure atomicity** — a measure's cells all land on ONE line;
* **runover exemption only** — a line exceeds the width only when it
  holds a single measure that cannot fit any line by itself;
* **greedy fill** — a measure opens a new line only if it (plus its
  separator) genuinely didn't fit the previous one;
* **separator provenance** — measures joined on one line are joined by
  the ORIGINAL separator cell object, so its source span survives;
* **indents** — first line opens with ``first_indent`` blanks,
  continuations with ``cont_indent``.

Generated measures carry no internal blanks (the separator-identity
assertion needs an unambiguous joint); rule-value compositions and the
§24.1.1 margin constants stay example-tested in ``test_music_layout.py``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.ir.braille import BrailleCell
from brailix.renderer.layout import LayoutOptions
from brailix.renderer.music_layout import get_scheme, scheme_names, wrap_measures

_SEP_ROLE = "music_measure_sep"


@st.composite
def measure_streams(draw: st.DrawFn) -> tuple[list[BrailleCell], list[list[BrailleCell]], list[BrailleCell | None]]:
    """A music cell stream plus its ground truth.

    Returns ``(cells, measures, sep_before)`` where ``measures`` is the
    list of non-empty measures in order and ``sep_before[i]`` is the
    separator cell immediately preceding measure ``i`` in the stream
    (``None`` for a measure with no separator before it).
    """
    cells: list[BrailleCell] = []
    measures: list[list[BrailleCell]] = []
    sep_before: list[BrailleCell | None] = []
    last_sep: BrailleCell | None = None

    def emit_seps(count: int) -> None:
        nonlocal last_sep
        for _ in range(count):
            sep = BrailleCell(dots=(), role=_SEP_ROLE)
            cells.append(sep)
            last_sep = sep

    emit_seps(draw(st.integers(0, 2)))  # leading strays
    n_measures = draw(st.integers(0, 5))
    for index in range(n_measures):
        if index > 0:
            # Model measures MUST be separated in the stream, or the
            # wrap's own split would merge them into one; doubling a
            # separator exercises the stray-empty-measure path.
            emit_seps(draw(st.integers(1, 2)))
        measure = [
            BrailleCell(
                dots=tuple(draw(st.sets(st.integers(1, 6), min_size=1, max_size=3))),
                role=draw(st.sampled_from([None, "music_note"])),
            )
            for _ in range(draw(st.integers(1, 7)))
        ]
        cells.extend(measure)
        measures.append(measure)
        sep_before.append(last_sep)
        last_sep = None
    emit_seps(draw(st.integers(0, 1)))  # trailing stray
    return cells, measures, sep_before


class TestWrapMeasuresContract:
    @settings(max_examples=150)
    @given(
        stream=measure_streams(),
        width=st.integers(-1, 30),
        first_indent=st.integers(0, 3),
        cont_indent=st.integers(0, 3),
    )
    def test_invariants(
        self,
        stream: tuple,
        width: int,
        first_indent: int,
        cont_indent: int,
    ) -> None:
        cells, measures, sep_before = stream
        options = LayoutOptions(line_width=width)
        lines = wrap_measures(
            cells, options, first_indent=first_indent, cont_indent=cont_indent
        )

        if width <= 0 and cells:
            # Documented defensive branch: a non-positive width would loop /
            # mis-place, so the stream comes back as one untouched line.
            # (Mutation testing found this branch entirely unexercised.)
            assert lines == [list(cells)]
            return

        flat = [c for line in lines for c in line]
        content_in = [c for c in cells if not c.is_blank]

        # Conservation by identity — and nothing non-blank synthesized,
        # which is also the "no continuation hyphen" rule.
        assert [id(c) for c in flat if not c.is_blank] == [id(c) for c in content_in]

        if not cells:
            assert lines == []
            return
        if not content_in:
            # Separator-only stream: the degenerate single empty line the
            # framing guard upstream knows to drop.
            assert lines == [[]]
            return

        # No stray structure: every emitted line carries content — wrap
        # never manufactures interior or trailing blank lines (framing
        # blanks are the caller's job).
        assert all(any(not c.is_blank for c in line) for line in lines)

        line_of = {
            id(c): idx for idx, line in enumerate(lines) for c in line if not c.is_blank
        }
        placed_lines: list[int] = []
        for measure in measures:
            # Measure atomicity: one line holds the whole measure.
            m_lines = {line_of[id(c)] for c in measure}
            assert len(m_lines) == 1
            placed_lines.append(m_lines.pop())
        # Measures appear in order, top to bottom.
        assert placed_lines == sorted(placed_lines)

        for i in range(1, len(measures)):
            prev_line, cur_line = placed_lines[i - 1], placed_lines[i]
            if cur_line == prev_line:
                # Joined on one line: the joint is the ORIGINAL separator
                # object (source span preserved), sitting right between
                # the two measures.
                line = lines[cur_line]
                # Locate by identity, not equality: frozen cells compare
                # by value, and equal-valued cells are common.
                idx_prev_last = next(
                    k for k, c in enumerate(line) if c is measures[i - 1][-1]
                )
                idx_cur_first = next(
                    k for k, c in enumerate(line) if c is measures[i][0]
                )
                assert idx_cur_first == idx_prev_last + 2
                expected_sep = sep_before[i]
                if expected_sep is not None:
                    assert line[idx_prev_last + 1] is expected_sep
                else:
                    assert line[idx_prev_last + 1].is_blank
            else:
                # Greedy fill: it moved down only because it didn't fit.
                assert (
                    len(lines[prev_line]) + 1 + len(measures[i]) > width
                )

        for idx, line in enumerate(lines):
            has_content = any(not c.is_blank for c in line)
            if not has_content:
                continue
            indent = first_indent if idx == 0 else cont_indent
            # Indent prefix, then content immediately.
            assert all(c.is_blank for c in line[:indent])
            assert not line[indent].is_blank
            if len(line) > width:
                # Runover exemption: only a single measure that can't fit
                # any line by itself may exceed the width.
                on_this_line = [
                    m for m, ln in zip(measures, placed_lines, strict=True) if ln == idx
                ]
                assert len(on_this_line) == 1
                assert indent + len(on_this_line[0]) > width

    @settings(max_examples=60)
    @given(stream=measure_streams(), width=st.integers(1, 30))
    def test_custom_break_role_is_honoured(self, stream: tuple, width: int) -> None:
        # The break contract is carried by LayoutOptions.measure_break_roles,
        # not a hard-coded role string: rebind the role set and the same
        # stream must wrap by the NEW role — conservation and measure
        # atomicity intact under the rebound boundaries.
        cells, measures, _ = stream
        rebound = [
            BrailleCell(dots=(), role="custom_sep") if c.role == _SEP_ROLE else c
            for c in cells
        ]
        options = LayoutOptions(
            line_width=width, measure_break_roles=frozenset({"custom_sep"})
        )
        lines = wrap_measures(rebound, options, first_indent=0, cont_indent=2)
        line_of = {
            id(c): idx for idx, line in enumerate(lines) for c in line if not c.is_blank
        }
        assert [id(c) for line in lines for c in line if not c.is_blank] == [
            id(c) for c in rebound if not c.is_blank
        ]
        # Rebuild measures from the rebound stream and check atomicity.
        # Generated measure cells are all non-blank, so a non-empty run
        # maps to a non-empty set of line indices.
        current: list[BrailleCell] = []
        for cell in [*rebound, BrailleCell(dots=(), role="custom_sep")]:
            if cell.role == "custom_sep":
                if current:
                    assert len({line_of[id(x)] for x in current}) == 1
                current = []
            else:
                current.append(cell)


class TestSchemeRegistryContract:
    @settings(max_examples=40)
    @given(name=st.one_of(st.none(), st.text(max_size=12)))
    def test_lookup_is_total_with_single_line_fallback(self, name: str | None) -> None:
        # A stale profile / setting must never hard-fail layout: any name
        # resolves to a registered scheme, unknown ones to single_line.
        scheme = get_scheme(name)
        assert scheme.name in scheme_names()
        if name not in scheme_names():
            assert scheme.name == "single_line"
