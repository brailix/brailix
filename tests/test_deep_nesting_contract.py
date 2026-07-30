"""Cross-cutting contract: deeply-nested MathML must never crash the
pipeline with a ``RecursionError``.

The "pipeline never crashes; soft-fail to ``<merror>`` / a warning"
invariant (the ``backend.math`` package docstring's "The pipeline never
crashes", the normalizer's "never raises") held at the element-handler
level but not against *depth*: the recursive tree walks — ``strip_namespace``
at the IR-deserialization boundary, the MathML normalizer's passes, and the
math backend's tag dispatch — would overflow Python's stack on an
adversarially deep (or merely corrupt) tree from an untrusted ``.docx`` OLE
/ direct MathML / ``.blx`` round-trip.

An iterative ``strip_namespace`` plus one bounded-depth probe
(``core._xml.tree_depth_exceeds``) fixes those boundaries together; this
test is the regression that keeps any future tree walk from silently
reintroducing the crash.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import _emit_tree, translate
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.frontend.math.normalizer import normalize
from brailix.ir.inline import MathInline, from_dict

# Far past Python's default recursion limit and the backend's empirical
# ~470-level overflow point, so a recursive walk would certainly crash.
_DEEP = 6000


def _deep_mathml(depth: int = _DEEP) -> str:
    return "<math>" + "<mrow>" * depth + "<mn>1</mn>" + "</mrow>" * depth + "</math>"


def _deep_tree(depth: int = _DEEP) -> ET.Element:
    root = ET.Element("math")
    cur = root
    for _ in range(depth):
        cur = ET.SubElement(cur, "mrow")
    ET.SubElement(cur, "mn").text = "1"
    return root


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


class TestDeepNestingNeverCrashes:
    def test_normalizer_soft_fails_to_merror(self) -> None:
        root = normalize(_deep_mathml())  # must not raise
        # Degraded to a single bare <merror> rather than overflowing the
        # recursive passes (namespace stripped, like the parse-error path).
        assert root.tag == "math"
        assert [c.tag for c in root] == ["merror"]

    def test_ir_deserialization_does_not_raise(self) -> None:
        # safe_fromstring (expat) parses deep XML iteratively; the IR boundary
        # then strips namespaces — iteratively now, so no RecursionError
        # escapes from_dict (the documented soft-fail boundary).
        node = from_dict(
            {"type": "math_inline", "surface": "x", "math": _deep_mathml()}
        )
        assert isinstance(node, MathInline)
        assert node.math is not None
        assert node.math.tag == "math"

    def test_backend_translate_soft_fails(self, profile) -> None:
        ctx = BackendContext(profile="cn_current")
        node = MathInline(surface="deep", source="mathml", math=_deep_tree())
        cells = translate(node, ctx, profile)  # must not raise
        assert cells  # at least the single unknown fallback cell
        assert any(w.code == "MATH_ERROR" for w in ctx.warnings.warnings)

    def test_backend_emit_tree_soft_fails(self, profile) -> None:
        ctx = BackendContext(profile="cn_current")
        cells = _emit_tree(_deep_tree(), ctx, profile)  # must not raise
        assert cells
        assert any(w.code == "MATH_ERROR" for w in ctx.warnings.warnings)

    def test_a_real_formula_still_renders(self, profile) -> None:
        # Guard against the depth cap being set so low it rejects real math:
        # a normal fraction must still translate to real cells, no MATH_ERROR.
        ctx = BackendContext(profile="cn_current")
        tree = normalize("<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>")
        cells = _emit_tree(tree, ctx, profile)
        assert cells
        assert not any(w.code == "MATH_ERROR" for w in ctx.warnings.warnings)
