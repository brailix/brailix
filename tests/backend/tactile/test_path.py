"""Tests for SVG ``<path>`` data parsing + flattening."""

from __future__ import annotations

import math

from brailix.backend.tactile._path import parse_path_data as P


def _close(p: tuple[float, float], q: tuple[float, float], tol: float = 1e-6) -> bool:
    return abs(p[0] - q[0]) < tol and abs(p[1] - q[1]) < tol


def test_empty_returns_no_subpaths():
    assert P("") == []


def test_moveto_lineto():
    sp = P("M0,0 L10,0 L10,10")
    assert len(sp) == 1
    assert sp[0].points == [(0, 0), (10, 0), (10, 10)]
    assert sp[0].closed is False


def test_close_sets_flag_without_duplicating_start():
    sp = P("M0,0 L10,0 L10,10 Z")
    assert sp[0].closed is True
    assert sp[0].points == [(0, 0), (10, 0), (10, 10)]


def test_relative_commands():
    sp = P("M0,0 l5,0 l0,5")
    assert sp[0].points == [(0, 0), (5, 0), (5, 5)]


def test_implicit_lineto_after_moveto():
    sp = P("M0,0 10,0 10,10")
    assert sp[0].points == [(0, 0), (10, 0), (10, 10)]


def test_horizontal_vertical_abs_and_rel():
    sp = P("M0,0 H10 V10 h-5 v-5")
    assert sp[0].points == [(0, 0), (10, 0), (10, 10), (5, 10), (5, 5)]


def test_multiple_subpaths():
    sp = P("M0,0 L5,0 M20,20 L25,20")
    assert len(sp) == 2
    assert sp[0].points == [(0, 0), (5, 0)]
    assert sp[1].points == [(20, 20), (25, 20)]


def test_cubic_flattened_with_correct_endpoints():
    sp = P("M0,0 C0,10 10,10 10,0")
    pts = sp[0].points
    assert pts[0] == (0, 0)
    assert _close(pts[-1], (10, 0))
    assert len(pts) > 4  # subdivided into multiple segments


def test_smooth_cubic_reflection_reaches_endpoint():
    sp = P("M0,0 C0,5 5,5 5,0 S10,-5 10,0")
    assert _close(sp[0].points[-1], (10, 0))


def test_quadratic_flattened():
    sp = P("M0,0 Q5,10 10,0")
    pts = sp[0].points
    assert pts[0] == (0, 0)
    assert _close(pts[-1], (10, 0))
    assert max(p[1] for p in pts) > 0  # bows upward toward the control point


def test_flatten_density_tracks_device_scale():
    # The subdivision density follows the device-pixel span, not the author's
    # coordinate magnitude: a larger scale subdivides more finely.
    d = "M0 2 C 0 0 4 0 4 2"
    small = P(d, scale=1.0)[0].points
    big = P(d, scale=50.0)[0].points
    assert len(big) > len(small)


def test_equivalent_physical_curves_get_equal_density():
    # Same physical curve written in a small vs large coordinate system, each
    # with the matching user→device scale, gets the same number of points.
    a = P("M0 2 C 0 0 4 0 4 2", scale=98.5)[0].points
    b = P("M0 50 C 0 0 100 0 100 50", scale=3.94)[0].points
    assert len(a) == len(b)


def test_default_scale_preserves_user_space_behavior():
    # No scale → points stay user-space, endpoints exact, still subdivided —
    # locks the pre-existing density contract.
    sp = P("M0,0 C0,10 10,10 10,0")
    assert sp[0].points[0] == (0, 0)
    assert _close(sp[0].points[-1], (10, 0))
    assert len(sp[0].points) > 4


def test_arc_points_lie_on_circle():
    # radius-5 arc from (0,0) to (10,0): center (5,0), every point ~5 away.
    pts = P("M0,0 A5,5 0 0 1 10,0")[0].points
    assert _close(pts[-1], (10, 0))
    for px, py in pts:
        assert abs(math.hypot(px - 5.0, py - 0.0) - 5.0) < 0.5


def test_arc_sweep_flag_flips_side():
    up = P("M0,0 A5,5 0 0 1 10,0")[0].points
    down = P("M0,0 A5,5 0 0 0 10,0")[0].points
    assert (max(p[1] for p in up) > 0) != (max(p[1] for p in down) > 0)


def test_arc_zero_radius_degrades_to_line():
    sp = P("M0,0 A0,0 0 0 1 10,0")
    assert _close(sp[0].points[-1], (10, 0))


def test_truncated_command_soft_fails():
    sp = P("M0,0 L10,0 L10")  # final L is missing its y coordinate
    assert sp[0].points == [(0, 0), (10, 0)]
