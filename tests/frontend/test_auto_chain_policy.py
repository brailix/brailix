"""One "candidate unavailable" policy, three ``auto`` chains.

``zh.analyzer``, ``zh.pinyin`` and ``ja.analyzer`` each ship an ``auto``
adapter that walks a preference list, skips whatever isn't usable, and caches
the first engine that loads. The skip condition is the whole contract: catch
too little and a routine "not installed" crashes a translation; catch too much
and a code defect silently changes which engine writes the braille.

The three had drifted to catching four, two and one exception type
respectively, while :class:`~brailix.core.errors.IncompatibleDependencyError`
documented itself as a signal "the ``auto`` selection chains" honour — true of
exactly one of them. They now share
:data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS`, and this pins that
they behave alike.

A shared *contract test*, not a shared helper: zh and ja are independently
replaceable language components (ARCHITECTURE#arch-layers), so welding their
chains onto one ``_pick_delegate`` would trade that property for
duplicate-line removal. A test may span them; the production code may not.

Each chain is driven through its **shipped** preference list with every real
candidate temporarily replaced by a raising loader, so what's checked is the
chain that actually runs, down to the dependency-free engine it lands on.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import (
    CANDIDATE_UNAVAILABLE_ERRORS,
    IncompatibleDependencyError,
    MissingExtraError,
    ModelNotInstalledError,
)
from brailix.frontend.ja.analyzer.adapters.auto import (
    _PREFERENCE as _JA_PREFERENCE,
)
from brailix.frontend.ja.analyzer.adapters.auto import AutoJapaneseAnalyzer
from brailix.frontend.ja.analyzer.registry import (
    analyzer_registry as ja_analyzer_registry,
)
from brailix.frontend.zh.analyzer.adapters.auto import AutoChineseAnalyzer
from brailix.frontend.zh.analyzer.registry import (
    analyzer_registry as zh_analyzer_registry,
)
from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.ir.inline import ChineseToken


@dataclass(frozen=True)
class _Chain:
    """One ``auto`` chain: what it probes, how to run it, where it lands."""

    name: str
    registry: Any
    # The shipped preference entries ahead of the dependency-free fallback.
    probed: tuple[str, ...]
    fallback: str
    build: Callable[[], Any]
    drive: Callable[[Any], Any]
    # The engine ``auto`` actually settled on, read off the built adapter.
    resolved: Callable[[Any], str]


def _delegate_name(adapter: Any) -> str:
    delegate = adapter._delegate
    assert delegate is not None, "auto never resolved a delegate"
    return str(delegate.name)


_CHAINS = (
    _Chain(
        name="zh.analyzer",
        registry=zh_analyzer_registry,
        probed=("thulac", "hanlp", "jieba"),
        fallback="char",
        build=AutoChineseAnalyzer,
        drive=lambda a: a.analyze("我在", FrontendContext(profile="cn_current")),
        resolved=_delegate_name,
    ),
    _Chain(
        name="zh.pinyin",
        registry=resolver_registry,
        probed=("g2pm", "g2pw", "pypinyin"),
        fallback="null",
        build=AutoPinyinResolver,
        drive=lambda a: a.resolve(
            [ChineseToken(surface="我")], FrontendContext(profile="cn_current")
        ),
        resolved=_delegate_name,
    ),
    _Chain(
        name="ja.analyzer",
        registry=ja_analyzer_registry,
        probed=_JA_PREFERENCE,
        fallback="kana",
        build=AutoJapaneseAnalyzer,
        drive=lambda a: a.analyze("ひらがな", FrontendContext(profile="ja_current")),
        resolved=_delegate_name,
    ),
)

_IDS = [chain.name for chain in _CHAINS]

# One instance per type in CANDIDATE_UNAVAILABLE_ERRORS, in the same order.
_UNAVAILABLE_CASES: tuple[Exception, ...] = (
    KeyError("no adapter named 'x'"),
    MissingExtraError(adapter="x", extra="x"),
    ModelNotInstalledError(model_id="x", install_dir="/nowhere"),
    IncompatibleDependencyError(
        "x",
        dependency="numpy",
        installed="9.0",
        requirement="<2",
        reason="the API this adapter calls was removed",
    ),
)
_UNAVAILABLE_IDS = [type(e).__name__ for e in _UNAVAILABLE_CASES]

_PROGRAMMING_ERROR_CASES = (
    AttributeError("'NoneType' object has no attribute 'tokenize'"),
    NameError("name 'model' is not defined"),
    AssertionError(),
)
_PROGRAMMING_ERROR_IDS = [type(e).__name__ for e in _PROGRAMMING_ERROR_CASES]


def _run_with_failing_candidates(chain: _Chain, exc: BaseException) -> Any:
    """Drive ``chain`` with every probed candidate raising ``exc``.

    Only the dependency-free fallback is left able to load, so where the chain
    ends up is unambiguous — and so is a chain that doesn't get there.
    """

    def boom() -> Any:
        raise exc

    with chain.registry.overriding():
        for candidate in chain.probed:
            chain.registry.register(candidate, boom)
        adapter = chain.build()
        chain.drive(adapter)
        return adapter


@pytest.fixture(autouse=True)
def _clear_caches() -> Any:
    for chain in _CHAINS:
        chain.registry.clear_cache()
    yield
    for chain in _CHAINS:
        chain.registry.clear_cache()


# ---------------------------------------------------------------------------
# What a chain must skip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain", _CHAINS, ids=_IDS)
@pytest.mark.parametrize("exc", _UNAVAILABLE_CASES, ids=_UNAVAILABLE_IDS)
def test_every_chain_skips_every_unavailable_candidate(
    chain: _Chain, exc: Exception
) -> None:
    """All four signals mean the same thing in all three chains.

    The drift this closes was invisible from either side alone: an adapter
    author raises ``IncompatibleDependencyError`` exactly as its docstring
    asks, and whether the translation degrades or crashes depends on which
    language the document happens to be in.
    """
    adapter = _run_with_failing_candidates(chain, exc)
    assert chain.resolved(adapter) == chain.fallback


def test_the_cases_cover_the_declared_tuple_exactly() -> None:
    """Adding a type to ``CANDIDATE_UNAVAILABLE_ERRORS`` without adding a case
    here fails now, rather than leaving the new signal unchecked in all three
    chains."""
    assert (
        tuple(type(e) for e in _UNAVAILABLE_CASES) == CANDIDATE_UNAVAILABLE_ERRORS
    )


# ---------------------------------------------------------------------------
# What a chain must not skip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("chain", _CHAINS, ids=_IDS)
@pytest.mark.parametrize(
    "exc", _PROGRAMMING_ERROR_CASES, ids=_PROGRAMMING_ERROR_IDS
)
def test_no_chain_degrades_past_a_programming_error(
    chain: _Chain, exc: BaseException
) -> None:
    """A code defect must not read as "engine unavailable".

    Degrading here is the quiet failure the whole policy exists to prevent: the
    document still translates, a weaker engine writes different braille, and
    the only evidence of the regression — the crash — was consumed making the
    decision to hide it.
    """
    with pytest.raises(type(exc)):
        _run_with_failing_candidates(chain, exc)
