"""Tests for the ``list_resolvers`` facade.

A front-end builds its pinyin-source picker from this, so the list must
mirror the registry and not depend on which optional pinyin wheels
happen to be installed.
"""

from __future__ import annotations

import sys

from brailix.frontend.zh.pinyin import list_resolvers
from brailix.frontend.zh.pinyin.registry import resolver_registry


def test_lists_every_registered_resolver():
    assert set(list_resolvers()) == {"auto", "null", "g2pm", "g2pw", "pypinyin"}


def test_matches_registry_names():
    assert list_resolvers() == resolver_registry.names()


def test_sorted_with_auto_first():
    names = list_resolvers()
    assert names == sorted(names)
    assert names[0] == "auto"


def test_lists_names_even_when_optional_wheels_absent(monkeypatch):
    # Enumeration is independent of installation: the registry holds lazy
    # loaders, so every resolver name is listed even when its wheel is
    # unimportable (selecting it is what would raise MissingExtraError).
    monkeypatch.setitem(sys.modules, "g2pM", None)
    monkeypatch.setitem(sys.modules, "g2pw", None)
    monkeypatch.setitem(sys.modules, "pypinyin", None)
    assert {"g2pm", "g2pw", "pypinyin"} <= set(list_resolvers())
