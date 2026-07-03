"""Touch-separability geometry — bbox gap + too-close pair detection."""

from __future__ import annotations

from brailix.backend.tactile._separation import bbox_gap, find_too_close


class TestBboxGap:
    def test_overlapping_boxes_zero_gap(self):
        assert bbox_gap((0, 0, 10, 10), (5, 5, 15, 15)) == 0.0

    def test_touching_boxes_zero_gap(self):
        assert bbox_gap((0, 0, 10, 10), (10, 0, 20, 10)) == 0.0

    def test_horizontal_gap(self):
        assert bbox_gap((0, 0, 10, 10), (13, 0, 20, 10)) == 3.0

    def test_diagonal_gap(self):
        # 3 across + 4 down → 5 (a 3-4-5 right triangle).
        assert bbox_gap((0, 0, 10, 10), (13, 14, 20, 20)) == 5.0


class TestFindTooClose:
    BOXES = [
        ("a", (0, 0, 10, 10)),
        ("b", (13, 0, 20, 10)),   # 3 px from a
        ("c", (100, 0, 110, 10)),  # far from both
    ]

    def test_flags_close_pair(self):
        close = find_too_close(self.BOXES, min_gap=5.0, allow_touch=True)
        names = {frozenset((x, y)) for x, y, _g in close}
        assert frozenset(("a", "b")) in names
        assert frozenset(("a", "c")) not in names

    def test_min_gap_boundary_excluded(self):
        # gap exactly == min_gap is not "too close".
        assert find_too_close(self.BOXES, min_gap=3.0, allow_touch=True) == []

    def test_allow_touch_skips_overlap(self):
        boxes = [("a", (0, 0, 10, 10)), ("b", (5, 5, 15, 15))]  # overlapping
        assert find_too_close(boxes, min_gap=5.0, allow_touch=True) == []

    def test_disallow_touch_flags_overlap(self):
        boxes = [("a", (0, 0, 10, 10)), ("b", (5, 5, 15, 15))]  # overlapping
        close = find_too_close(boxes, min_gap=5.0, allow_touch=False)
        assert len(close) == 1

    def test_returns_gap_value(self):
        close = find_too_close(
            [("a", (0, 0, 10, 10)), ("b", (13, 0, 20, 10))],
            min_gap=5.0,
            allow_touch=True,
        )
        assert close == [("a", "b", 3.0)]
