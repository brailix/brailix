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


class TestMidiRealBytes:
    """Real MIDI bytes through the real mido + partitura chain — no
    registry mock, no monkeypatch (R1-T1).

    Every MIDI test in this repo used to either replace the registry
    with a recording fake (``tests/input/test_music_xml.py``) or skip
    when the extra wasn't installed, so the adapter's decode and
    exception paths had no un-mocked coverage anywhere they could
    actually run. The hand-assembled bytes below exercise the wire
    format itself — MThd/MTrk headers, VLQ delta times, running
    note_on/off events, the end-of-track meta — rather than mido's own
    serialiser, so a decoder regression fails here instead of in a
    reader's document."""

    @staticmethod
    def _ctx() -> MusicContext:
        return MusicContext(profile="cn_current", source="midi")

    def test_str_input_is_refused_without_any_dependency(self):
        # MIDI is binary; a str payload is rejected before any import,
        # so this path needs no extra installed and runs everywhere.
        from brailix.frontend.music.adapters.midi import MidiSourceAdapter

        out = MidiSourceAdapter().to_musicxml("not bytes", self._ctx())
        assert "music-error" in out
        assert "requires bytes input" in out

    def test_empty_payload_is_refused_without_any_dependency(self):
        from brailix.frontend.music.adapters.midi import MidiSourceAdapter

        out = MidiSourceAdapter().to_musicxml(b"", self._ctx())
        assert "music-error" in out
        assert "empty midi payload" in out

    @pytest.mark.skipif(
        not (_has("partitura") and _has("mido")),
        reason="partitura/mido not installed — pip install brailix[midi]",
    )
    def test_hand_built_midi_bytes_decode_to_notes(self):
        # A minimal type-0 MIDI file assembled from raw bytes: header
        # (format 0, one track, 480 ticks per quarter) plus one track
        # carrying note_on(60) at delta 0, note_off at delta 480
        # (VLQ 0x83 0x60), and the end-of-track meta. The real decoder
        # must read the note back out with no diagnostics.
        events = (
            b"\x00\x90\x3C\x50"        # delta 0 — note_on 60, velocity 80
            b"\x83\x60\x80\x3C\x00"    # delta 480 — note_off 60
            b"\x00\xFF\x2F\x00"        # delta 0 — end_of_track
        )
        blob = (
            b"MThd"
            + b"\x00\x00\x00\x06"      # header length 6
            + b"\x00\x00"              # format 0
            + b"\x00\x01"              # one track
            + b"\x01\xE0"              # 480 ticks per quarter
            + b"MTrk"
            + b"\x00\x00\x00\x0D"      # track length 13
            + events
        )
        tree = parse_music_tree(blob, self._ctx())
        assert tree is not None
        assert tree.tag == "score-partwise"
        assert len(tree.findall(".//note")) == 1
        assert not any(
            w.code.startswith("MUSIC_") for w in self._ctx().warnings.warnings
        )

    @pytest.mark.skipif(
        not (_has("partitura") and _has("mido")),
        reason="partitura/mido not installed — pip install brailix[midi]",
    )
    def test_bad_magic_soft_fails_as_music_error(self):
        # A container that isn't MIDI at all: mido raises ("MThd not
        # found"), the adapter wraps it — a <music-error> doc comes
        # back, never an exception, never a silent wrong score.
        blob = b"XXXX" + b"\x00\x00\x00\x06\x00\x00\x00\x01\x01\xE0"
        tree = parse_music_tree(blob, self._ctx())
        assert tree is not None
        err = tree.find(".//music-error")
        assert err is not None
        assert "MThd not found" in (err.get("data-reason") or "")

    @pytest.mark.skipif(
        not (_has("partitura") and _has("mido")),
        reason="partitura/mido not installed — pip install brailix[midi]",
    )
    @pytest.mark.parametrize(
        "blob",
        [
            b"MThd\x00\x00\x00",   # truncated mid-header
            b"\x00\x01\x02\x03",   # garbage bytes
            b"MThd\x00\x00\x00\x06\x00\x00",  # header cut past the length
        ],
    )
    def test_malformed_bytes_soft_fail_as_music_error(self, blob):
        # Any decode exception lands in the adapter's wide except and
        # surfaces as the standard soft-failure tree.
        tree = parse_music_tree(blob, self._ctx())
        assert tree is not None
        err = tree.find(".//music-error")
        assert err is not None
        assert (err.get("data-reason") or "").startswith("partitura error")


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
        # ModuleNotFoundError: what ``import partitura`` really raises when the
        # extra is not installed, and the only ImportError shape the registry
        # turns into an install hint.
        def _loader():
            raise ModuleNotFoundError(
                "No module named 'partitura'", name="partitura"
            )

        with music_source_registry.overriding("fake-optional", _loader, extra="midi"):
            ctx = MusicContext(profile="cn_current", source="fake-optional")
            tree = parse_music_tree(b"\x00", ctx)
            assert tree is None
            warning = next(
                w
                for w in ctx.warnings.warnings
                if w.code == "MUSIC_ADAPTER_MISSING"
            )
            assert "pip install brailix[midi]" in warning.message
