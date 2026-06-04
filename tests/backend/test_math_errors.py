"""Math backend tests for error / unsupported / defensive paths:
<merror>, unsupported elements (<mstyle>, <mphantom>, <mtr>, <menclose>,
unknown tags), MathInline edge cases + serialization, and defensive
number/spacing guards.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend.math import translate
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.core.span import Span
from brailix.ir.inline import MathInline
from tests.backend._math_common import emit, mml

# ---------------------------------------------------------------------------
# 37: <merror>
# ---------------------------------------------------------------------------


class TestMerror:
    def test_merror_warns_with_reason(self, profile):
        cells, wc = emit(
            mml('<math><merror data-reason="latex parse error">'
                "<mtext>bad</mtext></merror></math>"),
            profile,
        )
        assert any(c.role == "unknown" for c in cells)
        codes = [w.code for w in wc]
        assert "MATH_ERROR" in codes


# ---------------------------------------------------------------------------
# 38-40: unsupported elements (mtable / mstyle / mphantom)
# ---------------------------------------------------------------------------


class TestUnsupported:
    def test_mtable_now_handled_not_unsupported(self, profile):
        # mtable is no longer "unsupported" — it routes to the linear
        # notation (full coverage in TestMatrix). Guard against regressing
        # to a MATH_UNSUPPORTED_ELEMENT warning + unknown cell.
        cells, wc = emit(
            mml("<math><mtable><mtr><mtd><mi>x</mi></mtd></mtr></mtable></math>"),
            profile,
        )
        assert "MATH_UNSUPPORTED_ELEMENT" not in [w.code for w in wc]
        assert not any(c.role == "unknown" for c in cells)

    def test_mstyle_warns(self, profile):
        cells, wc = emit(
            mml("<math><mstyle><mi>x</mi></mstyle></math>"), profile
        )
        assert any(w.code == "MATH_UNSUPPORTED_ELEMENT" for w in wc)

    def test_mphantom_warns(self, profile):
        cells, wc = emit(
            mml("<math><mphantom><mi>x</mi></mphantom></math>"), profile
        )
        assert any(w.code == "MATH_UNSUPPORTED_ELEMENT" for w in wc)


# ---------------------------------------------------------------------------
# 41-43: MathInline edge cases + serialization
# ---------------------------------------------------------------------------


class TestMathInlineEdges:
    def test_math_none_emits_warning_and_unknown_surface(self, profile):
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(profile="cn_current", warnings=wc)
        node = MathInline(surface="x^2", span=Span(0, 3), math=None)
        cells = translate(node, ctx, profile)
        assert all(c.role == "unknown" for c in cells)
        assert len(cells) == 3
        assert any(w.code == "MATH_NO_IR" for w in wc)

    def test_empty_math_root_emits_no_cells(self, profile):
        cells, wc = emit(mml("<math></math>"), profile)
        assert cells == []
        assert not any(w.code.startswith("MATH_") for w in wc)

    def test_to_dict_from_dict_round_trip_preserves_tree(self, profile):
        from brailix.ir.inline import from_dict as inline_from_dict

        tree = ET.fromstring("<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>")
        node = MathInline(surface="1/2", source="latex", math=tree)
        payload = node.to_dict()
        # The serialised form holds MathML as a string.
        assert isinstance(payload["math"], str)
        restored = inline_from_dict(payload)
        assert isinstance(restored.math, ET.Element)
        assert restored.math.tag == "math"
        # Both sides emit the same braille.
        original_cells, _ = emit(tree, profile)
        restored_cells, _ = emit(restored.math, profile)
        assert [c.dots for c in original_cells] == [c.dots for c in restored_cells]


# ---------------------------------------------------------------------------
# Edge / defensive cases
# ---------------------------------------------------------------------------


class TestDefensive:
    def test_missing_number_part_warns(self, profile):
        # Force the decimal_point cell empty so the MATH_MISSING_NUMBER_PART
        # branch fires.
        from dataclasses import replace

        empty_profile = replace(profile, decimal_point=())
        cells, wc = emit(mml("<math><mn>3.5</mn></math>"), empty_profile)
        assert any(c.role == "unknown" for c in cells)
        assert any(w.code == "MATH_MISSING_NUMBER_PART" for w in wc)

    def test_op_spacing_off_via_feature(self, profile, monkeypatch):
        monkeypatch.setitem(
            profile.features.setdefault("math", {}), "op_spacing", False
        )
        cells, _ = emit(
            mml("<math><mrow><mi>a</mi><mo>+</mo><mi>b</mi></mrow></math>"),
            profile,
        )
        # No blanks anywhere.
        assert all(not c.is_blank for c in cells)

    def test_op_spacing_collapses_adjacent_blanks(self, profile):
        # Two consecutive plus signs would each want a leading blank,
        # but the second's leading blank should collapse against the
        # first's trailing blank (none here, but checks symmetry).
        cells, _ = emit(
            mml("<math><mrow><mi>a</mi><mo>+</mo><mo>+</mo><mi>b</mi></mrow></math>"),
            profile,
        )
        for i in range(len(cells) - 1):
            assert not (cells[i].is_blank and cells[i + 1].is_blank)


# ---------------------------------------------------------------------------
# merror extras
# ---------------------------------------------------------------------------


class TestMerrorExtras:
    def test_merror_without_reason(self, profile):
        cells, wc = emit(
            mml("<math><merror><mtext>oops</mtext></merror></math>"), profile
        )
        codes = [w.code for w in wc]
        assert "MATH_ERROR" in codes

    def test_merror_inherits_text_to_warning(self, profile):
        cells, wc = emit(
            mml(
                '<math><merror data-reason="bad"><mtext>$x{</mtext></merror></math>'
            ),
            profile,
        )
        msgs = [w.message for w in wc if w.code == "MATH_ERROR"]
        assert msgs
        assert "bad" in msgs[0]


# ---------------------------------------------------------------------------
# Unsupported extras
# ---------------------------------------------------------------------------


class TestUnsupportedExtras:
    def test_mtr_warning(self, profile):
        cells, wc = emit(
            mml("<math><mtr><mtd><mi>x</mi></mtd></mtr></math>"), profile
        )
        # mtr is in the dispatch table as unsupported.
        codes = [w.code for w in wc]
        assert "MATH_UNSUPPORTED_ELEMENT" in codes

    def test_unknown_element_warning(self, profile):
        # <ms> isn't in the table at all → falls back to _emit_unsupported.
        cells, wc = emit(mml("<math><ms>foo</ms></math>"), profile)
        codes = [w.code for w in wc]
        assert "MATH_UNSUPPORTED_ELEMENT" in codes

    def test_menclose_warning(self, profile):
        cells, wc = emit(
            mml("<math><menclose><mi>x</mi></menclose></math>"), profile
        )
        codes = [w.code for w in wc]
        assert "MATH_UNSUPPORTED_ELEMENT" in codes
