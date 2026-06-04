"""Shared fixtures for the golden test suite.

The ``pipe`` fixture is module-scoped (not session-scoped) so each test
file constructs the Pipeline once but the underlying registries stay
clean between files.

The golden corpus is locked against the **jieba + pypinyin** pair —
that's the closest "real user" setup available without dragging in
HanLP / g2pw (which need bigger models and Python 3.11+ in some
transitive deps). jieba is required for the goldens: if it's missing
the whole suite skips, exactly the way we skip on missing
``latex2mathml``.
"""

from __future__ import annotations

import pytest

pytest.importorskip("latex2mathml.converter")
pytest.importorskip("jieba")
pytest.importorskip("pypinyin")

from brailix import Pipeline


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    """One Pipeline per test module — cheap to construct, kept hot for
    a file's cases to avoid re-loading profile JSON on every test.

    We pin **both** the Chinese analyzer (``"jieba"``) and the pinyin
    resolver (``"pypinyin"``) so the output is deterministic regardless
    of which optional packages are installed and regardless of how
    other tests in the suite monkeypatch ``sys.modules``. The default
    ``"auto"`` selection cascade would otherwise depend on package
    availability and on cached registry state that earlier-running
    tests can taint (e.g. by stuffing a fake ``pypinyin`` module into
    the registry cache).

    To keep insulation tight we also clear the resolver / analyzer
    registries at the start of every test that uses the ``pipe``
    fixture — see the autouse fixture below.
    """
    # jieba gives word-level tokenisation: 你好 / 世界 each become one
    # Word node, so blanks land between words instead of between every
    # hanzi. This matches what a default-install user sees.
    return Pipeline(analyzer="jieba", resolver="pypinyin")


@pytest.fixture(autouse=True)
def _isolate_registries():
    """Drop any cached resolver / analyzer instances before each test.

    Other tests in the suite create instances bound to monkeypatched
    fake modules and leave them in the registry cache. The golden tests
    need to talk to the real adapters, so we evict the cache and let
    each test rebuild from a clean slate.
    """
    from brailix.frontend.zh.analyzer.registry import analyzer_registry
    from brailix.frontend.zh.pinyin.registry import resolver_registry

    resolver_registry.clear_cache()
    analyzer_registry.clear_cache()
    yield
    resolver_registry.clear_cache()
    analyzer_registry.clear_cache()
