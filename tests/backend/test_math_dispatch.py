"""Math backend tests for dispatch / pipeline integration and the
number-sign state machine: end-to-end LaTeX goldens, the node dispatcher
route, and MathBrailleContext state resets.

Shared helpers come from ``_math_common``; the ``profile`` fixture is
provided by ``tests/backend/conftest.py``.
"""

from __future__ import annotations

import pytest

from brailix.backend.dispatch import translate_node
from brailix.backend.math import MathBrailleContext
from brailix.core.context import BackendContext
from brailix.core.errors import RunMode, WarningCollector
from brailix.ir.inline import MathInline
from tests.backend._math_common import emit, emit_via_tree, mml

# ---------------------------------------------------------------------------
# 44-47: pipeline integration goldens
# ---------------------------------------------------------------------------


pytest.importorskip("latex2mathml.converter")


class TestPipelineIntegration:
    def test_pipeline_x_squared(self):
        from brailix import Pipeline

        pipe = Pipeline()
        result = pipe.translate_text(r"$x^2$")
        r = [c.role for b in result.braille_ir.blocks for c in b.cells]
        assert "math_identifier" in r
        assert "math_superscript" in r
        # Atomic single-digit exponent → Antoine lower-form digit
        # (no number_sign, no close).
        sup_at = r.index("math_superscript")
        assert "math_digit_lower" in r[sup_at:]
        assert "number_sign" not in r[sup_at:]
        assert "math_script_close" not in r[sup_at:]

    def test_pipeline_sin_x(self):
        from brailix import Pipeline

        pipe = Pipeline()
        result = pipe.translate_text(r"$\sin x$")
        r = [c.role for b in result.braille_ir.blocks for c in b.cells]
        assert "math_function_prefix" in r
        assert "math_function_name" in r
        assert "math_identifier" in r

    def test_pipeline_one_half_uses_antoine(self):
        from brailix import Pipeline

        pipe = Pipeline()
        result = pipe.translate_text(r"$\frac{1}{2}$")
        r = [c.role for b in result.braille_ir.blocks for c in b.cells]
        assert "math_digit_lower" in r
        assert "math_fraction_bar" not in r

    def test_pipeline_lim_x_to_zero(self):
        from brailix import Pipeline

        pipe = Pipeline()
        result = pipe.translate_text(r"$\lim_{x \to 0} f(x)$")
        cells = [c for b in result.braille_ir.blocks for c in b.cells]
        r = [c.role for c in cells]
        assert "math_function_prefix" in r
        assert "math_function_name" in r
        assert "math_big_op_script_prefix" in r
        assert "math_subscript" in r
        assert "math_rel" in r           # the → arrow
        assert "number_sign" in r        # before 0
        assert "math_script_close" in r  # closes the complex sub
        # f and x identifiers
        assert any(c.source_text == "x" for c in cells if c.role == "math_identifier")
        assert any(c.source_text == "f" for c in cells if c.role == "math_identifier")


# ---------------------------------------------------------------------------
# Dispatcher integration
# ---------------------------------------------------------------------------


class TestDispatcherIntegration:
    def test_translate_node_routes_math_inline(self, profile):
        wc = WarningCollector(mode=RunMode.NORMAL)
        ctx = BackendContext(profile="cn_current", warnings=wc)
        node = MathInline(
            surface="x",
            source="mathml",
            math=mml("<math><mi>x</mi></math>"),
        )
        cells = translate_node(node, ctx, profile)
        assert any(c.role == "math_identifier" for c in cells)


# ---------------------------------------------------------------------------
# State machine
# ---------------------------------------------------------------------------


class TestStateMachine:
    def test_consecutive_digits_in_mn_only_first_gets_sign(self, profile):
        cells, _ = emit(mml("<math><mn>1234</mn></math>"), profile)
        ns_count = sum(1 for c in cells if c.role == "number_sign")
        assert ns_count == 1

    def test_relation_resets_number_sign(self, profile):
        cells, _ = emit(
            mml("<math><mrow><mn>1</mn><mo>=</mo><mn>2</mn></mrow></math>"), profile
        )
        ns_count = sum(1 for c in cells if c.role == "number_sign")
        assert ns_count == 2

    def test_identifier_resets_number_sign(self, profile):
        cells, _ = emit(
            mml("<math><mrow><mn>1</mn><mi>x</mi><mn>2</mn></mrow></math>"), profile
        )
        ns_count = sum(1 for c in cells if c.role == "number_sign")
        assert ns_count == 2

    def test_context_default_state(self, profile):
        ctx = BackendContext()
        mctx = MathBrailleContext(profile=profile, backend=ctx)
        assert mctx.need_number_sign is True

    def test_emit_tree_helper(self, profile):
        # Convenience function should produce the same cells as
        # translate() on a wrapping MathInline.
        tree = mml("<math><mi>x</mi></math>")
        ct, _ = emit(tree, profile)
        ct2, _ = emit_via_tree(tree, profile)
        assert [c.dots for c in ct] == [c.dots for c in ct2]
