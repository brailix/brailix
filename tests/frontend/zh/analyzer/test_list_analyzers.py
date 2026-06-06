"""Tests for the ``list_analyzers`` facade.

A front-end builds its tokenizer picker from this, so the list must
mirror the registry (the single source of truth) and stay stable
regardless of which optional tokenizer wheels are installed.
"""

from __future__ import annotations

import sys

from brailix.frontend.zh.analyzer import list_analyzers
from brailix.frontend.zh.analyzer.registry import analyzer_registry


def test_lists_every_registered_analyzer():
    assert set(list_analyzers()) == {"auto", "char", "thulac", "jieba", "hanlp"}


def test_matches_registry_names():
    # The facade is exactly the registry's view — no curated copy that
    # could drift from what's actually registered.
    assert list_analyzers() == analyzer_registry.names()


def test_sorted_with_auto_first():
    names = list_analyzers()
    assert names == sorted(names)
    assert names[0] == "auto"


def test_lists_names_even_when_optional_wheels_absent(monkeypatch):
    # Registration records a lazy loader, not the imported library, so a
    # name appears whether or not its wheel is installed. Simulate a bare
    # install: the heavy tokenizers must still be listed — selecting one
    # is what raises MissingExtraError, not enumerating it.
    monkeypatch.setitem(sys.modules, "thulac", None)
    monkeypatch.setitem(sys.modules, "hanlp", None)
    monkeypatch.setitem(sys.modules, "jieba", None)
    assert {"thulac", "hanlp", "jieba"} <= set(list_analyzers())
