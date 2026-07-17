"""Property-based tests for the music backend's failure and mode contracts.

The exact mirror of ``test_math_properties.py`` for the other recursive
tree backend: the music dispatcher walks MusicXML and must uphold the same
architecture-level promises over ANY finite tree shape — well-formed
scores, arity violations, unknown elements, degenerate nesting:

* **totality** — NORMAL and LENIENT always run to completion; failures
  degrade to warnings + placeholder cells, never exceptions;
* **mode is diagnostics policy, not behaviour** — cells are identical
  across NORMAL and LENIENT, and STRICT raises exactly when NORMAL would
  have collected a warning;
* **no state leakage** — octave memory, in-accord state, whatever a
  handler tracks is scoped to one translation;
* **diagnostics stay locatable** — MusicXML has no character-level source
  coordinate, so a warning must carry a structured anchor (part /
  measure) or a span; a warning with neither cannot be shown to the user
  at any location.

Notation-rule correctness (which cells a note emits) stays with the
example suites under ``tests/backend/music/``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.backend.music import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import (
    RunMode,
    StrictModeError,
    WarningCollector,
    WarningLevel,
)
from brailix.ir.braille import BrailleCell

_PROFILE = load_profile("cn_current")


def _elem(
    tag: str,
    text: str | None = None,
    attrib: dict[str, str] | None = None,
    children: tuple[ET.Element, ...] | list[ET.Element] = (),
) -> ET.Element:
    e = ET.Element(tag, dict(attrib or {}))
    if text is not None:
        e.text = text
    for c in children:
        e.append(c)
    return e


@st.composite
def _notes(draw: st.DrawFn) -> ET.Element:
    kind = draw(st.sampled_from(["pitched", "pitched", "rest", "malformed", "unknown"]))
    if kind == "rest":
        return _elem("note", children=[_elem("rest"), _elem("duration", "4")])
    if kind == "malformed":
        # A pitch missing its octave / step — must degrade, not crash.
        return _elem(
            "note",
            children=[_elem("pitch", children=[_elem("step", draw(st.sampled_from(["C", "G", "?"])))])],
        )
    if kind == "unknown":
        return _elem("mfoo", draw(st.one_of(st.none(), st.just("?"))))
    return _elem(
        "note",
        children=[
            _elem(
                "pitch",
                children=[
                    _elem("step", draw(st.sampled_from(["C", "D", "E", "F", "G", "A", "B"]))),
                    _elem("octave", draw(st.sampled_from(["1", "3", "4", "5", "7"]))),
                ],
            ),
            _elem("duration", draw(st.sampled_from(["1", "2", "4"]))),
            _elem("type", draw(st.sampled_from(["quarter", "eighth", "half"]))),
        ],
    )


@st.composite
def _measures(draw: st.DrawFn, number: int) -> ET.Element:
    children: list[ET.Element] = []
    if draw(st.booleans()):
        children.append(
            _elem("attributes", children=[_elem("divisions", draw(st.sampled_from(["1", "4"])))])
        )
    children.extend(draw(st.lists(_notes(), max_size=3)))
    return _elem("measure", attrib={"number": str(number)}, children=children)


@st.composite
def score_trees(draw: st.DrawFn) -> ET.Element:
    parts: list[ET.Element] = []
    for p in range(draw(st.integers(0, 2))):
        measures = [
            draw(_measures(number=m + 1)) for m in range(draw(st.integers(0, 2)))
        ]
        parts.append(_elem("part", attrib={"id": f"P{p + 1}"}, children=measures))
    return _elem("score-partwise", children=parts)


def _run(tree: ET.Element, mode: RunMode) -> tuple[list[BrailleCell], list]:
    warnings = WarningCollector(mode=mode)
    ctx = BackendContext(profile="cn_current", mode=mode, warnings=warnings)
    cells = emit_tree(tree, ctx, _PROFILE)
    return cells, list(warnings)


class TestModeContract:
    @settings(max_examples=50)
    @given(tree=score_trees())
    def test_normal_and_lenient_terminate_with_identical_cells(
        self, tree: ET.Element
    ) -> None:
        normal_cells, normal_warnings = _run(tree, RunMode.NORMAL)
        lenient_cells, lenient_warnings = _run(tree, RunMode.LENIENT)
        assert lenient_cells == normal_cells
        assert sorted(w.code for w in lenient_warnings) == sorted(
            w.code for w in normal_warnings
        )
        assert all(w.level is not WarningLevel.ERROR for w in lenient_warnings)

    @settings(max_examples=50)
    @given(tree=score_trees())
    def test_strict_raises_exactly_when_normal_warns(self, tree: ET.Element) -> None:
        normal_cells, normal_warnings = _run(tree, RunMode.NORMAL)
        try:
            strict_cells, _ = _run(tree, RunMode.STRICT)
            raised = False
        except StrictModeError:
            raised = True
        assert raised == bool(normal_warnings)
        if not raised:
            assert strict_cells == normal_cells


class TestStateIsolation:
    @settings(max_examples=30)
    @given(first=score_trees(), second=score_trees())
    def test_prior_translation_cannot_change_the_next(
        self, first: ET.Element, second: ET.Element
    ) -> None:
        # Octave memory (interval16 rule), measure state, in-accord depth —
        # whatever a handler tracks must be scoped to one translation.
        warnings = WarningCollector(mode=RunMode.NORMAL)
        shared = BackendContext(
            profile="cn_current", mode=RunMode.NORMAL, warnings=warnings
        )
        emit_tree(first, shared, _PROFILE)
        chained = emit_tree(second, shared, _PROFILE)
        fresh, _ = _run(second, RunMode.NORMAL)
        assert chained == fresh


class TestDiagnosticsLocatable:
    @settings(max_examples=50)
    @given(tree=score_trees())
    def test_every_warning_carries_an_anchor_or_span(self, tree: ET.Element) -> None:
        # MusicXML has no character-level source coordinate; a warning
        # without a structured anchor (part / measure) or a span cannot be
        # pointed at anything in the score UI.
        _, warnings = _run(tree, RunMode.NORMAL)
        for w in warnings:
            assert w.anchor or w.span is not None, (w.code, w.message)
