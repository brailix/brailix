"""Japanese analyzer registry (name → adapter, lazy-loaded).

Mirrors :mod:`brailix.frontend.zh.analyzer.registry`: adapters register a
zero-arg loader; the third-party library is imported only inside that
loader, so an environment without janome / sudachi / fugashi still runs
the dependency-free ``kana`` analyzer. Missing-extra requests raise a
clear :class:`~brailix.core.errors.MissingExtraError`.
"""

from __future__ import annotations

from brailix.core.registry import Registry
from brailix.frontend.ja.analyzer import JapaneseAnalyzer

analyzer_registry: Registry[JapaneseAnalyzer] = Registry(
    "ja_analyzer", protocol=JapaneseAnalyzer
)


def _register_builtin() -> None:
    from brailix.frontend.ja.analyzer.adapters import (
        auto,
        fugashi,
        janome,
        kana,
        sudachi,
    )

    analyzer_registry.register("auto", auto._load)
    analyzer_registry.register("kana", kana._load)
    analyzer_registry.register(
        "janome", janome._load, extra="janome", probe="janome"
    )
    analyzer_registry.register(
        "sudachi", sudachi._load, extra="sudachi", probe="sudachipy"
    )
    analyzer_registry.register(
        "fugashi", fugashi._load, extra="fugashi", probe="fugashi"
    )


_register_builtin()


# See :mod:`brailix.frontend.zh.analyzer.registry`: a registry module publishes
# its registry and nothing else. ``JapaneseAnalyzer`` is promised from
# :mod:`brailix.frontend.ja.analyzer`, ``Registry`` is internal machinery.
__all__ = ("analyzer_registry",)
