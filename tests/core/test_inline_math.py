"""Tests for :mod:`brailix.core.inline_math` — the deferred inline-math
island codec shared by the input layer (writer) and the frontend (reader).
"""

from __future__ import annotations

import pytest

from brailix.core import inline_math


class TestRoundTrip:
    # Round-trip losslessness (dialect tag, escaped ``$``, fixed point) is
    # contract-tested over generated sources/payloads in
    # tests/crosshair/contracts.py::check_inline_math_island_codec.

    def test_whitespace_is_flattened(self) -> None:
        # Newlines and whitespace runs collapse to single spaces so the
        # island lives on one line (the segmenter rejects an inner newline);
        # leading/trailing whitespace is trimmed.
        _, payload = inline_math.unwrap(inline_math.wrap("omml", "  a\n\t b   "))
        assert payload == "a b"

    @pytest.mark.parametrize(
        "payload",
        [
            "a\x1cb",  # File Separator
            "a\x1db",  # Unit Separator — the format's own field delimiter
            "a\x1eb",  # Record Separator — the format's own escape char
            "a\x1fb",  # Group Separator — the ``$`` escape's marker half
            "a\x1cb\x1dc\x1dd\x1ee\x1ff",  # all four interleaved
            "\x1c\x1d\x1e\x1f",  # nothing but controls
            "$\x1e$",  # dollar next to a payload-borne RS
            "\x1e$",  # RS then dollar at the field's edge
            "$\x1e",  # dollar then RS at the field's edge
            "a \x1c b",  # control adjacent to single spaces: byte-exact
        ],
    )
    def test_separator_controls_round_trip_byte_exact(self, payload: str) -> None:
        # ``\s`` matches U+001C–U+001F, so the flattening used to eat them
        # (``wrap("omml", "a\x1eb")`` came back as ``"a b"``) and the RS→``$``
        # unescape used to turn a payload-borne RS into a dollar. They are
        # payload data now: flattened never, escaped only where they collide
        # with the wire format's own machinery, and restored byte for byte.
        src, out = inline_math.unwrap(inline_math.wrap("omml", payload))
        assert src == "omml"
        assert out == payload

    @pytest.mark.parametrize(
        ("payload", "expected"),
        [
            (" \x1c", "\x1c"),  # edge whitespace trims, the control does not
            ("\x1e ", "\x1e"),
            ("  \x1f  ", "\x1f"),
            ("a  \x1c  b", "a \x1c b"),  # runs flatten, the control does not
        ],
    )
    def test_edge_trim_keeps_controls_drops_real_whitespace(
        self, payload: str, expected: str
    ) -> None:
        # ``str.strip()``'s default set classes the controls as whitespace,
        # so the OLD codec deleted a leading/trailing one outright. The trim
        # now drops only genuinely flattenable whitespace: the control at
        # the field's edge survives, the real space around it does not.
        _, out = inline_math.unwrap(inline_math.wrap("omml", payload))
        assert out == expected

    def test_wrapped_controls_survive_the_segmenter_scan(self) -> None:
        # The island carrying controls must still parse as ONE atomic
        # protected region — controls are not ``$`` and not newlines, so
        # the scan closes on the island's own trailing ``$``.
        from brailix.frontend.segmentation import _find_protected_regions

        island = inline_math.wrap("omml", "a\x1cb\x1ec$d\x1ff")
        text = "前 " + island + " 后"
        regions = _find_protected_regions(text)
        assert len(regions) == 1
        start, end, type_name = regions[0]
        assert type_name == "math_inline"
        assert text[start:end] == island


class TestIsTagged:
    def test_true_for_wrapped(self) -> None:
        assert inline_math.is_tagged(inline_math.wrap("omml", "<x/>"))

    @pytest.mark.parametrize(
        "piece",
        [
            "$x^2$",  # user-typed LaTeX
            "$<math><mi>x</mi></math>$",  # eager MathML (MTEF / cluster)
            "plain text",
            "$",
            "",
            "$$display$$",
        ],
    )
    def test_false_for_non_tagged(self, piece: str) -> None:
        assert not inline_math.is_tagged(piece)


class TestSegmenterContract:
    def test_island_matches_the_segmenter_pattern_whole(self) -> None:
        # Belt-and-braces: the real segmenter scan protects a wrapped island
        # in full, so deferred math is protected exactly like ``$x^2$``.
        from brailix.frontend.segmentation import _find_protected_regions

        island = inline_math.wrap("eq_field", r"eq \f(1,2)")
        text = "前 " + island + " 后"
        regions = _find_protected_regions(text)
        assert len(regions) == 1
        start, end, type_name = regions[0]
        assert type_name == "math_inline"
        assert text[start:end] == island


class TestErrors:
    @pytest.mark.parametrize("bad", ["$x^2$", "$<math>x</math>$", "not an island", ""])
    def test_unwrap_rejects_non_tagged(self, bad: str) -> None:
        with pytest.raises(ValueError):
            inline_math.unwrap(bad)

    @pytest.mark.parametrize("bad", ["$\x1donly-one-separator$", "$\x1d$"])
    def test_unwrap_rejects_malformed_tagged_island(self, bad: str) -> None:
        # Opens with $ + Unit Separator (so is_tagged says yes) but carries
        # only ONE field — the interior split can't produce (source,
        # payload). unwrap must reject it, not index past the end. (The
        # wrap/unwrap round-trip properties only ever see well-formed
        # islands, so this branch needs its own pin — flagged by mutation
        # testing.)
        assert inline_math.is_tagged(bad)
        with pytest.raises(ValueError):
            inline_math.unwrap(bad)
