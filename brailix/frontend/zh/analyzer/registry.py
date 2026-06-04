"""Registry for Chinese analyzer adapters.

Adapters self-register at import time. The ``char`` adapter is always
present and has no third-party dependencies, so the registry is never
empty even on a bare install.
"""

from __future__ import annotations

from brailix.core.protocols import ChineseAnalyzer
from brailix.core.registry import Registry

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
    analyzer_registry.register("thulac", thulac._load, extra="thulac")
    analyzer_registry.register("jieba", jieba._load, extra="jieba")
    analyzer_registry.register("hanlp", hanlp._load, extra="hanlp")


_register_builtin()
