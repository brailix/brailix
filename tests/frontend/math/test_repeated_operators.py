"""General-math repeated-operator warnings.

The normalizer tags the second of two immediately-adjacent identical
relational / arithmetic ``<mo>`` siblings (``==``, ``<<``, ``++``) so the
backend flags a likely typo — faithfully translating both operators, just
warning. Chained relations (``a = b = c``) and intentionally-doubled forms
(``n!!``) are left alone. The chemistry path has its own check
(see ``tests/backend/test_math_chem.py``)."""

from __future__ import annotations

from xml.sax.saxutils import escape

import pytest

from brailix import Pipeline
from brailix.frontend.math.normalizer import normalize


def _mo(op: str) -> str:
    return f"<mo>{escape(op)}</mo>"


def _warned(root) -> list:
    return [e for e in root.iter("mo") if e.get("data-bk-warn") == "repeated-operator"]


class TestNormalizerTagsRepeatedOperators:
    @pytest.mark.parametrize("op", ["=", "<", ">", "+", "-"])
    def test_adjacent_duplicate_tagged(self, op):
        root = normalize(f"<math><mi>a</mi>{_mo(op)}{_mo(op)}<mi>b</mi></math>")
        warned = _warned(root)
        assert len(warned) == 1 and warned[0].text == op

    def test_chained_relation_not_tagged(self):
        # a = b = c — the '=' are separated by operands, not adjacent.
        root = normalize(
            f"<math><mi>a</mi>{_mo('=')}<mi>b</mi>{_mo('=')}<mi>c</mi></math>"
        )
        assert _warned(root) == []

    def test_double_factorial_not_tagged(self):
        # n!! is a real notation (double factorial) — excluded from the set.
        root = normalize(f"<math><mi>n</mi>{_mo('!')}{_mo('!')}</math>")
        assert _warned(root) == []

    def test_different_adjacent_operators_not_tagged(self):
        # '= -' (relation then unary minus) is not a duplicate.
        root = normalize(f"<math>{_mo('=')}{_mo('-')}<mn>5</mn></math>")
        assert _warned(root) == []

    def test_triple_flags_each_extra(self):
        root = normalize(f"<math>{_mo('=')}{_mo('=')}{_mo('=')}</math>")
        assert len(_warned(root)) == 2


class TestRepeatedOperatorEndToEnd:
    def test_double_equals_in_latex_warns(self):
        res = Pipeline(profile="cn_current").translate_text("设 $a == b$ 成立")
        assert res.warnings.by_code("MATH_REPEATED_OPERATOR")

    def test_much_less_than_hint(self):
        res = Pipeline(profile="cn_current").translate_text("$a << b$")
        hits = res.warnings.by_code("MATH_REPEATED_OPERATOR")
        assert hits and "much-less-than" in hits[0].message

    def test_chained_equality_no_warning(self):
        res = Pipeline(profile="cn_current").translate_text("$a = b = c$")
        assert res.warnings.by_code("MATH_REPEATED_OPERATOR") == []
