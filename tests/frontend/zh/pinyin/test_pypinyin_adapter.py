"""pypinyin adapter specifics: the missing-extra contract and the ``_load``
wiring (TONE3 style + neutral-tone-with-five flags).

Per-token alignment arithmetic, mismatch bail-out and the structural
resolver contract are property-tested for every registered resolver in
``test_resolver_contract_properties.py`` — no per-adapter copies here.
"""

from __future__ import annotations

import sys
import types

import pytest

from brailix.core.errors import MissingExtraError
from brailix.frontend.zh.pinyin.adapters.pypinyin import PypinyinResolver
from brailix.frontend.zh.pinyin.registry import resolver_registry


def test_missing_pypinyin_surfaces_missing_extra_error(monkeypatch):
    resolver_registry.clear_cache()
    monkeypatch.setitem(sys.modules, "pypinyin", None)
    with pytest.raises(MissingExtraError) as ei:
        resolver_registry.get("pypinyin")
    assert ei.value.extra == "pypinyin"
    assert "pip install brailix[pypinyin]" in str(ei.value)


class TestLoaderWithFakeModule:
    def test_load_wires_lazy_pinyin(self, monkeypatch):
        """``_load`` must produce a converter that delegates to
        ``pypinyin.lazy_pinyin`` with the expected style flags."""
        fake_module = types.ModuleType("pypinyin")

        class _Style:
            TONE3 = "TONE3"

        captured: dict = {}

        def fake_lazy_pinyin(text, style=None, neutral_tone_with_five=False):
            captured["text"] = text
            captured["style"] = style
            captured["neutral_tone_with_five"] = neutral_tone_with_five
            return ["wo3"]

        fake_module.Style = _Style
        fake_module.lazy_pinyin = fake_lazy_pinyin
        monkeypatch.setitem(sys.modules, "pypinyin", fake_module)
        resolver_registry.clear_cache()

        adapter = resolver_registry.get("pypinyin")
        assert isinstance(adapter, PypinyinResolver)
        # Run the converter — it should call our fake with TONE3.
        assert adapter.converter("我") == ["wo3"]
        assert captured == {
            "text": "我",
            "style": "TONE3",
            "neutral_tone_with_five": True,
        }
