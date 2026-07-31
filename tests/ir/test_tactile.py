"""Tests for :class:`brailix.ir.tactile.TactileRaster`."""

from __future__ import annotations

import math

import pytest

from brailix.ir.tactile import MAX_LEVEL, TactileRaster

_DPI = 100.0
_MM_PER_INCH = 25.4


def _raster(w: int = 4, h: int = 3) -> TactileRaster:
    """A raster whose declared page really is its pixel grid at ``_DPI``.

    The helper used to call 4 × 3 pixels a 10 × 8 mm page — 250 dpi across
    and 9.5 down, declared as 100 — and every test below asserted only that
    the numbers were stored. Physical size is what the encoders read, so a
    fixture is not allowed to contradict itself either.
    """
    return TactileRaster.blank(
        w,
        h,
        dpi=_DPI,
        page_width_mm=max(w, 1) * _MM_PER_INCH / _DPI,
        page_height_mm=max(h, 1) * _MM_PER_INCH / _DPI,
    )


class TestConstruction:
    def test_blank_is_all_flat(self):
        r = _raster()
        assert r.width == 4
        assert r.height == 3
        assert len(r.data) == 12
        assert r.raised_count() == 0

    def test_empty_data_autofills_to_size(self):
        r = TactileRaster(
            width=2,
            height=2,
            dpi=100.0,
            page_width_mm=1.0,
            page_height_mm=1.0,
        )
        assert len(r.data) == 4

    def test_data_length_mismatch_raises(self):
        with pytest.raises(ValueError):
            TactileRaster(
                width=2,
                height=2,
                dpi=100.0,
                page_width_mm=1.0,
                page_height_mm=1.0,
                data=bytearray(3),
            )

    def test_negative_dimensions_raise(self):
        with pytest.raises(ValueError):
            TactileRaster(
                width=-1,
                height=2,
                dpi=100.0,
                page_width_mm=1.0,
                page_height_mm=1.0,
            )

    def test_zero_dimensions_construct_but_are_not_renderable(self):
        # 0 is a valid IR value (a blank grid the max(1, round(...)) callers
        # rely on), so construction succeeds; but require_renderable() rejects
        # it, since no image format can encode a zero-area raster.
        for w, h in [(0, 0), (0, 5), (5, 0)]:
            r = TactileRaster.blank(
                w, h, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
            )
            with pytest.raises(ValueError):
                r.require_renderable()

    def test_positive_raster_is_renderable(self):
        assert _raster().require_renderable() is None
        assert TactileRaster.blank(
            1, 1, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
        ).require_renderable() is None

    def test_carries_physical_metadata(self):
        r = _raster()
        assert r.dpi == 100.0
        assert r.page_width_mm == pytest.approx(4 * 25.4 / 100.0)
        assert r.page_height_mm == pytest.approx(3 * 25.4 / 100.0)
        assert r.bit_depth == 8


class TestPhysicalFieldsAreChecked:
    """Every field an encoder reads without a second look is checked here.

    Each of these used to construct fine and fail somewhere else entirely: a
    NaN dpi inside ``round()`` in the BMP header, an infinite page size as the
    literal text ``inf`` in a PDF ``MediaBox`` that no reader opens, a
    read-only ``data`` on the first :meth:`set_raise`. A raster is handed
    across a boundary, so the caller who built it is not the one who sees the
    failure.
    """

    @staticmethod
    def _build(**overrides) -> TactileRaster:
        kwargs = dict(
            width=2, height=2, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
        )
        kwargs.update(overrides)
        return TactileRaster(**kwargs)

    @pytest.mark.parametrize("field", ["dpi", "page_width_mm", "page_height_mm"])
    @pytest.mark.parametrize(
        "value", [float("nan"), float("inf"), float("-inf"), 0.0, -1.0]
    )
    def test_non_positive_or_non_finite_is_rejected(self, field, value):
        with pytest.raises(ValueError, match=field):
            self._build(**{field: value})

    @pytest.mark.parametrize("field", ["dpi", "page_width_mm", "page_height_mm"])
    def test_a_bool_is_not_a_measurement(self, field):
        # bool is an int subclass, so True would quietly mean "1 dpi".
        with pytest.raises(ValueError, match=field):
            self._build(**{field: True})

    @pytest.mark.parametrize("field", ["dpi", "page_width_mm", "page_height_mm"])
    def test_a_numeric_string_is_converted_and_stored(self, field):
        # It converts, so refusing it would be pedantic; keeping it as a str
        # would not — the field is declared float and the next arithmetic on
        # it would raise TypeError far from here.
        r = self._build(**{field: "50"})
        assert getattr(r, field) == 50.0
        assert isinstance(getattr(r, field), float)

    @pytest.mark.parametrize("field", ["dpi", "page_width_mm", "page_height_mm"])
    def test_a_non_number_is_rejected(self, field):
        with pytest.raises(ValueError, match=field):
            self._build(**{field: "wide"})

    @pytest.mark.parametrize("field", ["width", "height"])
    @pytest.mark.parametrize("value", [True, False, 1.5, 2.0, "4", None])
    def test_dimensions_reject_bool_float_and_string(self, field, value):
        # The pixel pair is a count of array elements, so — unlike the
        # millimetres, where anything that converts to a finite positive float
        # is a legitimate spelling — only an int is a value at all. The old
        # ``< 0`` test let ``True`` through as a one-pixel axis, blew up on
        # 1.5 two lines later inside ``bytearray(width * height)``, and on
        # "4" in the comparison itself with a TypeError naming neither the
        # field nor the raster.
        with pytest.raises(ValueError, match=field):
            self._build(**{field: value})

    @pytest.mark.parametrize("field", ["width", "height"])
    def test_dimensions_reject_negative_with_a_named_message(self, field):
        with pytest.raises(ValueError, match=field):
            self._build(**{field: -1})

    @pytest.mark.parametrize("bit_depth", [0, 4, 16, -1, "8"])
    def test_unsupported_bit_depth_is_rejected(self, bit_depth):
        with pytest.raises(ValueError, match="bit_depth"):
            self._build(bit_depth=bit_depth)

    @pytest.mark.parametrize("bit_depth", [True, False])
    def test_a_bool_is_not_a_bit_depth(self, bit_depth):
        # ``True == 1``, so the membership test against {1, 8} accepted it as
        # the 1-bit depth and stored a *bool* in a field the encoders read back
        # as an int — the same trap the pixel pair and the measurements above
        # each spell out. Only half the type slipped through (``False`` equals
        # neither depth, so it already raised), which is why the pair is
        # parametrized rather than asserted once.
        with pytest.raises(ValueError, match="bit_depth"):
            self._build(bit_depth=bit_depth)

    @pytest.mark.parametrize("bit_depth", [[1], {8: "yes"}, {1, 8}])
    def test_an_unhashable_bit_depth_is_still_a_named_value_error(self, bit_depth):
        # Membership on a frozenset hashes its operand, so the check meant to
        # produce this ValueError raised ``TypeError: unhashable type`` before
        # reaching it — an error naming neither the field nor the raster.
        with pytest.raises(ValueError, match="bit_depth"):
            self._build(bit_depth=bit_depth)

    @pytest.mark.parametrize("bit_depth", [1, 8])
    def test_supported_bit_depths_are_accepted(self, bit_depth):
        assert self._build(bit_depth=bit_depth).bit_depth == bit_depth

    def test_readonly_data_becomes_a_writable_copy(self):
        source = b"\x00\x00\x00\x00"
        r = self._build(data=source)
        assert isinstance(r.data, bytearray)
        r.set_raise(0, 0, 255)  # used to raise: bytes are not assignable
        assert r.get(0, 0) == 255
        assert source == b"\x00\x00\x00\x00"  # the caller's bytes untouched

    def test_a_bytearray_is_adopted_not_copied(self):
        # The backend paints into the grid it passed in; copying every page
        # would be a silent waste on the hot path.
        source = bytearray(4)
        r = self._build(data=source)
        assert r.data is source

    def test_zero_size_still_constructs(self):
        # The physical checks must not turn a legal empty grid into an error;
        # require_renderable() is what rejects it, at encode time.
        r = self._build(width=0, height=0)
        assert r.data == bytearray()
        assert math.isfinite(r.dpi)


class TestBlankAndDirectConstructionAgree:
    """One type, one construction contract — whichever way a raster is built.

    :meth:`TactileRaster.blank` used to allocate ``bytearray(width * height)``
    itself and pass it in, which put the allocator *ahead of* the field checks
    in ``__post_init__``. So the illegal ``width`` that
    ``TactileRaster(width=...)`` refuses with a ``ValueError`` naming the field
    came back from the factory as whatever ``bytearray`` happened to raise —
    ``TypeError: cannot convert 'float' object to bytearray`` for ``1.5``,
    ``TypeError: string argument without an encoding`` for ``"4"``, a bare
    ``negative count`` (no field, no raster) for ``-1``. A caller cannot catch
    or display a field-level diagnostic that only one of the two paths raises,
    so both are asserted together here rather than in either path's own class.
    """

    @staticmethod
    def _kwargs(**overrides) -> dict:
        kwargs = dict(
            width=2, height=2, dpi=100.0, page_width_mm=1.0, page_height_mm=1.0
        )
        kwargs.update(overrides)
        return kwargs

    @pytest.mark.parametrize(
        "field,value",
        [
            (field, value)
            for field in ("width", "height")
            for value in (1.5, "4", -1, True, None)
        ]
        + [
            ("dpi", 0.0),
            ("dpi", float("nan")),
            ("page_width_mm", -1.0),
            ("page_height_mm", "wide"),
            ("bit_depth", 4),
            ("bit_depth", True),
        ],
    )
    def test_both_paths_reject_it_and_name_the_field(self, field, value):
        with pytest.raises(ValueError, match=field):
            TactileRaster(**self._kwargs(**{field: value}))
        with pytest.raises(ValueError, match=field):
            TactileRaster.blank(**self._kwargs(**{field: value}))

    @pytest.mark.parametrize("size", [(0, 0), (2, 3), (5, 1)])
    def test_both_paths_build_the_same_flat_grid(self, size):
        w, h = size
        kwargs = self._kwargs(width=w, height=h)
        assert TactileRaster.blank(**kwargs) == TactileRaster(**kwargs)
        assert len(TactileRaster.blank(**kwargs).data) == w * h


class TestPixelAccess:
    def test_set_and_get(self):
        r = _raster()
        r.set_raise(1, 2, 200)
        assert r.get(1, 2) == 200
        assert r.get(0, 0) == 0

    def test_set_raise_takes_maximum(self):
        r = _raster()
        r.set_raise(1, 1, 200)
        r.set_raise(1, 1, 50)  # lower value must not overwrite
        assert r.get(1, 1) == 200
        r.set_raise(1, 1, 255)
        assert r.get(1, 1) == 255

    def test_level_is_clamped(self):
        r = _raster()
        r.set_raise(0, 0, 9999)
        assert r.get(0, 0) == MAX_LEVEL
        r2 = _raster()
        r2.set_raise(0, 0, -5)
        assert r2.get(0, 0) == 0

    def test_out_of_bounds_write_is_ignored(self):
        r = _raster()
        r.set_raise(99, 99, 255)
        r.set_raise(-1, 0, 255)
        assert r.raised_count() == 0

    def test_out_of_bounds_read_returns_zero(self):
        r = _raster()
        assert r.get(99, 99) == 0
        assert r.get(-1, -1) == 0

    def test_in_bounds(self):
        r = _raster()
        assert r.in_bounds(0, 0)
        assert r.in_bounds(3, 2)
        assert not r.in_bounds(4, 2)
        assert not r.in_bounds(0, 3)

    def test_raised_count_threshold(self):
        r = _raster()
        r.set_raise(0, 0, 10)
        r.set_raise(1, 0, 200)
        assert r.raised_count() == 2
        assert r.raised_count(threshold=100) == 1
