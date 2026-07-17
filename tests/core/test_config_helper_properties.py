"""Property-based tests for the pure config helpers.

Three small functions every profile load leans on, pinned against
independent models over generated inputs:

* ``_feature_lookup`` — dotted-path walk over a (possibly) nested feature
  dict: equals a hand-rolled walk for any dict shape, any path, default
  on any miss or non-dict intermediate;
* ``_feature_keys_to_try`` — the canonical + legacy key variants: checked
  data-driven over the REAL alias tables (both directions), so the test
  can't drift from the mapping it guards;
* ``_to_dots`` / ``_extract_dots`` — the single point every profile dot
  tuple is built: order-preserving, exactly the 1..8-unique domain, and
  the documented acceptance model for bare-list vs cell-spec-object vs
  junk.
"""

from __future__ import annotations

from typing import Any

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core.config._helpers import (
    _FEATURE_DOTTED_TO_FLAT,
    _FEATURE_FLAT_ALIASES,
    _extract_dots,
    _feature_keys_to_try,
    _feature_lookup,
    _to_dots,
)
from brailix.core.errors import ConfigurationError

_SEGMENTS = ["math", "zh", "music", "tone", "op_spacing", "x"]

_leaves = st.one_of(st.booleans(), st.integers(-3, 3), st.text(max_size=3))
_feature_dicts = st.recursive(
    _leaves,
    lambda children: st.dictionaries(st.sampled_from(_SEGMENTS), children, max_size=3),
    max_leaves=12,
)
_keys = st.lists(st.sampled_from(_SEGMENTS), min_size=1, max_size=3).map(".".join)


def _model_lookup(features: dict, key: str, default: Any) -> Any:
    if "." not in key:
        return features.get(key, default)
    node: Any = features
    for segment in key.split("."):
        if not isinstance(node, dict) or segment not in node:
            return default
        node = node[segment]
    return node


class TestFeatureLookup:
    @settings(max_examples=150)
    @given(
        features=st.dictionaries(st.sampled_from(_SEGMENTS), _feature_dicts, max_size=3),
        key=_keys,
        default=st.sampled_from([None, False, "fallback"]),
    )
    def test_matches_the_documented_walk(self, features: dict, key: str, default: Any) -> None:
        assert _feature_lookup(features, key, default) == _model_lookup(
            features, key, default
        )

    def test_alias_tables_round_both_directions(self) -> None:
        # Data-driven over the real tables: every legacy flat key tries
        # itself first, then its dotted form — and vice versa. A new alias
        # is covered the moment it's added to the map.
        assert _FEATURE_FLAT_ALIASES, "alias table unexpectedly empty"
        for flat, dotted in _FEATURE_FLAT_ALIASES.items():
            assert _feature_keys_to_try(flat) == [flat, dotted]
        for dotted, flat in _FEATURE_DOTTED_TO_FLAT.items():
            assert _feature_keys_to_try(dotted) == [dotted, flat]

    @settings(max_examples=60)
    @given(key=_keys)
    def test_unmapped_key_tries_only_itself(self, key: str) -> None:
        if key not in _FEATURE_FLAT_ALIASES and key not in _FEATURE_DOTTED_TO_FLAT:
            assert _feature_keys_to_try(key) == [key]


class TestDotHelpers:
    @settings(max_examples=150)
    @given(dots=st.lists(st.integers(-2, 10), max_size=6))
    def test_to_dots_accepts_exactly_the_valid_domain(self, dots: list[int]) -> None:
        # Valid: every dot 1..8, no repeats. Order is PRESERVED (unlike
        # BrailleCell, which canonicalizes) — table files may spell dots in
        # notation order. Anything else is a loud ConfigurationError.
        valid = all(1 <= d <= 8 for d in dots) and len(set(dots)) == len(dots)
        if valid:
            assert _to_dots(dots) == tuple(dots)
        elif not dots:
            assert _to_dots(dots) == ()
        else:
            with pytest.raises(ConfigurationError):
                _to_dots(dots)

    @settings(max_examples=150)
    @given(
        value=st.one_of(
            st.none(),
            st.booleans(),
            st.integers(-2, 9),
            st.text(max_size=3),
            st.lists(st.one_of(st.integers(1, 8), st.text(max_size=1)), max_size=4),
            st.fixed_dictionaries({"dots": st.one_of(st.lists(st.integers(1, 8), max_size=4, unique=True), st.text(max_size=2))}),
            st.fixed_dictionaries({"cells": st.lists(st.integers(1, 8), max_size=2)}),
        )
    )
    def test_extract_dots_acceptance_model(self, value: Any) -> None:
        # Documented model, three outcomes: a bare all-int list or a
        # {"dots": [...]}-spec IS a cell spec — it extracts (empty → ()),
        # and an invalid dot set inside one fails LOUD via _to_dots (a
        # recognized-but-broken spec must not be silently skipped);
        # everything else — None, scalars, a mixed list, a dict without
        # "dots", non-list dots — is "not a cell spec", i.e. None.
        is_int_list = (
            isinstance(value, list) and value and all(isinstance(x, int) for x in value)
        )
        dict_dots = value.get("dots") if isinstance(value, dict) else None
        if is_int_list or isinstance(dict_dots, list):
            dots = value if is_int_list else dict_dots
            if len(set(dots)) == len(dots) and all(1 <= d <= 8 for d in dots):
                assert _extract_dots(value) == tuple(dots)
            else:
                with pytest.raises(ConfigurationError):
                    _extract_dots(value)
        elif isinstance(value, list) and not value:
            assert _extract_dots(value) == ()
        else:
            assert _extract_dots(value) is None
