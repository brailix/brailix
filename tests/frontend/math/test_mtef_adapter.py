"""Tests for :mod:`brailix.frontend.math.adapters.mtef`.

The MTEF adapter consumes the binary equation format MathType (and the
legacy Microsoft Equation 3.0) embed inside Word documents as an OLE
object. There is no convenient text representation; the tests build
MTEF byte streams by hand via the ``_mtef_builder`` helper and pin the
resulting MathML.

Coverage focuses on the constructs already supported by the OMML
adapter — anything that produces a different MathML shape here vs.
OMML would silently translate Word formulas differently depending on
how the user inserted them.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.frontend.math.adapters.mtef import MtefMathSourceAdapter

from . import _mtef_builder as B

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _to_mathml(payload: bytes) -> str:
    return MtefMathSourceAdapter().to_mathml(payload)


def _ord(c: str) -> int:
    return ord(c)


def _local(tag: str) -> str:
    """Strip XML namespace from an ElementTree tag (handles ``{ns}name``)."""
    if tag.startswith("{"):
        return tag.partition("}")[2]
    return tag


def _find_first(root: ET.Element, local_name: str) -> ET.Element | None:
    """Find first descendant whose local name matches, ignoring namespace.

    ``ET.Element.find`` requires the Clark-form name when the document
    declares a default namespace, which ours does (the MathML xmlns).
    Tests prefer to query by local name only.
    """
    for el in root.iter():
        if _local(el.tag) == local_name:
            return el
    return None


# ---------------------------------------------------------------------------
# v5 — basic CHARs and runs
# ---------------------------------------------------------------------------


class TestV5BasicChars:
    def test_single_letter_becomes_mi(self):
        # "x" wrapped in a LINE
        payload = B.v5_prelude() + B.v5_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert out.startswith("<math")

    def test_single_digit_becomes_mn(self):
        payload = B.v5_prelude() + B.v5_simple_char_line(_ord("5"))
        out = _to_mathml(payload)
        assert "<mn>5</mn>" in out

    def test_operator_char_becomes_mo(self):
        payload = B.v5_prelude() + B.v5_simple_char_line(_ord("+"))
        out = _to_mathml(payload)
        assert "<mo>+</mo>" in out

    def test_multiple_chars_in_line(self):
        # "2x+1" — four CHAR records in one LINE
        body = (
            B.v5_char(_ord("2"))
            + B.v5_char(_ord("x"))
            + B.v5_char(_ord("+"))
            + B.v5_char(_ord("1"))
        )
        payload = B.v5_prelude() + B.v5_line(body)
        out = _to_mathml(payload)
        assert "<mn>2</mn>" in out
        assert "<mi>x</mi>" in out
        assert "<mo>+</mo>" in out
        assert "<mn>1</mn>" in out

    def test_no_char_marker_is_dropped(self):
        # MathType's "empty slot" sentinel (0xEEFF) should not emit text.
        body = B.v5_char(_ord("a")) + B.v5_char(0xEEFF) + B.v5_char(_ord("b"))
        payload = B.v5_prelude() + B.v5_line(body)
        out = _to_mathml(payload)
        assert "<mi>a</mi>" in out
        assert "<mi>b</mi>" in out
        assert "EEFF" not in out.upper()

    def test_pua_chars_dropped(self):
        # MathType emits its private-font glyphs (Symbol / MTExtra / custom
        # typeface slots) as PUA codepoints — these have no standard
        # Unicode semantics, downstream can't render them, and 0xEEFF
        # (already dropped) is just one of many. The whole PUA range
        # U+E000–U+F8FF is treated as a sentinel.
        body = (
            B.v5_char(_ord("a"))
            + B.v5_char(0xEF04)  # observed in real Word docs
            + B.v5_char(_ord("b"))
        )
        payload = B.v5_prelude() + B.v5_line(body)
        out = _to_mathml(payload)
        assert "<mi>a</mi>" in out
        assert "<mi>b</mi>" in out
        # No raw PUA codepoint leaks into the MathML (U+EF04 spelled
        # via chr() so the literal stays visible to readers).
        assert chr(0xEF04) not in out

    def test_pua_boundary_codepoints_dropped(self):
        # Boundary cases: U+E000 (PUA start) and U+F8FF (PUA end).
        for cp in (0xE000, 0xF8FF):
            body = B.v5_char(_ord("a")) + B.v5_char(cp) + B.v5_char(_ord("b"))
            payload = B.v5_prelude() + B.v5_line(body)
            out = _to_mathml(payload)
            assert "<mi>a</mi>" in out
            assert "<mi>b</mi>" in out
            assert chr(cp) not in out, f"U+{cp:04X} leaked"

    def test_non_pua_unicode_still_emitted(self):
        # U+0391 (Α — uppercase Greek alpha) is just outside any PUA
        # range; the suppression must not over-reach into real Unicode.
        body = B.v5_char(0x0391)
        payload = B.v5_prelude() + B.v5_line(body)
        out = _to_mathml(payload)
        assert "Α" in out  # uppercase alpha character preserved


# ---------------------------------------------------------------------------
# v5 — templates
# ---------------------------------------------------------------------------


class TestV5Fraction:
    def test_basic_fraction(self):
        # x / y as TMPL selector 11
        num = B.v5_simple_char_line(_ord("x"))
        den = B.v5_simple_char_line(_ord("y"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(11, [num, den]))
        out = _to_mathml(payload)
        assert "<mfrac>" in out
        assert "<mi>x</mi>" in out
        assert "<mi>y</mi>" in out

    def test_multi_atom_numerator_gets_mrow(self):
        # (a+b) / c
        num = B.v5_line(
            B.v5_char(_ord("a")) + B.v5_char(_ord("+")) + B.v5_char(_ord("b"))
        )
        den = B.v5_simple_char_line(_ord("c"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(11, [num, den]))
        out = _to_mathml(payload)
        assert "<mfrac>" in out
        # Numerator should be wrapped in mrow because it has 3 atoms.
        root = ET.fromstring(out)
        mfrac = _find_first(root, "mfrac")
        assert mfrac is not None
        num_child = mfrac[0]
        assert _local(num_child.tag) == "mrow"
        assert len(list(num_child)) == 3


class TestV5Radical:
    def test_square_root_with_empty_degree(self):
        # An empty degree slot (null LINE) means "square root".
        radicand = B.v5_simple_char_line(_ord("x"))
        deg = B.v5_null_line()
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(10, [radicand, deg]))
        out = _to_mathml(payload)
        assert "<msqrt>" in out
        assert "<mi>x</mi>" in out

    def test_nth_root(self):
        radicand = B.v5_simple_char_line(_ord("x"))
        deg = B.v5_simple_char_line(_ord("3"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(10, [radicand, deg]))
        out = _to_mathml(payload)
        assert "<mroot>" in out
        assert "<mi>x</mi>" in out
        assert "<mn>3</mn>" in out


class TestV5Scripts:
    def test_subscript(self):
        base = B.v5_simple_char_line(_ord("x"))
        sub = B.v5_simple_char_line(_ord("i"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(27, [base, sub]))
        out = _to_mathml(payload)
        assert "<msub>" in out
        assert "<mi>x</mi>" in out
        assert "<mi>i</mi>" in out

    def test_superscript(self):
        base = B.v5_simple_char_line(_ord("x"))
        sup = B.v5_simple_char_line(_ord("2"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(28, [base, sup]))
        out = _to_mathml(payload)
        assert "<msup>" in out
        assert "<mi>x</mi>" in out
        assert "<mn>2</mn>" in out

    def test_subscript_superscript(self):
        base = B.v5_simple_char_line(_ord("x"))
        sub = B.v5_simple_char_line(_ord("i"))
        sup = B.v5_simple_char_line(_ord("2"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(29, [base, sub, sup]))
        out = _to_mathml(payload)
        assert "<msubsup>" in out
        assert "<mi>i</mi>" in out
        assert "<mn>2</mn>" in out


class TestV5BigOperators:
    def test_summation_with_under_over(self):
        # ∑_{i=1}^{n} x_i — selector 16 (sum)
        body = B.v5_simple_char_line(_ord("x"))
        sub = B.v5_line(B.v5_char(_ord("i")) + B.v5_char(_ord("=")) + B.v5_char(_ord("1")))
        sup = B.v5_simple_char_line(_ord("n"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(16, [body, sub, sup]))
        out = _to_mathml(payload)
        assert "<munderover>" in out
        assert "<mo>∑</mo>" in out
        assert "<mi>n</mi>" in out

    def test_integral_without_limits(self):
        body = B.v5_simple_char_line(_ord("x"))
        sub = B.v5_null_line()
        sup = B.v5_null_line()
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(15, [body, sub, sup]))
        out = _to_mathml(payload)
        assert "<mo>∫</mo>" in out
        # No munder / mover wrapping the operator
        assert "<munderover>" not in out
        assert "<munder>" not in out


class TestV5Delimiters:
    def test_parentheses(self):
        # ( x )
        inner = B.v5_simple_char_line(_ord("x"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(1, [inner]))
        out = _to_mathml(payload)
        assert '<mo fence="true">(</mo>' in out
        assert '<mo fence="true">)</mo>' in out
        assert "<mi>x</mi>" in out

    def test_square_brackets(self):
        inner = B.v5_simple_char_line(_ord("a"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(3, [inner]))
        out = _to_mathml(payload)
        assert '<mo fence="true">[</mo>' in out
        assert '<mo fence="true">]</mo>' in out

    def test_absolute_value_bars(self):
        inner = B.v5_simple_char_line(_ord("a"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(4, [inner]))
        out = _to_mathml(payload)
        assert '<mo fence="true">|</mo>' in out


class TestV5CustomFence:
    """Selector 9 — MathType's "custom-bracket" fence.

    Used when the left and right brackets don't match: half-open
    intervals like ``[a, b)`` / ``(a, b]``, and the various
    ``{ } { }`` / ``) (`` combinations from old MathType installs.
    The body is slot 0 and the two trailing CHAR records hold the
    actual bracket characters.
    """

    def test_left_closed_right_open_interval(self):
        # [a, b)  — body "a,b" then '[' then ')'
        body = B.v5_line(
            B.v5_char(_ord("a")) + B.v5_char(_ord(",")) + B.v5_char(_ord("b"))
        )
        left = B.v5_char(_ord("["))
        right = B.v5_char(_ord(")"))
        payload = B.v5_prelude() + B.v5_line(
            B.v5_tmpl(9, [body, left, right], variation=0x12)
        )
        out = _to_mathml(payload)
        # Brackets wrap the body, not append after it.
        assert '<mrow><mo fence="true">[</mo>' in out
        assert '<mo fence="true">)</mo></mrow>' in out
        # Regression guard: the broken passthrough emitted brackets
        # AFTER the body. Make sure that shape never returns.
        assert "<mi>b</mi><mo>[</mo>" not in out
        assert "<mi>b</mi><mo>)</mo>" not in out

    def test_left_open_right_closed_interval(self):
        # (a, b]  — same template, different characters
        body = B.v5_line(
            B.v5_char(_ord("a")) + B.v5_char(_ord(",")) + B.v5_char(_ord("b"))
        )
        left = B.v5_char(_ord("("))
        right = B.v5_char(_ord("]"))
        payload = B.v5_prelude() + B.v5_line(
            B.v5_tmpl(9, [body, left, right], variation=0x12)
        )
        out = _to_mathml(payload)
        assert '<mrow><mo fence="true">(</mo>' in out
        assert '<mo fence="true">]</mo></mrow>' in out

    def test_selector_8_uses_same_handler(self):
        # MathType also uses selector 8 for the same custom-fence
        # template in some builds; both should produce a wrapping mrow.
        body = B.v5_line(B.v5_char(_ord("x")))
        left = B.v5_char(_ord("["))
        right = B.v5_char(_ord(")"))
        payload = B.v5_prelude() + B.v5_line(
            B.v5_tmpl(8, [body, left, right], variation=0x12)
        )
        out = _to_mathml(payload)
        assert '<mo fence="true">[</mo><mi>x</mi><mo fence="true">)</mo>' in out


class TestV5Matrix:
    def test_2x2_matrix(self):
        cells = [
            B.v5_simple_char_line(_ord("a")),
            B.v5_simple_char_line(_ord("b")),
            B.v5_simple_char_line(_ord("c")),
            B.v5_simple_char_line(_ord("d")),
        ]
        payload = B.v5_prelude() + B.v5_line(B.v5_matrix(2, 2, cells))
        out = _to_mathml(payload)
        assert "<mtable>" in out
        root = ET.fromstring(out)
        mtable = _find_first(root, "mtable")
        assert mtable is not None
        rows = list(mtable)
        assert len(rows) == 2
        for row in rows:
            assert _local(row.tag) == "mtr"
            assert len(list(row)) == 2


class TestV5LimitsAndAccents:
    def test_limit_template(self):
        # MTEF stores each glyph as its own CHAR record, so "lim" becomes
        # three independent <mi> elements — that's the spec-compliant
        # shape. We only assert the wrapper construct here.
        base = B.v5_line(
            B.v5_char(_ord("l")) + B.v5_char(_ord("i")) + B.v5_char(_ord("m"))
        )
        under = B.v5_simple_char_line(_ord("∞"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(23, [base, under]))
        out = _to_mathml(payload)
        assert "<munder>" in out
        assert "<mi>l</mi><mi>i</mi><mi>m</mi>" in out

    def test_vector_accent(self):
        base = B.v5_simple_char_line(_ord("v"))
        payload = B.v5_prelude() + B.v5_line(B.v5_tmpl(31, [base]))
        out = _to_mathml(payload)
        assert "<mover>" in out
        assert "→" in out


class TestV5Embellishments:
    def test_prime_via_embell(self):
        # x with single prime
        payload = B.v5_prelude() + B.v5_line(B.v5_char(_ord("x"), embells=[5]))
        out = _to_mathml(payload)
        assert "<mover>" in out
        assert "′" in out

    def test_dot_via_embell(self):
        payload = B.v5_prelude() + B.v5_line(B.v5_char(_ord("x"), embells=[2]))
        out = _to_mathml(payload)
        assert "<mover>" in out
        assert "˙" in out


# ---------------------------------------------------------------------------
# v3 — basic CHARs and templates
# ---------------------------------------------------------------------------


class TestV3BasicChars:
    def test_single_letter(self):
        payload = B.v3_prelude() + B.v3_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out

    def test_single_digit(self):
        payload = B.v3_prelude() + B.v3_simple_char_line(_ord("7"))
        out = _to_mathml(payload)
        assert "<mn>7</mn>" in out

    def test_run_of_chars(self):
        body = (
            B.v3_char(_ord("a"))
            + B.v3_char(_ord("+"))
            + B.v3_char(_ord("b"))
        )
        payload = B.v3_prelude() + B.v3_line(body)
        out = _to_mathml(payload)
        assert "<mi>a</mi>" in out
        assert "<mo>+</mo>" in out
        assert "<mi>b</mi>" in out


class TestV3Templates:
    def test_fraction_selector_14(self):
        num = B.v3_simple_char_line(_ord("x"))
        den = B.v3_simple_char_line(_ord("y"))
        payload = B.v3_prelude() + B.v3_line(B.v3_tmpl(14, [num, den]))
        out = _to_mathml(payload)
        assert "<mfrac>" in out
        assert "<mi>x</mi>" in out
        assert "<mi>y</mi>" in out

    def test_radical_selector_13(self):
        radicand = B.v3_simple_char_line(_ord("x"))
        deg = B.v3_null_line()
        payload = B.v3_prelude() + B.v3_line(B.v3_tmpl(13, [radicand, deg]))
        out = _to_mathml(payload)
        assert "<msqrt>" in out

    def test_scripts_selector_15_superscript(self):
        # variation=0x02 → superscript only
        base = B.v3_simple_char_line(_ord("x"))
        sup = B.v3_simple_char_line(_ord("2"))
        payload = B.v3_prelude() + B.v3_line(B.v3_tmpl(15, [base, sup], variation=0x02))
        out = _to_mathml(payload)
        assert "<msup>" in out
        assert "<mn>2</mn>" in out

    def test_scripts_selector_15_subscript(self):
        # variation=0x01 → subscript only
        base = B.v3_simple_char_line(_ord("x"))
        sub = B.v3_simple_char_line(_ord("i"))
        payload = B.v3_prelude() + B.v3_line(B.v3_tmpl(15, [base, sub], variation=0x01))
        out = _to_mathml(payload)
        assert "<msub>" in out

    def test_scripts_selector_15_both(self):
        # variation=0x03 → both
        base = B.v3_simple_char_line(_ord("x"))
        sub = B.v3_simple_char_line(_ord("i"))
        sup = B.v3_simple_char_line(_ord("2"))
        payload = B.v3_prelude() + B.v3_line(
            B.v3_tmpl(15, [base, sub, sup], variation=0x03)
        )
        out = _to_mathml(payload)
        assert "<msubsup>" in out

    def test_summation_selector_29(self):
        # ∑_{i=0}^{n} x_i — variation 0x03 = both scripts
        body = B.v3_simple_char_line(_ord("x"))
        sub = B.v3_simple_char_line(_ord("i"))
        sup = B.v3_simple_char_line(_ord("n"))
        payload = B.v3_prelude() + B.v3_line(
            B.v3_tmpl(29, [body, sub, sup], variation=0x03)
        )
        out = _to_mathml(payload)
        assert "<munderover>" in out
        assert "<mo>∑</mo>" in out

    def test_parens_selector_1(self):
        inner = B.v3_simple_char_line(_ord("x"))
        payload = B.v3_prelude() + B.v3_line(B.v3_tmpl(1, [inner]))
        out = _to_mathml(payload)
        assert '<mo fence="true">(</mo>' in out
        assert '<mo fence="true">)</mo>' in out


class TestV3Matrix:
    def test_simple_matrix(self):
        cells = [
            B.v3_simple_char_line(_ord("a")),
            B.v3_simple_char_line(_ord("b")),
            B.v3_simple_char_line(_ord("c")),
            B.v3_simple_char_line(_ord("d")),
        ]
        payload = B.v3_prelude() + B.v3_line(B.v3_matrix(2, 2, cells))
        out = _to_mathml(payload)
        root = ET.fromstring(out)
        mtable = _find_first(root, "mtable")
        assert mtable is not None
        assert len(list(mtable)) == 2


class TestRealMathTypeScriptLayout:
    """Scripts whose base is the *preceding sibling*, not slot 0.

    The synthetic tests in :class:`TestV5Scripts` / :class:`TestV3Templates`
    feed the base inside slot 0 (``v5_tmpl(28, [base, sup])``). Real
    MathType (DSMT6/7, the format Word actually emits) does the opposite:
    it writes the base as the CHAR/template *before* the script template
    and leaves the script's nucleus slot empty (a null LINE). The adapter
    must hoist that preceding sibling into the empty nucleus, otherwise
    ``x²`` decodes to ``<msup><mrow/><mn>2</mn></msup>`` with the real
    base ``x`` dangling outside the script — wrong for braille.

    These cases model the real wire layout and would all have failed
    before :func:`_attach_preceding_base`.
    """

    def _shape(self, mathml: str) -> str:
        root = ET.fromstring(mathml)

        def fmt(el: ET.Element) -> str:
            tag = _local(el.tag)
            kids = list(el)
            if kids:
                return f"{tag}({','.join(fmt(k) for k in kids)})"
            return f"{tag}:{el.text or ''}"

        return ",".join(fmt(c) for c in root)

    def test_v5_superscript_base_is_preceding_sibling(self):
        # x² as Word writes it: CHAR x, then a superscript whose base slot
        # is a null LINE.
        body = B.v5_char(_ord("x")) + B.v5_tmpl(
            28, [B.v5_null_line(), B.v5_simple_char_line(_ord("2"))]
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert self._shape(out) == "msup(mi:x,mn:2)"

    def test_v5_subscript_base_is_preceding_sibling(self):
        body = B.v5_char(_ord("x")) + B.v5_tmpl(
            27, [B.v5_null_line(), B.v5_simple_char_line(_ord("i"))]
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert self._shape(out) == "msub(mi:x,mi:i)"

    def test_v5_subsup_base_is_preceding_sibling(self):
        body = B.v5_char(_ord("x")) + B.v5_tmpl(
            29,
            [
                B.v5_null_line(),
                B.v5_simple_char_line(_ord("i")),
                B.v5_simple_char_line(_ord("2")),
            ],
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert self._shape(out) == "msubsup(mi:x,mi:i,mn:2)"

    def test_v5_leading_script_keeps_empty_base(self):
        # A script with no preceding sibling (nothing to hoist) must stay
        # well-formed with an empty nucleus rather than stealing an atom
        # from an outer scope.
        body = B.v5_tmpl(
            28, [B.v5_null_line(), B.v5_simple_char_line(_ord("2"))]
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert self._shape(out) == "msup(mrow:,mn:2)"

    def test_v5_only_immediate_sibling_is_taken(self):
        # 10ˣ → the base is just the adjacent '0', not '10'. MathType emits
        # CHAR 1, CHAR 0, then the script; only the '0' is the nucleus.
        body = (
            B.v5_char(_ord("1"))
            + B.v5_char(_ord("0"))
            + B.v5_tmpl(28, [B.v5_null_line(), B.v5_simple_char_line(_ord("x"))])
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert self._shape(out) == "mn:1,msup(mn:0,mi:x)"

    def test_v3_superscript_base_is_preceding_sibling(self):
        body = B.v3_char(_ord("x")) + B.v3_tmpl(
            15, [B.v3_null_line(), B.v3_simple_char_line(_ord("2"))], variation=0x02
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(body))
        assert self._shape(out) == "msup(mi:x,mn:2)"

    def test_slot0_base_layout_still_supported(self):
        # Backward-compat: an emitter that *does* put the base in slot 0
        # must keep working — the hoist only fires on an empty nucleus.
        body = B.v5_tmpl(
            28,
            [B.v5_simple_char_line(_ord("x")), B.v5_simple_char_line(_ord("2"))],
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert self._shape(out) == "msup(mi:x,mn:2)"


class TestRealDocxFixture:
    """Regression against bytes extracted from a real Word .docx.

    ``mathtype_v5_y_eq_x_cubed.bin`` is the verbatim ``Equation Native``
    OLE stream for ``y = x³`` produced by WPS/Word with MathType. Pinning
    the decoded shape guards the whole extract→decode path against the
    empty-script-base regression using ground-truth data, not synthesised
    bytes.
    """

    def _fixture(self, name: str) -> bytes:
        import pathlib

        path = pathlib.Path(__file__).parent / "fixtures" / name
        return path.read_bytes()

    def test_y_equals_x_cubed(self):
        out = _to_mathml(self._fixture("mathtype_v5_y_eq_x_cubed.bin"))
        root = ET.fromstring(out)

        def fmt(el: ET.Element) -> str:
            tag = _local(el.tag)
            kids = list(el)
            if kids:
                return f"{tag}({','.join(fmt(k) for k in kids)})"
            return f"{tag}:{el.text or ''}"

        assert ",".join(fmt(c) for c in root) == "mi:y,mo:=,msup(mi:x,mn:3)"


# ---------------------------------------------------------------------------
# Error paths
# ---------------------------------------------------------------------------


class TestErrorHandling:
    def test_empty_bytes(self):
        out = MtefMathSourceAdapter().to_mathml(b"")
        assert "<merror" in out

    def test_empty_hex_string(self):
        out = MtefMathSourceAdapter().to_mathml("")
        assert "<merror" in out

    def test_truncated_payload(self):
        # v5 prelude byte but nothing after
        out = MtefMathSourceAdapter().to_mathml(bytes([5]))
        assert "<merror" in out

    def test_unsupported_version(self):
        # Version 0 is illegal — we accept 2+
        out = MtefMathSourceAdapter().to_mathml(bytes([0, 0, 0, 0, 0]))
        assert "<merror" in out

    def test_hex_string_input_works(self):
        # Same payload as the basic v5 letter test, fed as hex.
        payload = B.v5_prelude() + B.v5_simple_char_line(_ord("x"))
        hex_str = payload.hex()
        out = MtefMathSourceAdapter().to_mathml(hex_str)
        assert "<mi>x</mi>" in out

    def test_eqnolefilehdr_is_stripped(self):
        # Prefix a fake 28-byte EQNOLEFILEHDR (cbHdr=28, version=0x00020000,
        # rest zeros) and check the v5 payload still parses.
        header = bytes([0x1C, 0x00]) + bytes([0x00, 0x00, 0x02, 0x00]) + bytes(22)
        assert len(header) == 28
        payload = header + B.v5_prelude() + B.v5_simple_char_line(_ord("x"))
        out = MtefMathSourceAdapter().to_mathml(payload)
        assert "<mi>x</mi>" in out


class TestSurrogateMtcode:
    """A corrupt / truncated stream can carry a UTF-16 surrogate half
    as MTCode.  ``chr()`` accepts it, so it used to flow into the
    MathML string and blow up the UTF-8 re-encode inside the normalizer
    (``UnicodeEncodeError``) — escaping the adapter's soft-failure
    contract and crashing the whole pipeline."""

    def test_lone_surrogates_are_dropped(self):
        from brailix.frontend.math.adapters.mtef._mathml import (
            _char_to_mathml,
        )

        assert _char_to_mathml(0xD800, []) == []
        assert _char_to_mathml(0xDFFF, []) == []

    def test_surrogate_payload_parses_end_to_end(self):
        from brailix.core.context import MathContext
        from brailix.frontend.math import parse_math_tree

        payload = B.v5_prelude() + B.v5_line(B.v5_char(0xD800)) + B.v5_end()
        ctx = MathContext(source="mtef")
        tree = parse_math_tree(payload, ctx)  # must not raise
        assert tree is not None
        # The poisonous character is gone — the tree re-serialises and
        # re-encodes cleanly.
        ET.tostring(tree, encoding="unicode").encode("utf-8")


# ---------------------------------------------------------------------------
# v5 — EQN_PREFS and inline RULER (regression: MathType-emitted docs)
# ---------------------------------------------------------------------------


class TestV5EqnPrefsAndInlineRuler:
    """Pin behaviour against malformed-but-real MathType emitter output.

    Before the fix, ``_skip_eqn_prefs_v5`` treated the styles section as
    a nibble stream and consumed the rest of the buffer, leaving the
    equation body empty. Real ``.docx`` files (MathType 6 + Word 2016)
    triggered this on every formula. The tests below synthesise the
    same byte patterns.
    """

    def test_eqn_prefs_stops_before_equation_body(self):
        # opts=0, no sizes/spaces, 3 styles (one zero, two nonzero).
        # If the parser treats styles as a nibble stream it will eat the
        # following CHAR; the per-spec form stops on the right byte.
        prefs = B.v5_eqn_prefs(
            styles=[(0, None), (1, 0x00), (2, 0x02)],
        )
        body = B.v5_simple_char_line(_ord("x"))
        payload = B.v5_prelude() + prefs + body
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_eqn_prefs_with_sizes_and_spaces(self):
        # A single sized dimension (unit=2 points, value="12", F terminator)
        # packed high-nibble first → byte 0x21 then 0x2F.
        size_stream = bytes([0x21, 0x2F])
        # A single space dimension (unit=4 percentage, value="150"):
        # nibbles 4,1,5,0,F → bytes 0x41, 0x50, 0xF0 (high-pad 0).
        space_stream = bytes([0x41, 0x50, 0xF0])
        prefs = B.v5_eqn_prefs(
            sizes=[size_stream],
            spaces=[space_stream],
            styles=[(1, 0x00)],
        )
        body = B.v5_simple_char_line(_ord("y"))
        payload = B.v5_prelude() + prefs + body
        out = _to_mathml(payload)
        assert "<mi>y</mi>" in out
        assert "merror" not in out

    def test_inline_ruler_after_line_opts(self):
        # LINE opts=0x02 followed by ruler data WITHOUT a leading 0x07
        # tag — the layout MathType actually writes.
        body = B.v5_char(_ord("a"))
        line = B.v5_inline_ruler_line(body, stops=[(0, 0x1470)])
        payload = B.v5_prelude() + line
        out = _to_mathml(payload)
        assert "<mi>a</mi>" in out
        assert "merror" not in out

    def test_inline_ruler_accepts_explicit_ruler_tag(self):
        # Same flow but WITH the 0x07 RULER tag still present — the
        # parser should accept both forms.
        body = B.v5_char(_ord("b"))
        # Manually build: 0x01 0x02 0x07 <count> <type> <offset_lo> <offset_hi>
        # body END
        payload = (
            B.v5_prelude()
            + bytes([0x01, 0x02, 0x07, 0x01, 0x00])
            + B.u16_le(0)
            + body
            + bytes([0x00])
        )
        out = _to_mathml(payload)
        assert "<mi>b</mi>" in out
        assert "merror" not in out

    def test_inline_ruler_with_seven_stops_keeps_body(self):
        # Regression: an inline ruler whose stop COUNT is exactly 7 has a
        # leading byte equal to the RULER tag (0x07). The parser used to
        # mistake that count for a tag, desync, and silently drop the whole
        # line body with no <merror>. It must keep the body now (the body
        # parse falls back to the tagged reading only when inline fails).
        body = B.v5_char(_ord("z"))
        line = B.v5_inline_ruler_line(body, stops=[(0, i) for i in range(7)])
        out = _to_mathml(B.v5_prelude() + line)
        assert "<mi>z</mi>" in out
        assert "merror" not in out

    def test_color_def_inside_tmpl_slot(self):
        # COLOR_DEF (0x10) can appear anywhere — including inside a TMPL
        # slot list — because definitions need only precede first use.
        # Build: TMPL selector=11 (fraction) where the numerator slot
        # contains a COLOR_DEF then a LINE.
        color_def = bytes([0x10, 0x00, 0, 0, 0, 0, 0, 0])  # opts=0, RGB 0,0,0
        num_slot = color_def + B.v5_simple_char_line(_ord("p"))
        den_slot = B.v5_simple_char_line(_ord("q"))
        frac = B.v5_tmpl(11, [num_slot, den_slot])
        payload = B.v5_prelude() + B.v5_line(frac)
        out = _to_mathml(payload)
        assert "<mi>p</mi>" in out
        assert "<mi>q</mi>" in out
        assert "<mfrac>" in out
        assert "merror" not in out


# ---------------------------------------------------------------------------
# Adapter contract
# ---------------------------------------------------------------------------


class TestAdapterContract:
    def test_source_field(self):
        assert MtefMathSourceAdapter().source == "mtef"

    def test_output_is_well_formed_mathml(self):
        payload = B.v5_prelude() + B.v5_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        # Should parse as XML without error.
        root = ET.fromstring(out)
        assert root.tag.endswith("math")

    def test_registered_under_mtef(self):
        from brailix.frontend.math.registry import math_source_registry

        adapter = math_source_registry.get("mtef")
        assert isinstance(adapter, MtefMathSourceAdapter)
        assert adapter.source == "mtef"

    def test_satisfies_protocol(self):
        from brailix.core.protocols import MathSourceAdapter

        adapter = MtefMathSourceAdapter()
        assert isinstance(adapter, MathSourceAdapter)


# ---------------------------------------------------------------------------
# Shared helpers for the record-level coverage tests below
# ---------------------------------------------------------------------------


def _tree_shape(mathml: str) -> str:
    """Serialise the element tree as ``tag(child,...)`` / ``tag:text``."""
    root = ET.fromstring(mathml)

    def fmt(el: ET.Element) -> str:
        tag = _local(el.tag)
        kids = list(el)
        if kids:
            return f"{tag}({','.join(fmt(k) for k in kids)})"
        return f"{tag}:{el.text or ''}"

    return ",".join(fmt(c) for c in root)


def _v3tag(rec: int, opts: int = 0) -> bytes:
    """v3 tag byte: record type in the low nibble, options in the high."""
    return bytes([((opts & 0x0F) << 4) | (rec & 0x0F)])


# ---------------------------------------------------------------------------
# v5 — styling / definition records interleaved with content
# ---------------------------------------------------------------------------


class TestV5StylingRecordSkips:
    """Style and definition records emit no MathML — braille rendering is
    style-agnostic — but the parser must consume their exact byte
    payloads or the equation body after them turns to garbage."""

    def test_stray_embell_at_object_list_is_skipped(self):
        stray = bytes([0x06, 0x00, 0x02])  # EMBELL outside any CHAR
        payload = B.v5_prelude() + stray + B.v5_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_ruler_record_at_object_list_is_skipped(self):
        ruler = bytes([0x07, 0x01, 0x00]) + B.u16_le(0x0120)
        payload = B.v5_prelude() + ruler + B.v5_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_font_style_def_is_skipped(self):
        font_style = bytes([0x08, 0x01, 0x00])  # index 1, plain style
        payload = (
            B.v5_prelude() + font_style + B.v5_simple_char_line(_ord("x"))
        )
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_size_records_all_three_flavours(self):
        # Per the WIRIS MTEF spec (v3 and v5 share this encoding):
        #   101 → explicit point size (16-bit);
        #   100 → large delta: lsize typesize byte, then 16-bit dsize;
        #   else → small delta: the byte is lsize, then dsize+128.
        # All three must consume exactly their own bytes (the 100 case is
        # 4 bytes after the tag, NOT 3 — omitting the lsize byte desyncs).
        sizes = (
            bytes([0x09, 101]) + B.u16_le(240)
            + bytes([0x09, 100, 3]) + B.u16_le(0xFFF6)
            + bytes([0x09, 50, 130])
        )
        payload = B.v5_prelude() + sizes + B.v5_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_typesize_shorthand_tags_are_skipped(self):
        shorthand = bytes([0x0A, 0x0B, 0x0C, 0x0D, 0x0E])  # FULL..SUBSYM
        payload = (
            B.v5_prelude() + shorthand + B.v5_simple_char_line(_ord("x"))
        )
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_future_record_skipped_by_declared_length(self):
        # Records >= 0x64 are reserved for future use and carry an
        # explicit length so old parsers can hop over them.
        future = bytes([0x64, 0x03, 0xAA, 0xBB, 0xCC])
        payload = B.v5_prelude() + future + B.v5_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_eqn_prefs_truncated_styles_section(self):
        # The styles count claims one entry but the stream ends — the
        # skipper must bail out instead of reading past the buffer.
        prefs = bytes([0x12, 0x00, 0x00, 0x00, 0x01])
        payload = B.v5_prelude() + prefs
        out = _to_mathml(payload)
        assert "merror" not in out


class TestV5Nudges:
    def test_nudged_line(self):
        line = bytes([0x01, 0x08]) + B.nudge_small() + B.v5_char(_ord("x")) + B.v5_end()
        out = _to_mathml(B.v5_prelude() + line)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_wide_nudge_consumes_six_bytes(self):
        line = bytes([0x01, 0x08]) + B.nudge_wide() + B.v5_char(_ord("x")) + B.v5_end()
        out = _to_mathml(B.v5_prelude() + line)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_line_spacing_flag(self):
        line = bytes([0x01, 0x04]) + B.u16_le(120) + B.v5_char(_ord("x")) + B.v5_end()
        out = _to_mathml(B.v5_prelude() + line)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_nudged_char(self):
        char = bytes([0x02, 0x08]) + B.nudge_small() + bytes([128]) + B.u16_le(_ord("x"))
        out = _to_mathml(B.v5_prelude() + B.v5_line(char))
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_nudged_tmpl(self):
        tmpl = (
            bytes([0x03, 0x08])
            + B.nudge_small()
            + bytes([11, 0, 0])  # fraction selector, variation, options
            + B.v5_simple_char_line(_ord("a"))
            + B.v5_simple_char_line(_ord("b"))
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert "<mfrac>" in out
        assert "merror" not in out


class TestV5CharVariants:
    def test_char_with_16bit_font_position(self):
        char = bytes([0x02, 0x10, 128]) + B.u16_le(_ord("x")) + B.u16_le(0x1234)
        out = _to_mathml(B.v5_prelude() + B.v5_line(char))
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_extended_typeface_form(self):
        # Typeface escapes the one-byte biased form with a 0xFF leader
        # followed by a signed 16-bit value.
        char = bytes([0x02, 0x00, 0xFF]) + B.u16_le(700) + B.u16_le(_ord("x"))
        out = _to_mathml(B.v5_prelude() + B.v5_line(char))
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_whitespace_char_emits_nothing(self):
        body = B.v5_char(_ord("a")) + B.v5_char(0x20) + B.v5_char(_ord("b"))
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert _tree_shape(out) == "mi:a,mi:b"


class TestV5EmbellVariants:
    def test_slash_embell_renders_overlay(self):
        out = _to_mathml(
            B.v5_prelude() + B.v5_line(B.v5_char(_ord("a"), embells=[10]))
        )
        # Combining long solidus overlay next to the base.
        assert "̸" in out

    def test_underbar_embell_uses_munder(self):
        out = _to_mathml(
            B.v5_prelude() + B.v5_line(B.v5_char(_ord("a"), embells=[16]))
        )
        assert "<munder>" in out

    def test_unknown_embell_code_leaves_base_alone(self):
        out = _to_mathml(
            B.v5_prelude() + B.v5_line(B.v5_char(_ord("a"), embells=[99]))
        )
        assert _tree_shape(out) == "mi:a"

    def test_nudged_embell_record(self):
        char = (
            bytes([0x02, 0x01, 128])
            + B.u16_le(_ord("a"))
            + bytes([0x06, 0x08])
            + B.nudge_small()
            + bytes([2])  # dot above
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(char))
        assert "<mover>" in out

    def test_style_records_inside_embell_list_are_skipped(self):
        char = (
            bytes([0x02, 0x01, 128])
            + B.u16_le(_ord("a"))
            + bytes([0x09, 50, 130])     # SIZE, one-byte flavour
            + bytes([0x0F, 0x02])        # COLOR
            + bytes([0x0A])              # FULL shorthand
            + bytes([0x08, 0x01, 0x00])  # FONT_STYLE_DEF
            + bytes([0x06, 0x00, 2])     # the actual dot-above embell
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(char))
        assert "<mover>" in out
        assert "merror" not in out

    def test_unknown_record_in_embell_list_is_error(self):
        char = (
            bytes([0x02, 0x01, 128])
            + B.u16_le(_ord("a"))
            + bytes([0x05])  # MATRIX record can't appear here
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(char))
        assert "<merror" in out


# ---------------------------------------------------------------------------
# v5 — pile and matrix variants
# ---------------------------------------------------------------------------


class TestV5PileVariants:
    def test_pile_at_top_level(self):
        pile = B.v5_pile(
            [
                B.v5_line(B.v5_char(_ord("a"))),
                B.v5_line(B.v5_char(_ord("b"))),
            ]
        )
        out = _to_mathml(B.v5_prelude() + pile)
        root = ET.fromstring(out)
        mtable = _find_first(root, "mtable")
        assert mtable is not None
        assert len(list(mtable)) == 2

    def test_nudged_pile_with_inline_ruler(self):
        pile = (
            bytes([0x04, 0x0A])  # PILE, opts = nudge | ruler
            + B.nudge_small()
            + bytes([1, 0])  # halign, valign
            + bytes([0x01, 0x00]) + B.u16_le(0)  # inline ruler, one stop
            + B.v5_line(B.v5_char(_ord("a")))
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + pile)
        assert "<mtable>" in out
        assert "<mi>a</mi>" in out
        assert "merror" not in out

    def test_pile_inline_ruler_with_seven_stops_keeps_body(self):
        # The same 7-stop / RULER-tag (0x07) collision on the PILE ruler
        # path — the body must survive, not be silently dropped.
        pile = (
            bytes([0x04, 0x02])  # PILE, opts = ruler
            + bytes([1, 0])  # halign, valign
            + bytes([7])  # inline ruler, seven stops (== RULER tag 0x07)
            + b"".join(bytes([0]) + B.u16_le(i) for i in range(7))
            + B.v5_line(B.v5_char(_ord("q")))
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + pile)
        assert "<mtable>" in out
        assert "<mi>q</mi>" in out
        assert "merror" not in out

    def test_pile_inside_tmpl_slot(self):
        pile = B.v5_pile([B.v5_line(B.v5_char(_ord("a")))])
        fence = B.v5_tmpl(1, [pile])  # parenthesised pile
        out = _to_mathml(B.v5_prelude() + B.v5_line(fence))
        assert "<mtable>" in out
        assert '<mo fence="true">(</mo>' in out

    def test_unexpected_record_in_pile_is_error(self):
        pile = bytes([0x04, 0x00, 1, 0]) + B.v5_char(_ord("a")) + B.v5_end()
        out = _to_mathml(B.v5_prelude() + pile)
        assert "<merror" in out


class TestV5MatrixVariants:
    def test_nudged_matrix(self):
        matrix = (
            bytes([0x05, 0x08])
            + B.nudge_small()
            + bytes([0, 0, 0, 1, 1])  # valign, h_just, v_just, rows, cols
            + bytes(1)  # row partition bits
            + bytes(1)  # col partition bits
            + B.v5_line(B.v5_char(_ord("a")))
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + matrix)
        assert "<mtable>" in out
        assert "<mi>a</mi>" in out
        assert "merror" not in out

    def test_truncated_matrix_pads_empty_cells(self):
        # Declares 2x2 but carries only one LINE cell; the reader pads
        # the remaining cells as empty <mtd> instead of crashing.
        matrix = (
            bytes([0x05, 0x00, 0, 0, 0, 2, 2])
            + bytes(1)
            + bytes(1)
            + B.v5_line(B.v5_char(_ord("a")))
            + bytes([0x00, 0x00, 0x00])  # END markers for the missing cells
            + B.v5_end()
        )
        out = _to_mathml(B.v5_prelude() + matrix)
        root = ET.fromstring(out)
        mtable = _find_first(root, "mtable")
        assert mtable is not None
        rows = list(mtable)
        assert len(rows) == 2
        assert all(len(list(rw)) == 2 for rw in rows)

    def test_matrix_inside_tmpl_slot(self):
        matrix = B.v5_matrix(1, 1, [B.v5_line(B.v5_char(_ord("a")))])
        fence = B.v5_tmpl(1, [matrix])
        out = _to_mathml(B.v5_prelude() + B.v5_line(fence))
        assert "<mtable>" in out
        assert '<mo fence="true">(</mo>' in out

    def test_non_line_record_in_matrix_cell_is_error(self):
        matrix = (
            bytes([0x05, 0x00, 0, 0, 0, 1, 1])
            + bytes(1)
            + bytes(1)
            + B.v5_char(_ord("a"))  # CHAR where a LINE cell must sit
        )
        out = _to_mathml(B.v5_prelude() + matrix)
        assert "<merror" in out


# ---------------------------------------------------------------------------
# v5 — template selector variants
# ---------------------------------------------------------------------------


class TestV5TmplVariants:
    def test_extended_variation_byte(self):
        # The high bit on the variation byte announces a second
        # variation byte (templates with >256 variations); handlers
        # branch on the low byte only.
        tmpl = (
            bytes([0x03, 0x00, 28, 0x80, 0x42, 0x00])
            + B.v5_null_line()
            + B.v5_simple_char_line(_ord("2"))
            + B.v5_end()
        )
        body = B.v5_char(_ord("x")) + tmpl
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert _tree_shape(out) == "msup(mi:x,mn:2)"

    def test_unknown_selector_passes_slots_through(self):
        tmpl = B.v5_tmpl(
            26,
            [
                B.v5_simple_char_line(_ord("a")),
                B.v5_simple_char_line(_ord("b")),
            ],
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert _tree_shape(out) == "mi:a,mi:b"
        assert "merror" not in out

    def test_fraction_with_no_slots_keeps_mathml_well_formed(self):
        out = _to_mathml(B.v5_prelude() + B.v5_line(B.v5_tmpl(11, [])))
        assert _tree_shape(out) == "mfrac(mrow:,mrow:)"

    def test_template_after_sibling_stays_sibling(self):
        # Only script templates hoist the preceding sibling; a fraction
        # following a CHAR leaves the CHAR where it is.
        body = B.v5_char(_ord("x")) + B.v5_tmpl(
            11,
            [
                B.v5_simple_char_line(_ord("a")),
                B.v5_simple_char_line(_ord("b")),
            ],
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert _tree_shape(out) == "mi:x,mfrac(mi:a,mi:b)"

    def test_underbar_selector_12(self):
        tmpl = B.v5_tmpl(12, [B.v5_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert _tree_shape(out) == "munder(mi:x,mo:¯)"

    def test_overbar_selector_13(self):
        tmpl = B.v5_tmpl(13, [B.v5_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert _tree_shape(out) == "mover(mi:x,mo:¯)"

    def test_arrow_selector_14(self):
        tmpl = B.v5_tmpl(14, [B.v5_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert _tree_shape(out) == "mover(mi:x,mo:→)"

    def test_hbrace_over_selector_24(self):
        tmpl = B.v5_tmpl(24, [B.v5_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert _tree_shape(out) == "mover(mi:x,mo:⏞)"

    def test_hbrace_under_selector_25(self):
        tmpl = B.v5_tmpl(25, [B.v5_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert _tree_shape(out) == "munder(mi:x,mo:⏟)"

    def test_bigop_with_subscript_only(self):
        tmpl = B.v5_tmpl(
            16,
            [
                B.v5_simple_char_line(_ord("x")),
                B.v5_simple_char_line(_ord("k")),
            ],
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert "<munder>" in out
        assert "<munderover>" not in out

    def test_bigop_with_superscript_only(self):
        tmpl = B.v5_tmpl(
            16,
            [
                B.v5_simple_char_line(_ord("x")),
                B.v5_null_line(),
                B.v5_simple_char_line(_ord("n")),
            ],
        )
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert "<mover>" in out
        assert "<munderover>" not in out

    def test_custom_fence_with_body_only(self):
        tmpl = B.v5_tmpl(8, [B.v5_simple_char_line(_ord("a"))])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        # No bracket slots → the fence row carries just the body.
        assert _tree_shape(out) == "mrow(mi:a)"

    def test_custom_fence_ignores_multi_atom_bracket_slot(self):
        bracket = B.v5_line(B.v5_char(_ord("[")) + B.v5_char(_ord("(")))
        tmpl = B.v5_tmpl(8, [B.v5_simple_char_line(_ord("a")), bracket])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert 'fence="true"' not in out
        assert "<mi>a</mi>" in out

    def test_definition_records_inside_tmpl_slot_list(self):
        # Definitions only need to precede first use, so every
        # definition / styling record may sit inside a TMPL slot list.
        recs = (
            bytes([0x09, 50, 130])                      # SIZE
            + bytes([0x0F, 0x02])                       # COLOR
            + bytes([0x0A])                             # FULL shorthand
            + bytes([0x08, 0x01, 0x00])                 # FONT_STYLE_DEF
            + bytes([0x07, 0x00])                       # RULER, zero stops
            + bytes([0x10, 0x05])                       # COLOR_DEF: RGBA+name
            + B.u16_le(1) + B.u16_le(2) + B.u16_le(3) + B.u16_le(4)
            + b"red\x00"
            + bytes([0x11, 0x01]) + b"Euclid\x00"       # FONT_DEF
            + bytes([0x11, 0xFF]) + B.u16_le(300)       # FONT_DEF, extended
            + b"Euclid Extra\x00"
            + bytes([0x12, 0x00, 0x00, 0x00, 0x00])     # EQN_PREFS, empty
            + bytes([0x13]) + b"MTEF\x00"               # ENCODING_DEF
        )
        slot = recs + B.v5_line(B.v5_char(_ord("a")))
        tmpl = B.v5_tmpl(1, [slot])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert "<mi>a</mi>" in out
        assert '<mo fence="true">(</mo>' in out
        assert "merror" not in out

    def test_unknown_record_in_tmpl_slot_list_is_error(self):
        tmpl = B.v5_tmpl(1, [bytes([0x20])])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert "<merror" in out


# ---------------------------------------------------------------------------
# v5 — malformed input
# ---------------------------------------------------------------------------


class TestV5Malformed:
    def test_future_version_prelude_is_error(self):
        payload = bytes([6, 1, 0, 11, 0]) + b"DSMT7\x00" + bytes([0])
        payload += B.v5_simple_char_line(_ord("x"))
        out = MtefMathSourceAdapter().to_mathml(payload)
        assert "<merror" in out

    def test_unknown_record_type_is_error(self):
        payload = B.v5_prelude() + bytes([0x20])
        out = _to_mathml(payload)
        assert "<merror" in out

    def test_line_nesting_too_deep_is_error(self):
        body = B.v5_char(_ord("x"))
        for _ in range(70):
            body = B.v5_line(body)
        out = _to_mathml(B.v5_prelude() + body)
        assert "<merror" in out

    def test_tmpl_nesting_too_deep_is_error(self):
        tmpl = B.v5_simple_char_line(_ord("x"))
        for _ in range(70):
            tmpl = B.v5_tmpl(1, [tmpl])
        out = _to_mathml(B.v5_prelude() + B.v5_line(tmpl))
        assert "<merror" in out

    def test_char_truncated_mid_mtcode_is_error(self):
        # CHAR promises a 16-bit MTCode but the stream ends after one byte.
        payload = B.v5_prelude() + bytes([0x02, 0x00, 128, 0x78])
        out = _to_mathml(payload)
        assert "<merror" in out


# ---------------------------------------------------------------------------
# v3 — option flags and styling records
# ---------------------------------------------------------------------------


class TestV3RecordSkips:
    def test_line_with_nudge_spacing_and_ruler(self):
        # All three option payloads in their wire order: nudge, then the
        # one-byte line spacing, then the RULER record.
        line = (
            _v3tag(0x01, 0x0E)
            + B.nudge_small()
            + bytes([12])  # line spacing
            + _v3tag(0x07) + bytes([0x01, 0x00]) + B.u16_le(0)
            + B.v3_char(_ord("x"))
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + line)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_line_ruler_flag_without_ruler_record_is_error(self):
        line = _v3tag(0x01, 0x02) + B.v3_char(_ord("x")) + B.v3_end()
        out = _to_mathml(B.v3_prelude() + line)
        assert "<merror" in out

    def test_nudged_char(self):
        char = _v3tag(0x02, 0x08) + B.nudge_small() + bytes([128]) + B.u16_le(_ord("x"))
        out = _to_mathml(B.v3_prelude() + B.v3_line(char))
        assert "<mi>x</mi>" in out

    def test_wide_nudge_consumes_six_bytes(self):
        line = _v3tag(0x01, 0x08) + B.nudge_wide() + B.v3_char(_ord("x")) + B.v3_end()
        out = _to_mathml(B.v3_prelude() + line)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_stray_embells_at_object_list_are_skipped(self):
        strays = (
            _v3tag(0x06, 0x08) + B.nudge_small() + bytes([2])  # nudged
            + _v3tag(0x06) + bytes([2])                        # plain
        )
        payload = B.v3_prelude() + strays + B.v3_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_ruler_record_at_object_list_is_skipped(self):
        ruler = _v3tag(0x07) + bytes([0x01, 0x00]) + B.u16_le(0x0120)
        payload = B.v3_prelude() + ruler + B.v3_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_font_record_is_skipped(self):
        font = _v3tag(0x08) + bytes([129, 1]) + b"Symbol\x00"
        payload = B.v3_prelude() + font + B.v3_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_size_records_all_three_flavours(self):
        sizes = (
            _v3tag(0x09) + bytes([101]) + B.u16_le(240)
            + _v3tag(0x09) + bytes([100, 3]) + B.u16_le(0xFFF6)
            + _v3tag(0x09) + bytes([50, 130])
        )
        payload = B.v3_prelude() + sizes + B.v3_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_typesize_shorthand_tags_are_skipped(self):
        shorthand = (
            _v3tag(0x0A) + _v3tag(0x0B) + _v3tag(0x0C)
            + _v3tag(0x0D) + _v3tag(0x0E)
        )
        payload = B.v3_prelude() + shorthand + B.v3_simple_char_line(_ord("x"))
        out = _to_mathml(payload)
        assert "<mi>x</mi>" in out
        assert "merror" not in out

    def test_unknown_record_type_is_error(self):
        payload = B.v3_prelude() + _v3tag(0x0F)
        out = _to_mathml(payload)
        assert "<merror" in out

    def test_line_nesting_too_deep_is_error(self):
        body = B.v3_char(_ord("x"))
        for _ in range(70):
            body = B.v3_line(body)
        out = _to_mathml(B.v3_prelude() + body)
        assert "<merror" in out


class TestV3CharEmbells:
    def test_char_with_embell_list(self):
        out = _to_mathml(
            B.v3_prelude() + B.v3_line(B.v3_char(_ord("a"), embells=[2]))
        )
        assert "<mover>" in out

    def test_nudged_embell_record(self):
        char = (
            _v3tag(0x02, 0x0A)  # nudge + embell flags
            + B.nudge_small()
            + bytes([128])
            + B.u16_le(_ord("a"))
            + _v3tag(0x06, 0x08) + B.nudge_small() + bytes([2])
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(char))
        assert "<mover>" in out

    def test_style_records_inside_embell_list_are_skipped(self):
        char = (
            _v3tag(0x02, 0x02)
            + bytes([128])
            + B.u16_le(_ord("a"))
            + _v3tag(0x09) + bytes([50, 130])           # SIZE
            + _v3tag(0x0A)                              # FULL shorthand
            + _v3tag(0x08) + bytes([129, 1]) + b"X\x00"  # FONT
            + _v3tag(0x06) + bytes([2])
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(char))
        assert "<mover>" in out
        assert "merror" not in out

    def test_unknown_record_in_embell_list_is_error(self):
        char = (
            _v3tag(0x02, 0x02)
            + bytes([128])
            + B.u16_le(_ord("a"))
            + _v3tag(0x05)
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(char))
        assert "<merror" in out


# ---------------------------------------------------------------------------
# v3 — template selector variants
# ---------------------------------------------------------------------------


class TestV3TemplateVariants:
    def test_nudged_tmpl(self):
        tmpl = (
            _v3tag(0x03, 0x08)
            + B.nudge_small()
            + bytes([14, 0, 0])
            + B.v3_simple_char_line(_ord("a"))
            + B.v3_simple_char_line(_ord("b"))
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mfrac>" in out

    def test_root_with_index_selector_13(self):
        tmpl = B.v3_tmpl(
            13,
            [
                B.v3_simple_char_line(_ord("x")),
                B.v3_simple_char_line(_ord("3")),
            ],
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mroot(mi:x,mn:3)"

    def test_slash_fraction_selector_41(self):
        tmpl = B.v3_tmpl(
            41,
            [
                B.v3_simple_char_line(_ord("a")),
                B.v3_simple_char_line(_ord("b")),
            ],
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mrow(mi:a,mo:/,mi:b)"

    def test_scripts_variation_zero_passes_base_through(self):
        tmpl = B.v3_tmpl(15, [B.v3_simple_char_line(_ord("x"))], variation=0)
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mi:x"

    def test_underbar_selector_16(self):
        tmpl = B.v3_tmpl(16, [B.v3_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "munder(mi:x,mo:¯)"

    def test_overbar_selector_17(self):
        tmpl = B.v3_tmpl(17, [B.v3_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mover(mi:x,mo:¯)"

    def test_bigop_with_subscript_only(self):
        tmpl = B.v3_tmpl(
            29,
            [
                B.v3_simple_char_line(_ord("x")),
                B.v3_simple_char_line(_ord("k")),
            ],
            variation=0x01,
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<munder>" in out
        assert "<munderover>" not in out

    def test_bigop_with_superscript_only(self):
        tmpl = B.v3_tmpl(
            29,
            [
                B.v3_simple_char_line(_ord("x")),
                B.v3_null_line(),
                B.v3_simple_char_line(_ord("n")),
            ],
            variation=0x02,
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mover>" in out
        assert "<munderover>" not in out

    def test_bigop_without_scripts(self):
        tmpl = B.v3_tmpl(29, [B.v3_simple_char_line(_ord("x"))], variation=0)
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mrow(mo:∑,mi:x)"

    def test_limits_selector_39(self):
        tmpl = B.v3_tmpl(
            39,
            [
                B.v3_simple_char_line(_ord("f")),
                B.v3_simple_char_line(_ord("n")),
            ],
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "munder(mi:f,mi:n)"

    def test_hbrace_over_selector_27(self):
        tmpl = B.v3_tmpl(27, [B.v3_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mover(mi:x,mo:⏞)"

    def test_hbrace_under_selector_28(self):
        tmpl = B.v3_tmpl(28, [B.v3_simple_char_line(_ord("x"))])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "munder(mi:x,mo:⏟)"

    def test_left_prescripts_selector_44(self):
        tmpl = B.v3_tmpl(
            44,
            [
                B.v3_simple_char_line(_ord("U")),
                B.v3_simple_char_line(_ord("Z")),
                B.v3_simple_char_line(_ord("A")),
            ],
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mmultiscripts>" in out
        assert "<mprescripts" in out

    def test_bare_char_as_tmpl_slot(self):
        # v3 allows a bare CHAR record (no LINE wrapper) per slot.
        tmpl = (
            _v3tag(0x03)
            + bytes([14, 0, 0])
            + B.v3_char(_ord("x"))
            + B.v3_char(_ord("y"))
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert _tree_shape(out) == "mfrac(mi:x,mi:y)"

    def test_nested_tmpl_as_slot(self):
        inner = B.v3_tmpl(1, [B.v3_simple_char_line(_ord("a"))])
        tmpl = B.v3_tmpl(14, [inner, B.v3_simple_char_line(_ord("b"))])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mfrac>" in out
        assert '<mo fence="true">(</mo>' in out

    def test_pile_as_tmpl_slot(self):
        pile = B.v3_pile([B.v3_simple_char_line(_ord("a"))])
        tmpl = B.v3_tmpl(1, [pile])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mtable>" in out

    def test_matrix_as_tmpl_slot(self):
        matrix = B.v3_matrix(1, 1, [B.v3_simple_char_line(_ord("a"))])
        tmpl = B.v3_tmpl(1, [matrix])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mtable>" in out

    def test_style_records_inside_tmpl_slot_list(self):
        recs = (
            _v3tag(0x09) + bytes([50, 130])
            + _v3tag(0x0A)
            + _v3tag(0x08) + bytes([129, 1]) + b"X\x00"
        )
        tmpl = B.v3_tmpl(1, [recs + B.v3_simple_char_line(_ord("a"))])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<mi>a</mi>" in out
        assert "merror" not in out

    def test_unknown_record_in_tmpl_slot_list_is_error(self):
        tmpl = B.v3_tmpl(1, [_v3tag(0x0F)])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<merror" in out

    def test_tmpl_nesting_too_deep_is_error(self):
        tmpl = B.v3_simple_char_line(_ord("x"))
        for _ in range(70):
            tmpl = B.v3_tmpl(1, [tmpl])
        out = _to_mathml(B.v3_prelude() + B.v3_line(tmpl))
        assert "<merror" in out


# ---------------------------------------------------------------------------
# v3 — pile and matrix variants
# ---------------------------------------------------------------------------


class TestV3PileAndMatrix:
    def test_pile_at_top_level(self):
        pile = B.v3_pile(
            [
                B.v3_simple_char_line(_ord("a")),
                B.v3_simple_char_line(_ord("b")),
            ]
        )
        out = _to_mathml(B.v3_prelude() + pile)
        root = ET.fromstring(out)
        mtable = _find_first(root, "mtable")
        assert mtable is not None
        assert len(list(mtable)) == 2

    def test_nudged_pile(self):
        pile = (
            _v3tag(0x04, 0x08)
            + B.nudge_small()
            + bytes([1, 0])
            + B.v3_simple_char_line(_ord("a"))
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + pile)
        assert "<mtable>" in out
        assert "merror" not in out

    def test_pile_with_ruler_option(self):
        pile = (
            _v3tag(0x04, 0x02)
            + bytes([1, 0])
            + _v3tag(0x07) + bytes([0x00])  # RULER record, zero stops
            + B.v3_simple_char_line(_ord("a"))
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + pile)
        assert "<mtable>" in out
        assert "merror" not in out

    def test_unexpected_record_in_pile_is_error(self):
        pile = _v3tag(0x04) + bytes([1, 0]) + B.v3_char(_ord("a")) + B.v3_end()
        out = _to_mathml(B.v3_prelude() + pile)
        assert "<merror" in out

    def test_nudged_matrix(self):
        matrix = (
            _v3tag(0x05, 0x08)
            + B.nudge_small()
            + bytes([0, 0, 0, 1, 1])
            + bytes(1)
            + bytes(1)
            + B.v3_simple_char_line(_ord("a"))
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + matrix)
        assert "<mtable>" in out
        assert "merror" not in out

    def test_truncated_matrix_pads_empty_cells(self):
        matrix = (
            _v3tag(0x05)
            + bytes([0, 0, 0, 2, 2])
            + bytes(1)
            + bytes(1)
            + B.v3_simple_char_line(_ord("a"))
            + bytes([0x00, 0x00, 0x00])
            + B.v3_end()
        )
        out = _to_mathml(B.v3_prelude() + matrix)
        root = ET.fromstring(out)
        mtable = _find_first(root, "mtable")
        assert mtable is not None
        rows = list(mtable)
        assert len(rows) == 2
        assert all(len(list(rw)) == 2 for rw in rows)

    def test_non_line_record_in_matrix_cell_is_error(self):
        matrix = (
            _v3tag(0x05)
            + bytes([0, 0, 0, 1, 1])
            + bytes(1)
            + bytes(1)
            + B.v3_char(_ord("a"))
        )
        out = _to_mathml(B.v3_prelude() + matrix)
        assert "<merror" in out


# ---------------------------------------------------------------------------
# Payload-level edges (both dialects)
# ---------------------------------------------------------------------------


class TestPayloadEdges:
    def test_invalid_hex_string_is_error(self):
        out = MtefMathSourceAdapter().to_mathml("zz")
        assert "<merror" in out

    def test_header_only_payload_is_error(self):
        # A bare EQNOLEFILEHDR with nothing after it.
        header = bytes([0x1C, 0x00]) + bytes([0x00, 0x00, 0x02, 0x00]) + bytes(22)
        out = MtefMathSourceAdapter().to_mathml(header)
        assert "<merror" in out

    def test_v5_char_without_mtcode_emits_nothing(self):
        # opts 0x20 = "no MTCode stored"; 0x04 = 8-bit font position. The
        # record carries glyph-position data only, so no atom comes out.
        silent = bytes([0x02, 0x24, 128, 0x33])
        body = B.v5_char(_ord("a")) + silent + B.v5_char(_ord("b"))
        out = _to_mathml(B.v5_prelude() + B.v5_line(body))
        assert _tree_shape(out) == "mi:a,mi:b"

    def test_v5_pile_truncated_at_eof_keeps_rows(self):
        pile = bytes([0x04, 0x00, 1, 0]) + B.v5_line(B.v5_char(_ord("a")))
        out = _to_mathml(B.v5_prelude() + pile)  # no trailing END
        assert "<mtable>" in out
        assert "<mi>a</mi>" in out
        assert "merror" not in out

    def test_v3_pile_truncated_at_eof_keeps_rows(self):
        pile = _v3tag(0x04) + bytes([1, 0]) + B.v3_simple_char_line(_ord("a"))
        out = _to_mathml(B.v3_prelude() + pile)  # no trailing END
        assert "<mtable>" in out
        assert "<mi>a</mi>" in out
        assert "merror" not in out
