"""Tests for :mod:`brailix.core.inline_math` — the deferred inline-math
island codec shared by the input layer (writer) and the frontend (reader).
"""

from __future__ import annotations

import pytest

from brailix.core import inline_math


class TestRoundTrip:
    def test_wrap_unwrap_is_lossless_for_plain_payload(self) -> None:
        island = inline_math.wrap("omml", "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>")
        assert inline_math.unwrap(island) == (
            "omml",
            "<m:oMath><m:r><m:t>x</m:t></m:r></m:oMath>",
        )

    def test_source_name_round_trips(self) -> None:
        for source in ("omml", "eq_field", "mathml", "latex"):
            src, payload = inline_math.unwrap(inline_math.wrap(source, "<x/>"))
            assert src == source and payload == "<x/>"

    def test_inner_dollar_round_trips(self) -> None:
        # A literal ``$`` in the payload (e.g. currency in an ``<m:t>``) must
        # survive: it is escaped on wrap and restored on unwrap, never left
        # as a raw ``$`` that would terminate the segmenter's span early.
        island = inline_math.wrap("omml", "a$b$c")
        assert "$" not in island[1:-1]  # only the two wrappers are raw ``$``
        assert inline_math.unwrap(island) == ("omml", "a$b$c")

    def test_whitespace_is_flattened(self) -> None:
        # Newlines and whitespace runs collapse to single spaces so the
        # island lives on one line (the segmenter rejects an inner newline);
        # leading/trailing whitespace is trimmed.
        _, payload = inline_math.unwrap(inline_math.wrap("omml", "  a\n\t b   "))
        assert payload == "a b"


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
    def test_island_carries_no_inner_dollar_or_newline(self) -> None:
        # The frontend segmenter's inline-math scan rejects an inner ``$``
        # or newline inside a ``$...$`` region. A wrapped island must
        # therefore expose neither between its wrappers, or it would be
        # split / truncated before the normalizer sees it.
        island = inline_math.wrap("omml", "x$y\nz")
        body = island[1:-1]
        assert "$" not in body and "\n" not in body

    def test_island_matches_the_segmenter_pattern_whole(self) -> None:
        # Belt-and-braces: the real segmenter scan protects a wrapped island
        # in full, so deferred math is protected exactly like ``$x^2$``.
        from brailix.frontend.segment import _find_protected_regions

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
