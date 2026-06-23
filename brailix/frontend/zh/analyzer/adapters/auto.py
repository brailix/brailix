"""Automatic Chinese-analyzer selection.

Mirrors :mod:`brailix.frontend.zh.pinyin.adapters.auto`: the
``zh.tokenize`` entry point picks the best installed tokenizer.
``thulac`` leads the chain — it bundles its model in the wheel, so it
works offline with no download (unlike ``hanlp``, which the Model
Manager fetches on demand) — making it the shipping default. The chain
then falls back to ``hanlp``, then the small ``jieba``, and finally the
dependency-free ``char`` fallback so the pipeline runs even on a bare
install.

The delegate is resolved lazily on first call and cached for the
lifetime of the AutoChineseAnalyzer instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brailix.core.context import FrontendContext
from brailix.core.errors import (
    MissingExtraError,
    ModelNotInstalledError,
    UnknownAdapterError,
)
from brailix.core.protocols import ChineseAnalyzer
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class AutoChineseAnalyzer:
    """Delegating analyzer that resolves to the first viable candidate."""

    name: str = "auto"
    preferred: tuple[str, ...] = ("thulac", "hanlp", "jieba", "char")
    _delegate: ChineseAnalyzer | None = field(default=None, init=False, repr=False)

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[ChineseToken]:
        return self._load_delegate().analyze(text, ctx)

    def _load_delegate(self) -> ChineseAnalyzer:
        if self._delegate is not None:
            return self._delegate

        from brailix.frontend.zh.analyzer.registry import analyzer_registry

        last_error: Exception | None = None
        for name in self.preferred:
            if name == self.name:
                continue
            try:
                self._delegate = analyzer_registry.get(name)
                return self._delegate
            except (KeyError, MissingExtraError, ModelNotInstalledError, OSError) as e:
                # ModelNotInstalledError: a candidate (e.g. hanlp under
                # managed download) is importable but its model isn't
                # downloaded yet. OSError: a candidate's loader touched the
                # filesystem (e.g. created its model dir) and failed — a
                # read-only models root when brailix runs inside another
                # app's frozen interpreter. Treat both like any other
                # "candidate unavailable" and fall through to the next — the
                # shipping default chain must degrade to char, not crash the
                # compile.
                last_error = e

        if last_error is not None:
            raise last_error
        raise UnknownAdapterError("auto Chinese analyzer has no candidates")


def _load() -> AutoChineseAnalyzer:
    return AutoChineseAnalyzer()
