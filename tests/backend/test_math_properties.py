"""Property-based tests for the math backend's failure and mode contracts.

The math backend is a recursive dispatcher over normalized MathML. Its
architecture-level promises — the ones every handler must uphold no matter
what tree shape arrives — are:

* **totality** — any finite tree translates to completion in NORMAL and
  LENIENT mode; unknown tags, arity violations and ``<merror>`` degrade to
  warnings + placeholder cells, never exceptions;
* **mode is diagnostics policy, not behaviour** — the emitted cells are
  identical across NORMAL and LENIENT (only warning *levels* differ), and
  STRICT raises exactly when NORMAL would have collected a warning;
* **no state leakage** — translating one tree must not change how the next
  tree translates through the same backend context.

Rule-level output correctness stays with the example/golden suites; this
module only pins the contracts above, over generated trees fed through the
real normalizer first (the shape the backend actually receives).
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.backend.math import emit_tree
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import (
    RunMode,
    StrictModeError,
    WarningCollector,
    WarningLevel,
)
from brailix.frontend.math.normalizer import normalize
from brailix.ir.braille import BrailleCell

_PROFILE = load_profile("cn_current")

_UNKNOWN_TAG = "mfoo"


def _elem(
    tag: str,
    text: str | None = None,
    children: tuple[ET.Element, ...] | list[ET.Element] = (),
) -> ET.Element:
    e = ET.Element(tag)
    if text is not None:
        e.text = text
    for c in children:
        e.append(c)
    return e


@st.composite
def _leaves(draw: st.DrawFn) -> ET.Element:
    kind = draw(st.sampled_from(["mi", "mn", "mo", "mtext", _UNKNOWN_TAG]))
    if kind == "mi":
        return _elem("mi", draw(st.sampled_from(["x", "y", "α", "AB"])))
    if kind == "mn":
        return _elem("mn", draw(st.sampled_from(["0", "7", "12", "3.5"])))
    if kind == "mo":
        return _elem("mo", draw(st.sampled_from(["+", "-", "=", "<", ",", "∘"])))
    if kind == "mtext":
        return _elem("mtext", draw(st.sampled_from(["sin", "lim", "if"])))
    return _elem(_UNKNOWN_TAG, draw(st.one_of(st.none(), st.just("?"))))


def _containers(children: st.SearchStrategy[ET.Element]) -> st.SearchStrategy[ET.Element]:
    pair = st.tuples(children, children)
    return st.one_of(
        st.builds(lambda ks: _elem("mrow", None, ks), st.lists(children, max_size=3)),
        # Arity violations included on purpose: a 1- or 3-child mfrac must
        # degrade in-band, not blow the recursion.
        st.builds(lambda ks: _elem("mfrac", None, ks), st.lists(children, min_size=1, max_size=3)),
        st.builds(lambda p: _elem("msup", None, p), pair),
        st.builds(lambda p: _elem("msub", None, p), pair),
        st.builds(lambda ks: _elem("msqrt", None, ks), st.lists(children, min_size=1, max_size=2)),
        st.builds(lambda ks: _elem("merror", None, ks), st.lists(children, max_size=1)),
    )


@st.composite
def normalized_math_trees(draw: st.DrawFn) -> ET.Element:
    kids = draw(st.lists(st.recursive(_leaves(), _containers, max_leaves=8), max_size=3))
    root = _elem("math", None, kids)
    # The backend consumes the NORMALIZER's output — feed the same shape.
    return normalize(ET.tostring(root, encoding="unicode"))


def _run(tree: ET.Element, mode: RunMode) -> tuple[list[BrailleCell], list]:
    warnings = WarningCollector(mode=mode)
    ctx = BackendContext(profile="cn_current", mode=mode, warnings=warnings)
    cells = emit_tree(tree, ctx, _PROFILE)
    return cells, list(warnings)


class TestModeContract:
    @settings(max_examples=60)
    @given(tree=normalized_math_trees())
    def test_normal_and_lenient_terminate_with_identical_cells(
        self, tree: ET.Element
    ) -> None:
        # Neither mode may raise, and the mode must not change the OUTPUT —
        # it is a diagnostics policy. LENIENT keeps every diagnostic but
        # holds no ERROR-level entries (downgraded, never dropped).
        normal_cells, normal_warnings = _run(tree, RunMode.NORMAL)
        lenient_cells, lenient_warnings = _run(tree, RunMode.LENIENT)
        assert lenient_cells == normal_cells
        assert sorted(w.code for w in lenient_warnings) == sorted(
            w.code for w in normal_warnings
        )
        assert all(w.level is not WarningLevel.ERROR for w in lenient_warnings)

    @settings(max_examples=60)
    @given(tree=normalized_math_trees())
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


class TestFailureInBand:
    @settings(max_examples=60)
    @given(tree=normalized_math_trees(), data=st.data())
    def test_unknown_element_is_reported_not_swallowed(
        self, tree: ET.Element, data: st.DataObject
    ) -> None:
        # Force at least one unknown element at dispatch level. Whatever
        # else the tree contains, the failure must surface as a warning —
        # silence would mean content vanished with no trace.
        tree.append(_elem(_UNKNOWN_TAG, data.draw(st.one_of(st.none(), st.just("?")))))
        _, warnings = _run(tree, RunMode.NORMAL)
        assert warnings


class TestStateIsolation:
    @settings(max_examples=40)
    @given(first=normalized_math_trees(), second=normalized_math_trees())
    def test_prior_translation_cannot_change_the_next(
        self, first: ET.Element, second: ET.Element
    ) -> None:
        # Number-sign runs, script depth, letter-sign state ... whatever a
        # handler tracks, it must be scoped to one translation. Translating
        # ``second`` after ``first`` through the SAME backend context must
        # equal translating ``second`` alone.
        warnings = WarningCollector(mode=RunMode.NORMAL)
        shared = BackendContext(
            profile="cn_current", mode=RunMode.NORMAL, warnings=warnings
        )
        emit_tree(first, shared, _PROFILE)
        chained = emit_tree(second, shared, _PROFILE)
        fresh, _ = _run(second, RunMode.NORMAL)
        assert chained == fresh
