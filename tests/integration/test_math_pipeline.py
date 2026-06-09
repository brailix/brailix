"""End-to-end tests for math frontend integration in the Pipeline.

The math IR is the MathML tree itself (an
:class:`ET.Element`); these tests confirm:

* the segmenter peels ``$...$`` math out of mixed text;
* the math source adapter + normalizer produce an ET.Element on
  :attr:`MathInline.math`;
* missing-adapter conditions warn cleanly and leave ``math = None``;
* downstream consumers (proofread JSON, braille renderer) see the
  tree.
"""

from __future__ import annotations

import sys
import types
import xml.etree.ElementTree as ET

import pytest

from brailix import Pipeline
from brailix.core.context import MathContext
from brailix.frontend.math import parse_math_tree
from brailix.frontend.math.registry import math_source_registry
from brailix.ir.inline import MathInline


@pytest.fixture
def reset_math_source_cache():
    """Clean both before *and* after the test.

    ``math_source_registry`` caches the adapter instance returned by
    each loader. The monkeypatched ``latex2mathml`` tests in this
    module replace ``sys.modules['latex2mathml']`` and then call
    ``clear_cache()`` so the loader re-imports — fine for *that*
    test. But if we don't clear again on teardown, the cache still
    holds the fake adapter while ``sys.modules`` has been restored,
    poisoning every later test that asks for the ``latex`` adapter.
    """
    math_source_registry.clear_cache()
    yield
    math_source_registry.clear_cache()


class TestTranslateMathInline:
    """Pipeline.translate_math_inline — one formula → braille string, so
    callers (CLI / a proofreading front-end preview) needn't reassemble
    backend.math."""

    def test_mathml_source_renders_nonempty(self):
        pipe = Pipeline(profile="cn_current")
        out = pipe.translate_math_inline("<math><mn>5</mn></math>", "mathml")
        assert isinstance(out, str) and out != ""

    def test_blank_surface_returns_empty(self):
        pipe = Pipeline(profile="cn_current")
        assert pipe.translate_math_inline("   ", "mathml") == ""


class TestInlineMathAdapterCrash:
    """A non-conforming math source adapter that raises must not crash the
    whole document translate; the inline parse is guarded like display
    math, and the backend's MATH_NO_IR path degrades a None tree to a
    warning + placeholder rather than propagating the exception."""

    def test_raising_adapter_degrades_not_crashes(self, monkeypatch):
        import brailix.pipeline as pipeline_mod

        def _boom(surface, ctx):
            raise ValueError("adapter blew up")

        # _attach_math calls the module-level parse alias; make it raise.
        monkeypatch.setattr(pipeline_mod, "_frontend_parse_math_tree", _boom)
        pipe = Pipeline(profile="cn_current", mode="normal")
        # Inline $...$ math flows through _attach_math (math=None at first).
        result = pipe.translate_text("看 $x^2$ 完")
        # Must NOT raise; the surrounding prose still renders.
        assert result.render()
        assert "MATH_INLINE_PARSE_FAILED" in [w.code for w in result.warnings]


# ---------------------------------------------------------------------------
# MathML adapter + normalizer wiring (no IR-builder layer)
# ---------------------------------------------------------------------------


class TestMathmlAdapterWiring:
    def test_mathml_round_trip(self):
        adapter = math_source_registry.get("mathml")
        ctx = MathContext(source="mathml")
        # Adapter directly returns the MathML string verbatim for the
        # mathml source format; we assert that to lock the contract.
        assert "<mi>x</mi>" in adapter.to_mathml("<math><mi>x</mi></math>", ctx)
        # parse_math_tree wraps adapter + normalizer.
        tree = parse_math_tree("<math><mi>x</mi></math>", ctx)
        assert isinstance(tree, ET.Element)
        assert tree.tag == "math"
        # Single-child mrow collapses; <math><mi>x</mi></math> stays.
        # The inner <mi> survives normalisation.
        assert tree[0].tag == "mi"
        assert (tree[0].text or "").strip() == "x"

    def test_translate_text_with_latex_dollars(self):
        # Real end-to-end check: $x$ comes out as a populated MathInline.
        # Requires the LaTeX → MathML adapter; without it the segmenter
        # still produces a MathInline but math stays None (covered by
        # TestMissingAdapter).
        pytest.importorskip("latex2mathml")
        pipe = Pipeline()
        result = pipe.translate_text("$x$")
        math_nodes = _find_math_nodes(result.ir)
        assert len(math_nodes) == 1
        assert math_nodes[0].math is not None
        assert isinstance(math_nodes[0].math, ET.Element)
        assert math_nodes[0].math.tag == "math"


# ---------------------------------------------------------------------------
# Missing adapter -> warning + math stays None
# ---------------------------------------------------------------------------


class TestMissingAdapter:
    def test_latex_without_extras_emits_warning(
        self, monkeypatch, reset_math_source_cache
    ):
        monkeypatch.setitem(sys.modules, "latex2mathml", None)
        monkeypatch.setitem(sys.modules, "latex2mathml.converter", None)
        math_source_registry.clear_cache()

        pipe = Pipeline()
        result = pipe.translate_text("a $x^2$ b")

        math_nodes = _find_math_nodes(result.ir)
        assert len(math_nodes) == 1
        assert math_nodes[0].math is None

        codes = {w.code for w in result.warnings}
        assert "MATH_ADAPTER_MISSING" in codes

    def test_unregistered_source_emits_warning(self):
        ctx = MathContext(source="plain")

        assert parse_math_tree("x", ctx) is None

        warnings = ctx.warnings.by_code("MATH_ADAPTER_MISSING")
        assert len(warnings) == 1
        assert "plain" in warnings[0].message
        assert "mathml" in warnings[0].candidates


# ---------------------------------------------------------------------------
# Fake latex2mathml -> end-to-end pipeline fills math with ET tree
# ---------------------------------------------------------------------------


class TestEndToEndLatex:
    def test_dollar_math_gets_tree(self, monkeypatch, reset_math_source_cache):
        fake_converter_mod = types.ModuleType("latex2mathml.converter")

        def fake_convert(formula: str) -> str:
            return f"<math><mn>{formula}</mn></math>"

        fake_converter_mod.convert = fake_convert
        fake_pkg = types.ModuleType("latex2mathml")
        fake_pkg.converter = fake_converter_mod
        monkeypatch.setitem(sys.modules, "latex2mathml", fake_pkg)
        monkeypatch.setitem(sys.modules, "latex2mathml.converter", fake_converter_mod)
        math_source_registry.clear_cache()

        pipe = Pipeline()
        result = pipe.translate_text("see $x^2$ now")

        math_nodes = _find_math_nodes(result.ir)
        assert len(math_nodes) == 1
        tree = math_nodes[0].math
        assert isinstance(tree, ET.Element)
        # The fake converter wraps the latex surface in <mn>x^2</mn>;
        # the normalizer collapses <math><mn>x^2</mn></math> ⇒ root with
        # a single mn child whose text is "x^2".
        assert tree.tag == "math"
        assert tree[0].tag == "mn"
        assert (tree[0].text or "").strip() == "x^2"

    def test_proofread_json_serialises_math_as_string(
        self, monkeypatch, reset_math_source_cache
    ):
        fake_pkg = types.ModuleType("latex2mathml")
        conv = types.ModuleType("latex2mathml.converter")
        conv.convert = lambda f: f"<math><mi>{f}</mi></math>"
        fake_pkg.converter = conv
        monkeypatch.setitem(sys.modules, "latex2mathml", fake_pkg)
        monkeypatch.setitem(sys.modules, "latex2mathml.converter", conv)
        math_source_registry.clear_cache()

        pipe = Pipeline()
        result = pipe.translate_text("$y$")
        payload = result.proofread_json()
        ir_blocks = payload["ir"]["blocks"]
        para_children = ir_blocks[0]["children"]
        math_entry = next(c for c in para_children if c["type"] == "math_inline")
        # New schema: math is a MathML string, not a nested dict.
        assert isinstance(math_entry["math"], str)
        assert "<mi>y</mi>" in math_entry["math"]


# ---------------------------------------------------------------------------
# End-to-end braille rendering
# ---------------------------------------------------------------------------


class TestEndToEndBrailleRendering:
    def test_latex_inline_math_renders_to_braille(self):
        # End-to-end LaTeX → braille requires the latex2mathml adapter.
        pytest.importorskip("latex2mathml")
        pipe = Pipeline()
        result = pipe.translate_text(r"$x^2$")
        rendered = result.render()
        assert isinstance(rendered, str)
        for ch in rendered:
            cp = ord(ch)
            assert 0x2800 <= cp <= 0x28FF or ch == "\n"
        roles = [c.role for b in result.braille_ir.blocks for c in b.cells]
        assert "math_identifier" in roles
        assert "math_superscript" in roles
        # Atomic single-digit exponent → Antoine lower-form digit cell.
        assert "math_digit_lower" in roles

    def test_latex_pipeline_renders_full_formula(
        self, monkeypatch, reset_math_source_cache
    ):
        fake_pkg = types.ModuleType("latex2mathml")
        conv = types.ModuleType("latex2mathml.converter")
        conv.convert = lambda f: (
            "<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>"
        )
        fake_pkg.converter = conv
        monkeypatch.setitem(sys.modules, "latex2mathml", fake_pkg)
        monkeypatch.setitem(sys.modules, "latex2mathml.converter", conv)
        math_source_registry.clear_cache()

        pipe = Pipeline()
        result = pipe.translate_text("a $1/2$ b")
        rendered = result.render()
        assert isinstance(rendered, str)
        assert len(rendered) > 0

        bd = result.braille_ir
        roles_seen = [c.role for block in bd.blocks for c in block.cells]
        assert "math_digit_lower" in roles_seen

    def test_chem_chinese_condition_via_pipeline(self):
        # A \ce reaction with a Chinese condition routes the condition text
        # through the pipeline's inline_text_translator (zh path), so it
        # renders as Chinese braille rather than per-char unknowns.
        pytest.importorskip("latex2mathml")
        pipe = Pipeline(profile="cn_current")
        result = pipe.translate_text(r"$\ce{H2 + Cl2 ->[点燃] HCl}$")
        roles = [c.role for b in result.braille_ir.blocks for c in b.cells]
        # The connector carries an over-condition (46-prefix + superscript
        # sign), and the molecules render in chem mode (chemical-formula
        # indicator).
        assert "math_big_op_script_prefix" in roles
        assert "math_chem_indicator" in roles
        # 点燃 went through the zh text path — not the per-char unknown fallback.
        assert not any(w.code == "MATH_UNKNOWN_TEXT_CHAR" for w in result.warnings)


# ---------------------------------------------------------------------------
# Pipeline internals (direct exercise)
# ---------------------------------------------------------------------------


class TestPipelineAttachMath:
    """``Pipeline._attach_math`` is idempotent: if the MathInline
    already has its ``math`` populated, the second call is a no-op.
    This pins that behaviour so re-running a partially-completed
    pipeline doesn't double-parse."""

    def test_attach_math_no_op_when_already_attached(self):
        from brailix.core.context import FrontendContext
        from brailix.pipeline import Pipeline

        pipe = Pipeline()
        ctx = FrontendContext(profile="cn_current")
        tree = ET.fromstring("<math><mi>x</mi></math>")
        node = MathInline(surface="x", source="mathml", math=tree)
        # Sentinel: same object identity should survive.
        pipe._attach_math(node, ctx)
        assert node.math is tree


class TestTokensToInlineEmpty:
    """``tokens_to_inline`` short-circuits on an empty token list.

    The helper now lives in :mod:`brailix.frontend.zh` (ARCHITECTURE
    §7.1 — Chinese-typesetting logic belongs in the Chinese subsystem,
    not in the language-agnostic orchestrator).  More comprehensive
    coverage in :mod:`tests.frontend.zh.analyzer.test_inline`; this one stays
    here as a Pipeline-context smoke that the public API works."""

    def test_empty_returns_empty(self):
        from brailix.frontend.zh import tokens_to_inline

        assert tokens_to_inline([]) == []


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _find_math_nodes(doc) -> list[MathInline]:
    out: list[MathInline] = []
    for block in doc.blocks:
        for child in block.children:
            if isinstance(child, MathInline):
                out.append(child)
    return out
