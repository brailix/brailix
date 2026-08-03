"""Registry for Chinese analyzer adapters.

Adapters self-register at import time. The ``char`` adapter is always
present and has no third-party dependencies, so the registry is never
empty even on a bare install.
"""

from __future__ import annotations

from brailix.core.registry import Registry
from brailix.frontend.zh.analyzer import ChineseAnalyzer

analyzer_registry: Registry[ChineseAnalyzer] = Registry(
    "zh_analyzer", protocol=ChineseAnalyzer
)


def _register_builtin() -> None:
    from brailix.frontend.zh.analyzer.adapters import (  # noqa: F401
        auto,
        char,
        hanlp,
        jieba,
        thulac,
    )

    analyzer_registry.register("auto", auto._load)
    analyzer_registry.register("char", char._load)
    # ``probe`` is the MODULE each loader imports, not the extra that
    # installs it — they differ often enough that deriving one from the other
    # is a bug waiting (see Registry.register). It is what lets a front-end
    # offer only the engines that are actually installed instead of listing
    # every registered name and failing at compile time.
    analyzer_registry.register(
        "thulac", thulac._load, extra="thulac", probe="thulac"
    )
    analyzer_registry.register(
        "jieba", jieba._load, extra="jieba", probe="jieba"
    )
    analyzer_registry.register(
        "hanlp", hanlp._load, extra="hanlp", probe="hanlp"
    )


_register_builtin()
