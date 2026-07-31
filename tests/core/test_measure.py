"""The shared positive-finite measurement check, and that both layers use it.

:func:`brailix.core.measure.as_positive_finite` exists because two layers ask
the same question of the same numbers: the tactile raster (built in code,
:mod:`brailix.ir.tactile`) and the tactile device profile (read out of JSON,
:mod:`brailix.backend.tactile.profile`). They may not import each other, so
before this they each carried a copy of the five steps.

Two things are pinned here, and the second is the point. The first class checks
the function's own contract. The second checks that the two callers really route
through it and stay *semantically* identical — same verdict on ``bool``, ``NaN``,
``Infinity``, zero, a negative, and a numeric string — while keeping the
different error type each one owes its own caller. A shared helper that one side
stopped calling would leave every test in both suites green, and the drift would
only show up as two layers disagreeing about whether ``"100"`` is a dpi.
"""

from __future__ import annotations

import math

import pytest

from brailix.backend.tactile.profile import TactileProfile
from brailix.core.errors import ConfigurationError
from brailix.core.measure import as_positive_finite
from brailix.ir.tactile import TactileRaster


class TestAsPositiveFinite:
    @pytest.mark.parametrize("value", [1, 2.5, "100", " 7 "])
    def test_a_measurement_converts_and_is_returned_as_float(self, value):
        result = as_positive_finite(value, "dpi")
        assert isinstance(result, float)
        assert result == float(value)

    @pytest.mark.parametrize(
        "value", [0, 0.0, -1, -0.5, float("nan"), float("inf"), float("-inf")]
    )
    def test_non_positive_and_non_finite_are_refused(self, value):
        with pytest.raises(ValueError, match="dpi"):
            as_positive_finite(value, "dpi")

    @pytest.mark.parametrize("value", [True, False])
    def test_a_bool_is_not_a_measurement(self, value):
        # ``bool`` is an ``int`` subclass, so ``True`` would convert to 1.0 and
        # pass every check below it — a 1-dpi page nobody asked for.
        with pytest.raises(ValueError, match="dpi"):
            as_positive_finite(value, "dpi")

    @pytest.mark.parametrize("value", [None, "wide", object(), [1], {}])
    def test_a_non_number_is_refused(self, value):
        with pytest.raises(ValueError, match="dpi"):
            as_positive_finite(value, "dpi")

    def test_the_caller_chooses_the_exception(self):
        with pytest.raises(ConfigurationError, match="profile field 'dpi'"):
            as_positive_finite(
                -1, "profile field 'dpi'", error=ConfigurationError
            )

    def test_the_message_opens_with_what_was_measured(self):
        with pytest.raises(ValueError) as excinfo:
            as_positive_finite(float("nan"), "page_width_mm")
        assert str(excinfo.value).startswith("page_width_mm ")


class TestBothLayersAgree:
    """The same value, offered to a raster and to a profile, gets the same
    verdict — and each layer's own error type."""

    @staticmethod
    def _raster(value):
        return TactileRaster(
            width=2, height=2, dpi=value, page_width_mm=1.0, page_height_mm=1.0
        )

    @staticmethod
    def _profile(value):
        return TactileProfile(
            name="t",
            dpi=value,
            page_width_mm=100.0,
            page_height_mm=100.0,
            min_line_width_mm=1.0,
            min_feature_spacing_mm=1.0,
            braille_dot_radius_mm=0.75,
            braille_dot_spacing_mm=2.5,
            braille_cell_spacing_mm=6.0,
        )

    @pytest.mark.parametrize(
        "value",
        [
            True,
            False,
            float("nan"),
            float("inf"),
            float("-inf"),
            0,
            -1.0,
            "wide",
            None,
        ],
    )
    def test_both_refuse_it(self, value):
        with pytest.raises(ValueError):
            self._raster(value)
        # ConfigurationError subclasses ValueError, so the assertion above
        # would pass here too; naming it is the point — a profile's bad value
        # is a configuration fault, and a front-end catches it as one.
        with pytest.raises(ConfigurationError):
            self._profile(value)

    @pytest.mark.parametrize("value", ["100", 100, 100.0])
    def test_both_accept_it_and_store_a_float(self, value):
        assert self._raster(value).dpi == 100.0
        assert isinstance(self._raster(value).dpi, float)
        assert self._profile(value).dpi == 100.0
        assert isinstance(self._profile(value).dpi, float)

    def test_a_raster_measurement_is_not_a_configuration_error(self):
        """The diagnosis is what deliberately did *not* move into core: a
        raster is built in code, so its bad value is the caller's bug."""
        with pytest.raises(ValueError) as excinfo:
            self._raster(math.nan)
        assert not isinstance(excinfo.value, ConfigurationError)
