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
        not (_has("partitura") and _has("mido")),
        reason="partitura/mido not installed — pip install brailix[midi]",
    )
    def test_midi_converts_to_musicxml_with_notes(self):
        # Functional smoke: a real (tiny) MIDI must convert into a
        # normalised <score-partwise> tree carrying notes. This guards
        # the partitura entry point — load_score() takes a *filename*,
        # not our in-memory buffer, so the old load_score(BytesIO) call
        # failed for every input and silently produced a music-error.
        # registry.get() succeeding (above) never exercised conversion.
        import io

        import mido

        mf = mido.MidiFile()
        track = mido.MidiTrack()
        mf.tracks.append(track)
        track.append(mido.MetaMessage("set_tempo", tempo=500000, time=0))
        track.append(mido.Message("note_on", note=60, velocity=80, time=0))
        track.append(mido.Message("note_off", note=60, velocity=0, time=480))
        track.append(mido.MetaMessage("end_of_track", time=0))
        buf = io.BytesIO()
        mf.save(file=buf)

        ctx = MusicContext(profile="cn_current", source="midi")
        tree = parse_music_tree(buf.getvalue(), ctx)
        assert tree is not None
        assert tree.tag == "score-partwise"
        assert tree.findall(".//note")  # at least one note survived
        assert not any(
            w.code.startswith("MUSIC_") for w in ctx.warnings.warnings
        )

    @pytest.mark.skipif(
        _has("partitura"),
        reason="partitura is installed — can't test missing-extra path",
    )
    def test_midi_without_partitura_warns(self):
        # parse_music_tree wraps registry.get() and converts
        # MissingExtraError into a MUSIC_ADAPTER_MISSING warning, so
        # the tree comes back None but the pipeline doesn't crash.
        ctx = MusicContext(profile="cn_current", source="midi")
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
        not _has("abc_xml_converter"),
        reason="abc-xml-converter not installed — pip install brailix[abc]",
    )
    def test_abc_converts_to_musicxml_with_notes(self):
        # Functional smoke: a 4-note ABC tune must convert into a
        # normalised <score-partwise> tree with 4 notes. Guards the
        # convert_abc2xml entry point — the old code probed an internal
        # 4-arg helper (abc2xml.convert), called it with one arg, hit
        # TypeError, and silently produced a music-error for every tune.
        ctx = MusicContext(profile="cn_current", source="abc")
        tree = parse_music_tree("X:1\nT:probe\nM:4/4\nL:1/4\nK:C\nCDEF|\n", ctx)
        assert tree is not None
        assert tree.tag == "score-partwise"
        assert len(tree.findall(".//note")) == 4
        assert not any(
            w.code.startswith("MUSIC_") for w in ctx.warnings.warnings
        )

    @pytest.mark.skipif(
        not _has("abc_xml_converter"),
        reason="abc-xml-converter not installed — pip install brailix[abc]",
    )
    def test_abc_multi_score_warns_about_dropped_tunes(self, monkeypatch):
        # music-3: if the converter returns more than one per-tune score, only
        # the first is translated, so the rest must be flagged (not silently
        # dropped). The installed converter merges tunes into a single score,
        # so simulate the multi-score return the adapter's own comment
        # anticipates and assert it warns + keeps the first.
        from abc_xml_converter import abc2xml

        monkeypatch.setattr(abc2xml, "getXmlScores", lambda _text: ["<a/>", "<b/>"])
        ctx = MusicContext(profile="cn_current", source="abc")
        adapter = music_source_registry.get("abc")
        out = adapter.to_musicxml("X:1\nK:C\nCDEF|", ctx)
        assert out == "<a/>"  # the first tune is kept
        assert any(
            w.code == "MUSIC_UNSUPPORTED_NOTATION" and "tunes" in w.message
            for w in ctx.warnings.warnings
        )

    @pytest.mark.skipif(
        _has("abc_xml_converter"),
        reason="abc-xml-converter installed — can't test missing-extra path",
    )
    def test_abc_without_converter_warns(self):
        ctx = MusicContext(profile="cn_current", source="abc")
        tree = parse_music_tree("X:1\nT:Test\nM:4/4\nK:C\nCDEF|", ctx)
        assert tree is None
        codes = [w.code for w in ctx.warnings.warnings]
        assert "MUSIC_ADAPTER_MISSING" in codes


class TestMissingExtraPath:
    """The two skipif tests above each only run in one kind of
    environment; this one pins the missing-extra path in every
    environment by registering an adapter whose loader fails the way
    a missing optional dependency does."""

    def test_missing_extra_warns_with_install_hint(self):
        def _loader():
            raise ImportError("synthetic: optional dependency absent")

        music_source_registry.register("fake-optional", _loader, extra="midi")
        try:
            ctx = MusicContext(profile="cn_current", source="fake-optional")
            tree = parse_music_tree(b"\x00", ctx)
            assert tree is None
            warning = next(
                w
                for w in ctx.warnings.warnings
                if w.code == "MUSIC_ADAPTER_MISSING"
            )
            assert "pip install brailix[midi]" in warning.message
        finally:
            music_source_registry.unregister("fake-optional")
