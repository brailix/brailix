"""Tests for the M7.3/M7.4 optional MIDI / ABC adapters.

We don't require ``partitura`` or ``abc-xml-converter`` to be
installed for the test suite — the adapters are registered with
``extra=`` so a missing dependency surfaces as a friendly
``MissingExtraError`` via the registry / pipeline's warning
collector. These tests cover the registration + soft-failure paths
without forcing the heavy deps.
"""

from __future__ import annotations

import importlib.util

import pytest

from brailix.core.context import MusicContext
from brailix.frontend.music import parse_music_tree
from brailix.frontend.music.registry import music_source_registry


def _has(pkg: str) -> bool:
    return importlib.util.find_spec(pkg) is not None


class TestRegistration:
    def test_midi_registered_with_extra(self):
        # ``names()`` lists every registered adapter regardless of
        # whether its load() would succeed.
        assert "midi" in music_source_registry.names()

    def test_abc_registered_with_extra(self):
        assert "abc" in music_source_registry.names()


class TestMidiAdapter:
    @pytest.mark.skipif(
        not _has("partitura"),
        reason="partitura not installed — pip install brailix[midi]",
    )
    def test_midi_with_partitura(self):
        # If partitura is around, registry.get() should succeed.
        adapter = music_source_registry.get("midi")
        assert adapter.source == "midi"

    @pytest.mark.skipif(
        _has("partitura"),
        reason="partitura is installed — can't test missing-extra path",
    )
    def test_midi_without_partitura_warns(self):
        # parse_music_tree wraps registry.get() and converts
        # MissingExtraError into a MUSIC_ADAPTER_MISSING warning, so
        # the tree comes back None but the pipeline doesn't crash.
        ctx = MusicContext(source="midi")
        tree = parse_music_tree(b"\x00\x00\x00", ctx)
        assert tree is None
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_ADAPTER_MISSING" in codes


class TestAbcAdapter:
    @pytest.mark.skipif(
        not _has("abc_xml_converter"),
        reason="abc-xml-converter not installed — pip install brailix[abc]",
    )
    def test_abc_with_converter(self):
        adapter = music_source_registry.get("abc")
        assert adapter.source == "abc"

    @pytest.mark.skipif(
        _has("abc_xml_converter"),
        reason="abc-xml-converter installed — can't test missing-extra path",
    )
    def test_abc_without_converter_warns(self):
        ctx = MusicContext(source="abc")
        tree = parse_music_tree("X:1\nT:Test\nM:4/4\nK:C\nCDEF|", ctx)
        assert tree is None
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_ADAPTER_MISSING" in codes
