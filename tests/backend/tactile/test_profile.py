"""Tests for the tactile rendering profile loader."""

from __future__ import annotations

import json

import pytest

import brailix.backend.tactile.profile as profile_mod
from brailix.backend.tactile.profile import (
    TactileProfile,
    list_tactile_profiles,
    load_tactile_profile,
)
from brailix.core.errors import ConfigurationError


class TestBuiltinProfile:
    def test_generic_is_listed(self):
        assert "generic" in list_tactile_profiles()

    def test_letter_is_listed(self):
        assert "letter" in list_tactile_profiles()

    def test_load_letter(self):
        prof = load_tactile_profile("letter")
        assert prof.name == "letter"
        assert prof.page_width_mm == 215.9
        assert prof.page_height_mm == 279.4

    def test_load_generic(self):
        prof = load_tactile_profile("generic")
        assert isinstance(prof, TactileProfile)
        assert prof.name == "generic"
        assert prof.dpi == 100.0
        assert prof.page_width_mm == 210.0
        assert prof.page_height_mm == 297.0
        assert prof.min_line_width_mm > 0
        assert prof.min_feature_spacing_mm > 0
        assert prof.braille_dot_radius_mm == 0.75
        assert prof.braille_dot_spacing_mm == 2.5
        assert prof.braille_cell_spacing_mm == 6.0

    def test_default_is_generic(self):
        assert load_tactile_profile().name == "generic"

    def test_missing_profile_raises(self):
        with pytest.raises(ConfigurationError):
            load_tactile_profile("does-not-exist")


class TestProfileValidation:
    def _write(self, tmp_path, monkeypatch, payload: dict) -> None:
        d = tmp_path / "tactile"
        d.mkdir()
        (d / "custom.json").write_text(json.dumps(payload), encoding="utf-8")
        monkeypatch.setattr(profile_mod, "_TACTILE_DIR", d)

    def test_valid_custom_profile(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "name": "custom",
                "dpi": 200,
                "page_width_mm": 100,
                "page_height_mm": 100,
                "min_line_width_mm": 0.6,
                "min_feature_spacing_mm": 3.0,
            },
        )
        prof = load_tactile_profile("custom")
        assert prof.dpi == 200.0
        assert prof.min_feature_spacing_mm == 3.0
        assert list_tactile_profiles() == ["custom"]

    def test_spacing_defaults_to_line_width(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "dpi": 100,
                "page_width_mm": 100,
                "page_height_mm": 100,
                "min_line_width_mm": 0.7,
            },
        )
        prof = load_tactile_profile("custom")
        assert prof.min_feature_spacing_mm == 0.7
        # Braille metrics also fall back to standard defaults when omitted.
        assert prof.braille_dot_radius_mm == 0.75
        assert prof.braille_cell_spacing_mm == 6.0

    def test_non_positive_dpi_raises(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "dpi": 0,
                "page_width_mm": 100,
                "page_height_mm": 100,
                "min_line_width_mm": 0.5,
            },
        )
        with pytest.raises(ConfigurationError):
            load_tactile_profile("custom")

    def test_non_numeric_field_raises(self, tmp_path, monkeypatch):
        self._write(
            tmp_path,
            monkeypatch,
            {
                "dpi": "fast",
                "page_width_mm": 100,
                "page_height_mm": 100,
                "min_line_width_mm": 0.5,
            },
        )
        with pytest.raises(ConfigurationError):
            load_tactile_profile("custom")

    @pytest.mark.parametrize("literal", ["NaN", "Infinity", "-Infinity"])
    def test_non_finite_dpi_raises(self, tmp_path, monkeypatch, literal: str):
        """``NaN`` and ``Infinity`` are JSON literals Python's decoder accepts,
        and both walk past a bare ``<= 0`` test — every comparison with ``NaN``
        is false, and infinity really is greater than zero. They then fail far
        downstream (``round(nan)`` → ValueError, ``int(inf)`` → OverflowError)
        instead of at the load this loader promises to fail at."""
        d = tmp_path / "tactile"
        d.mkdir()
        (d / "custom.json").write_text(
            '{"dpi": ' + literal + ', "page_width_mm": 100, '
            '"page_height_mm": 100, "min_line_width_mm": 0.5}',
            encoding="utf-8",
        )
        monkeypatch.setattr(profile_mod, "_TACTILE_DIR", d)
        with pytest.raises(ConfigurationError):
            load_tactile_profile("custom")

    @pytest.mark.parametrize(
        "field", ["page_width_mm", "min_line_width_mm", "braille_dot_radius_mm"]
    )
    def test_every_millimetre_field_shares_the_constraint(
        self, tmp_path, monkeypatch, field: str
    ):
        """Not just DPI: one loose field is one raster geometry computed from
        a non-number."""
        d = tmp_path / "tactile"
        d.mkdir()
        payload = {
            "dpi": 100,
            "page_width_mm": 100,
            "page_height_mm": 100,
            "min_line_width_mm": 0.5,
        }
        body = ", ".join(f'"{k}": {v}' for k, v in payload.items() if k != field)
        (d / "custom.json").write_text(
            "{" + body + f', "{field}": NaN' + "}", encoding="utf-8"
        )
        monkeypatch.setattr(profile_mod, "_TACTILE_DIR", d)
        with pytest.raises(ConfigurationError):
            load_tactile_profile("custom")

    def test_boolean_field_raises(self, tmp_path, monkeypatch):
        """``bool`` is an ``int`` subclass, so ``"dpi": true`` would otherwise
        load as a 1-DPI profile."""
        self._write(
            tmp_path,
            monkeypatch,
            {
                "dpi": True,
                "page_width_mm": 100,
                "page_height_mm": 100,
                "min_line_width_mm": 0.5,
            },
        )
        with pytest.raises(ConfigurationError):
            load_tactile_profile("custom")

    def test_invalid_json_raises(self, tmp_path, monkeypatch):
        d = tmp_path / "tactile"
        d.mkdir()
        (d / "broken.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(profile_mod, "_TACTILE_DIR", d)
        with pytest.raises(ConfigurationError):
            load_tactile_profile("broken")


class TestTheConstraintBelongsToTheType:
    """A profile built in code gets the same check the JSON path does.

    ``translate_graphic`` accepts an already-loaded profile, and the editor's
    settings pane builds one, so the loader is not the only door into the mm →
    px transform. The invariant lives on the dataclass; the loader only adds
    the filename to the message.
    """

    _VALID = dict(
        name="t",
        dpi=100.0,
        page_width_mm=210.0,
        page_height_mm=297.0,
        min_line_width_mm=0.5,
        min_feature_spacing_mm=2.5,
        braille_dot_radius_mm=0.75,
        braille_dot_spacing_mm=2.5,
        braille_cell_spacing_mm=6.0,
    )

    def test_a_valid_profile_still_constructs(self):
        assert TactileProfile(**self._VALID).dpi == 100.0

    @pytest.mark.parametrize(
        "bad", [float("nan"), float("inf"), float("-inf"), 0, -1.0, True]
    )
    def test_a_bad_metric_is_refused_at_construction(self, bad: object):
        with pytest.raises(ConfigurationError):
            TactileProfile(**{**self._VALID, "dpi": bad})

    def test_the_defaulted_field_is_checked_too(self):
        with pytest.raises(ConfigurationError):
            TactileProfile(**{**self._VALID, "braille_line_spacing_mm": 0.0})
