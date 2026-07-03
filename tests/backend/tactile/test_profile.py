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

    def test_invalid_json_raises(self, tmp_path, monkeypatch):
        d = tmp_path / "tactile"
        d.mkdir()
        (d / "broken.json").write_text("{not json", encoding="utf-8")
        monkeypatch.setattr(profile_mod, "_TACTILE_DIR", d)
        with pytest.raises(ConfigurationError):
            load_tactile_profile("broken")
