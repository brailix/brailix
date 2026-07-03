"""Tests for the affine transform parser/composer (SVG ``transform``)."""

from __future__ import annotations

from brailix.backend.tactile._transform import IDENTITY, Affine, parse_transform


def _close(p: tuple[float, float], q: tuple[float, float], tol: float = 1e-6) -> bool:
    return abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol


def test_identity_applies_unchanged():
    assert _close(IDENTITY.apply(3, 4), (3, 4))


def test_parse_empty_is_identity():
    assert parse_transform(None) == IDENTITY
    assert parse_transform("") == IDENTITY


def test_translate():
    assert _close(parse_transform("translate(10, 20)").apply(1, 2), (11, 22))


def test_translate_single_arg_defaults_ty_zero():
    assert _close(parse_transform("translate(5)").apply(1, 2), (6, 2))


def test_scale_uniform_and_nonuniform():
    assert _close(parse_transform("scale(2)").apply(3, 4), (6, 8))
    assert _close(parse_transform("scale(2,3)").apply(3, 4), (6, 12))


def test_rotate_90_maps_x_axis_to_y_axis():
    # y-down SVG: rotate(90) sends (1,0) to (0,1).
    assert _close(parse_transform("rotate(90)").apply(1, 0), (0, 1))


def test_rotate_about_point_keeps_center_fixed():
    m = parse_transform("rotate(90, 1, 1)")
    assert _close(m.apply(1, 1), (1, 1))
    assert _close(m.apply(2, 1), (1, 2))


def test_skewX():
    assert _close(parse_transform("skewX(45)").apply(0, 1), (1, 1))


def test_skewY():
    assert _close(parse_transform("skewY(45)").apply(1, 0), (1, 1))


def test_matrix():
    assert _close(parse_transform("matrix(1 0 0 1 5 6)").apply(2, 3), (7, 9))


def test_composition_applies_right_to_left():
    m = parse_transform("translate(10, 0) scale(2)")
    assert _close(m.apply(1, 0), (12, 0))


def test_composition_order_matters():
    a = parse_transform("translate(10,0) scale(2)")
    b = parse_transform("scale(2) translate(10,0)")
    assert _close(a.apply(1, 0), (12, 0))
    assert _close(b.apply(1, 0), (22, 0))


def test_then_matches_nested_application():
    t = Affine(1, 0, 0, 1, 10, 0)
    s = Affine(2, 0, 0, 2, 0, 0)
    assert _close(t.then(s).apply(1, 0), t.apply(*s.apply(1, 0)))


def test_scale_factor():
    assert abs(parse_transform("scale(2)").scale_factor() - 2.0) < 1e-9
    assert abs(parse_transform("scale(2,8)").scale_factor() - 4.0) < 1e-9
    assert abs(parse_transform("rotate(37)").scale_factor() - 1.0) < 1e-9


def test_unknown_function_skipped():
    assert _close(parse_transform("foo(1,2) translate(3,4)").apply(0, 0), (3, 4))


def test_empty_args_soft_fail():
    assert parse_transform("translate()").apply(2, 2) == (2, 2)


def test_inverse_round_trips_a_point():
    m = parse_transform("translate(10, 20) rotate(30) scale(2, 3)")
    inv = m.inverse()
    assert inv is not None
    assert _close(inv.apply(*m.apply(7, 5)), (7, 5))


def test_inverse_of_translate_is_negative_translate():
    inv = Affine(1, 0, 0, 1, 10, 20).inverse()
    assert inv is not None
    assert _close(inv.apply(10, 20), (0, 0))


def test_inverse_of_identity_is_identity():
    inv = IDENTITY.inverse()
    assert inv is not None
    assert _close(inv.apply(3, 4), (3, 4))


def test_inverse_singular_returns_none():
    # scale(0) collapses to zero area — no inverse.
    assert parse_transform("scale(0)").inverse() is None
    assert Affine(1, 1, 1, 1, 0, 0).inverse() is None  # rows linearly dependent
