"""Automatic Chinese-analyzer selection.

Mirrors :mod:`brailix.frontend.zh.pinyin.adapters.auto`: the
``zh.tokenize`` entry point picks the best installed tokenizer.
``thulac`` leads the chain — it bundles its model in the wheel, so it
works offline with no download (unlike ``hanlp``, whose weights are
fetched on first use). The chain then falls back to ``hanlp``, then the
small ``jieba``, and finally the dependency-free ``char`` fallback so
the pipeline runs even on a bare install.

Order is a preference among what is **installed**, never an assumption
that anything is: each candidate that can't load raises one of
:data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS` and the chain
steps past it. A deployment that ships without ``thulac`` and ``hanlp``
therefore lands on ``jieba`` with no configuration — which is what the
packaged distributions do, since ``thulac``'s wheel carries a ~100 MB
model. Dropping a name from this list is a different, stronger act: it
would deny the engine to a caller who *did* install it.

The delegate is resolved lazily on first call and cached for the
lifetime of the AutoChineseAnalyzer instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brailix.core.context import FrontendContext
from brailix.core.errors import (
    CANDIDATE_UNAVAILABLE_ERRORS,
    UnknownAdapterError,
)
from brailix.frontend.zh.analyzer import ChineseAnalyzer
from brailix.frontend.zh.tokens import ChineseToken

# The shared list plus one addition this chain owns. OSError: a candidate's
# loader touched the filesystem (e.g. created its model dir) and failed — a
# read-only models root when brailix runs inside another application's frozen
# interpreter. That is this chain's own known failure mode rather than a
# general "unavailable" signal, so it stays local instead of widening the
# shared tuple (see :data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS`
# for why that distinction is kept).
#
# Bound to a name rather than unpacked inline in the ``except``: a starred
# tuple there is not something a type checker can verify.
_UNAVAILABLE: tuple[type[Exception], ...] = (
    *CANDIDATE_UNAVAILABLE_ERRORS,
    OSError,
)


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
            except _UNAVAILABLE as e:
                # Candidate unavailable — fall through to the next. The
                # shipping default chain must degrade to char, not crash the
                # compile.
                last_error = e

        if last_error is not None:
            raise last_error
        raise UnknownAdapterError("auto Chinese analyzer has no candidates")


def _load() -> AutoChineseAnalyzer:
    return AutoChineseAnalyzer()
