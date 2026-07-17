"""Concrete execution of the CrossHair contract corpus.

The ``check_*`` functions in ``contracts.py`` are written for symbolic
checking, but a contract nobody executes rots: an API rename or a wrong
assertion would sit unnoticed until someone happens to run the solver.
So every contract is also driven here with Hypothesis-generated concrete
inputs on every ordinary pytest run — cheap fuzzing that doubles as a
liveness guard. The closing test fails loudly when a new contract is
added without a smoke wrapper.
"""

from __future__ import annotations

import pytest

pytest.importorskip("hypothesis")

from hypothesis import example, given, settings
from hypothesis import strategies as st

from tests.crosshair import contracts

_ints = st.integers(-30, 30)
_small_nonneg = st.integers(0, 30)
_dots = st.integers(-2, 10)
_syllables = st.text(
    alphabet=st.sampled_from(list("abcdefghijklmnopqrstuvwxyzZHV 与ü'12345Ⅴ")),
    max_size=8,
)


@settings(max_examples=60)
@given(start=_ints, end=_ints)
def test_span_construction_validity(start: int, end: int) -> None:
    contracts.check_span_construction_validity(start, end)


@settings(max_examples=60)
@given(a_start=_small_nonneg, a_len=_small_nonneg, b_start=_small_nonneg, b_len=_small_nonneg)
def test_span_merge_is_bounding_box(a_start: int, a_len: int, b_start: int, b_len: int) -> None:
    contracts.check_span_merge_is_bounding_box(a_start, a_len, b_start, b_len)


@settings(max_examples=60)
@given(start=_small_nonneg, length=_small_nonneg, offset=_ints)
def test_span_shift_is_exact_translation(start: int, length: int, offset: int) -> None:
    contracts.check_span_shift_is_exact_translation(start, length, offset)


@settings(max_examples=60)
@given(a_start=_small_nonneg, a_len=_small_nonneg, b_start=_small_nonneg, b_len=_small_nonneg)
def test_span_relations_match_interval_model(
    a_start: int, a_len: int, b_start: int, b_len: int
) -> None:
    contracts.check_span_relations_match_interval_model(a_start, a_len, b_start, b_len)


@settings(max_examples=60)
@given(d1=_dots, d2=_dots)
def test_cell_dot_pair_is_canonical_and_order_free(d1: int, d2: int) -> None:
    contracts.check_cell_dot_pair_is_canonical_and_order_free(d1, d2)


@settings(max_examples=60)
@given(d1=_dots, d2=_dots, d3=_dots)
def test_cell_dot_triple_is_canonical_and_order_free(d1: int, d2: int, d3: int) -> None:
    contracts.check_cell_dot_triple_is_canonical_and_order_free(d1, d2, d3)


@settings(max_examples=60)
@given(
    pn=st.text(alphabet=st.sampled_from(list("#abij")), min_size=1, max_size=4),
    width=st.integers(0, 40),
    align_right=st.booleans(),
)
# pad == 0 and pad == 1 exactly — the fence between "overflow with the
# bare number" and "pad to the right edge"; a derandomized sweep missed
# them (mutation testing caught a surviving boundary tweak there).
@example(pn="#a", width=2, align_right=True)
@example(pn="#a", width=3, align_right=True)
@example(pn="#a", width=3, align_right=False)
def test_page_number_line_geometry(pn: str, width: int, align_right: bool) -> None:
    contracts.check_page_number_line_geometry(pn, width, align_right)


@settings(max_examples=80)
@given(syllable=_syllables)
def test_parse_pinyin_totality(syllable: str) -> None:
    contracts.check_parse_pinyin_totality(syllable)


@settings(max_examples=80)
@given(syllable=_syllables)
def test_normalize_syllable_spelling_idempotent(syllable: str) -> None:
    contracts.check_normalize_syllable_spelling_idempotent(syllable)


@settings(max_examples=80)
@given(mask=st.integers(0, 255))
def test_unicode_braille_codec_bijection(mask: int) -> None:
    contracts.check_unicode_braille_codec_bijection(mask)


@settings(max_examples=80)
@given(
    cp=st.one_of(
        st.integers(0, 0x3000),
        # The block itself and its one-off edges, explicitly: a
        # derandomized sweep of 0..0x3000 can miss the 256-code-point
        # block entirely (mutation testing proved it — a broken
        # ``cp - BRAILLE_BASE`` survived).
        st.integers(0x2800, 0x28FF),
        st.sampled_from([0x27FF, 0x2800, 0x28FF, 0x2900]),
    )
)
def test_char_to_dots_accepts_exactly_the_braille_block(cp: int) -> None:
    contracts.check_char_to_dots_accepts_exactly_the_braille_block(cp)


@settings(max_examples=64)
@given(mask=st.integers(0, 63))
def test_brf_six_dot_codec_round_trips(mask: int) -> None:
    contracts.check_brf_six_dot_codec_round_trips(mask)


@settings(max_examples=80)
@given(
    source=st.text(alphabet=st.sampled_from(list("latexomq3_")), min_size=1, max_size=8),
    payload=st.text(
        alphabet=st.sampled_from(list("x+2$ 我\t\n(")), max_size=8
    ),
)
def test_inline_math_island_codec(source: str, payload: str) -> None:
    contracts.check_inline_math_island_codec(source, payload)


# Code-point strategy that guarantees the interesting classes are visited
# alongside the broad sweep: full-width ASCII variants AND their range
# boundaries, the ideographic space, the invisible set, real Sm math
# symbols, and the braille block's edges (inside and one-off each end).
# Explicit sampling matters: under CI the profile is derandomized, and a
# fixed corpus drawn from 0..0xFFFF alone can miss a 256-code-point block
# entirely — mutation testing caught exactly that blind spot.
_codepoints = st.one_of(
    st.integers(0, 0xFFFF),
    st.integers(0xFF01, 0xFF5E),
    st.sampled_from(sorted(
        {0x3000, 0x2208, 0x2264, 0x00B7, 0x00B0,
         0xFF00, 0xFF01, 0xFF5E, 0xFF5F,
         0x27FF, 0x2800, 0x2801, 0x2836, 0x28FF, 0x2900}.union(
            {0x200B, 0x200C, 0x200D, 0x2060, 0x00AD, 0xFEFF}
        )
    )),
)


@settings(max_examples=100)
@given(cp=_codepoints)
def test_fold_fullwidth_mapping(cp: int) -> None:
    contracts.check_fold_fullwidth_mapping(cp)


@settings(max_examples=100)
@given(cp=_codepoints)
def test_nonstandard_hint_consistency(cp: int) -> None:
    contracts.check_nonstandard_hint_consistency(cp)


@settings(max_examples=100)
@given(cp=_codepoints)
def test_is_math_symbol_is_exactly_category_sm(cp: int) -> None:
    contracts.check_is_math_symbol_is_exactly_category_sm(cp)


def test_every_contract_opens_with_a_precondition_assert() -> None:
    # CrossHair's ``asserts`` analysis kind DISCOVERS contract functions by
    # their shape: the first statement after the docstring must be an
    # ``assert``, or the whole function is silently skipped — never
    # analyzed, no warning. That failure mode is invisible at runtime
    # (concrete smoke still passes), so it is pinned statically here.
    import ast
    import inspect

    module_tree = ast.parse(inspect.getsource(contracts))
    for node in module_tree.body:
        if not isinstance(node, ast.FunctionDef) or not node.name.startswith("check_"):
            continue
        body = node.body
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            body = body[1:]  # skip the docstring
        assert body and isinstance(body[0], ast.Assert), (
            f"{node.name} must open with a precondition assert, or CrossHair "
            f"will silently skip it (asserts-mode discovery rule)"
        )


def test_every_contract_has_a_smoke_wrapper() -> None:
    # Rot guard: a contract added to contracts.py without a concrete smoke
    # wrapper here would only ever run under the (optional) solver.
    meta = {
        "test_every_contract_has_a_smoke_wrapper",
        "test_every_contract_opens_with_a_precondition_assert",
    }
    smoked = {
        name[len("test_") :]
        for name in globals()
        if name.startswith("test_") and name not in meta
    }
    declared = {
        name[len("check_") :]
        for name in vars(contracts)
        if name.startswith("check_")
    }
    assert declared == smoked
