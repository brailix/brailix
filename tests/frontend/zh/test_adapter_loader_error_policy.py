"""One loader-failure policy, three heavyweight adapters.

``hanlp``, ``g2pm`` and ``g2pw`` each build their engine inside a ``_load``
that wraps construction in a wide ``except`` and re-raises as
:class:`~brailix.core.errors.ModelNotInstalledError` /
:class:`~brailix.core.errors.MissingExtraError`. That is deliberate and load
bearing: a missing download or a corrupt weights file must let ``auto`` move
on to the next engine rather than crash a whole translation.

It also makes these boundaries *unlike* the math / music / graphics ones, and
more dangerous. What they raise is not a diagnostic somebody reads later — it
is a **candidate-unavailable signal**, and the ``auto`` chains answer it by
silently selecting a different engine. So a code defect caught here (ours, or
an upstream API that moved under us) produced a translation that succeeded,
with jieba / char / pypinyin / null quietly writing different braille, and no
stack anywhere naming the regression. The one signal a maintainer would have
had — the crash — was the thing being swallowed.

:data:`~brailix.core.errors.PROGRAMMING_ERRORS` exists to say which exceptions
can never be a legitimate response to input, and the three verticals already
re-raise them ahead of their own wide ``except``
(``tests/frontend/test_soft_failure_policy.py``). These three loaders did not.
This pins the same ladder here, for the same reason, and — as there — one
shared *contract test* rather than one shared helper: zh's analyzer and pinyin
subsystems stay independently replaceable, so a test may span them where
production code may not.

Nothing here needs HanLP / g2pM / g2pW installed: each third-party module is
faked in ``sys.modules`` and its constructor raises on demand, so the check
runs in the main CI job rather than only in the weekly heavyweight one.
"""

from __future__ import annotations

import sys
import types
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import (
    PROGRAMMING_ERRORS,
    MissingExtraError,
    ModelNotInstalledError,
)
from brailix.core.models.asset_registry import set_managed_download
from brailix.frontend.zh.analyzer.adapters.auto import AutoChineseAnalyzer
from brailix.frontend.zh.analyzer.registry import analyzer_registry
from brailix.frontend.zh.pinyin.adapters.auto import AutoPinyinResolver
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.frontend.zh.tokens import ChineseToken

# ---------------------------------------------------------------------------
# The three third-party modules, faked down to the one call ``_load`` makes
# ---------------------------------------------------------------------------


def _fake_hanlp(boom: BaseException) -> types.ModuleType:
    mod = types.ModuleType("hanlp")
    mod.pretrained = types.SimpleNamespace(  # type: ignore[attr-defined]
        mtl=types.SimpleNamespace(
            CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH="mtl-model-id"
        )
    )

    def load(_model_id: Any) -> Any:
        raise boom

    mod.load = load  # type: ignore[attr-defined]
    return mod


def _fake_g2pm(boom: BaseException) -> types.ModuleType:
    mod = types.ModuleType("g2pM")  # note the capital M: PyPI ``g2pM``

    class _Model:
        def __init__(self) -> None:
            raise boom

    mod.G2pM = _Model  # type: ignore[attr-defined]
    return mod


def _fake_g2pw(boom: BaseException) -> types.ModuleType:
    mod = types.ModuleType("g2pw")

    class _Converter:
        def __init__(self) -> None:
            raise boom

    mod.G2PWConverter = _Converter  # type: ignore[attr-defined]
    return mod


# ---------------------------------------------------------------------------
# One description per loader
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _Loader:
    """How to make one heavyweight loader fail, and what should happen then."""

    name: str
    registry: Any
    module: str
    fake: Callable[[BaseException], types.ModuleType]
    # What the wide ``except`` re-raises for a genuine environment failure.
    reclassified: type[BaseException]
    # A real environment failure of the kind that catch exists for.
    env_error: BaseException
    # An ``auto`` chain with this candidate first and a dependency-free
    # engine behind it, so a degradation is unambiguous.
    auto: Callable[[], Any]
    drive: Callable[[Any], Any]
    # True when the *fallback* engine produced the output.
    degraded: Callable[[Any], bool]


def _drive_analyzer(adapter: Any) -> Any:
    return adapter.analyze("我在", FrontendContext(profile="cn_current"))


def _drive_resolver(adapter: Any) -> Any:
    return adapter.resolve(
        [ChineseToken(surface="我"), ChineseToken(surface="在")],
        FrontendContext(profile="cn_current"),
    )


_LOADERS = (
    _Loader(
        name="hanlp",
        registry=analyzer_registry,
        module="hanlp",
        fake=_fake_hanlp,
        reclassified=ModelNotInstalledError,
        # HanLP auto-downloads on first load; no network / a truncated archive
        # / a read-only models root all surface as OSError down there.
        env_error=OSError("model download failed"),
        auto=lambda: AutoChineseAnalyzer(preferred=("hanlp", "char")),
        drive=_drive_analyzer,
        # char splits per character and tags nothing — unmistakably not HanLP.
        degraded=lambda out: [t.surface for t in out] == ["我", "在"]
        and all(t.pos is None for t in out),
    ),
    _Loader(
        name="g2pm",
        registry=resolver_registry,
        module="g2pM",
        fake=_fake_g2pm,
        reclassified=MissingExtraError,
        env_error=RuntimeError("corrupt bundled weights"),
        auto=lambda: AutoPinyinResolver(preferred=("g2pm", "null")),
        drive=_drive_resolver,
        degraded=lambda out: all(t.pinyin is None for t in out),
    ),
    _Loader(
        name="g2pw",
        registry=resolver_registry,
        module="g2pw",
        fake=_fake_g2pw,
        reclassified=MissingExtraError,
        env_error=OSError("model download failed"),
        auto=lambda: AutoPinyinResolver(preferred=("g2pw", "null")),
        drive=_drive_resolver,
        degraded=lambda out: all(t.pinyin is None for t in out),
    ),
)

_IDS = [loader.name for loader in _LOADERS]

_PROGRAMMING_ERROR_CASES = [
    AttributeError("'NoneType' object has no attribute 'encode_plus'"),
    NameError("name 'tokenizer' is not defined"),
    AssertionError(),
]
_PROGRAMMING_ERROR_IDS = [type(e).__name__ for e in _PROGRAMMING_ERROR_CASES]


@pytest.fixture(autouse=True)
def _isolated_loaders(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Any:
    """Make every loader's non-injected side effects harmless and repeatable.

    ``hanlp._load`` creates ``models/hanlp/`` under the cwd and exports
    ``HANLP_HOME`` before importing anything, and its transformers guard reads
    installed package metadata — none of which is what these tests are about,
    and all of which would otherwise leak across tests or vary by machine.
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HANLP_HOME", "")
    monkeypatch.setattr(
        "brailix.frontend.zh.analyzer.adapters.hanlp."
        "_check_transformers_compatibility",
        lambda: None,
    )
    # Default (non-managed) download policy: the managed pre-check would raise
    # ModelNotInstalledError before the injected failure could happen.
    set_managed_download(False)
    analyzer_registry.clear_cache()
    resolver_registry.clear_cache()
    yield
    set_managed_download(False)
    analyzer_registry.clear_cache()
    resolver_registry.clear_cache()


def _install(
    loader: _Loader, boom: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, loader.module, loader.fake(boom))


# ---------------------------------------------------------------------------
# Exempt from the wide catch: programming errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loader", _LOADERS, ids=_IDS)
@pytest.mark.parametrize(
    "exc", _PROGRAMMING_ERROR_CASES, ids=_PROGRAMMING_ERROR_IDS
)
def test_an_explicitly_selected_loader_propagates_a_programming_error(
    loader: _Loader, exc: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``Pipeline(analyzer="hanlp")`` asked for one engine by name. If building
    it hits a code defect, the caller must see *that* — not "the model is not
    installed", which sends them off to download a model that is already
    there."""
    _install(loader, exc, monkeypatch)
    with pytest.raises(type(exc)):
        loader.registry.get(loader.name)


@pytest.mark.parametrize("loader", _LOADERS, ids=_IDS)
@pytest.mark.parametrize(
    "exc", _PROGRAMMING_ERROR_CASES, ids=_PROGRAMMING_ERROR_IDS
)
def test_auto_does_not_degrade_past_a_programming_error(
    loader: _Loader, exc: BaseException, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure that motivated the ladder, at the level where it hurt.

    Reclassified into a candidate-unavailable signal, a code defect made
    ``auto`` skip the engine and translate the document with the next one. The
    output changed, the compile reported success, and the defect left no trace.
    """
    _install(loader, exc, monkeypatch)
    with pytest.raises(type(exc)):
        loader.drive(loader.auto())


def test_the_ladder_covers_exactly_the_declared_programming_errors() -> None:
    """The cases above are the whole of ``PROGRAMMING_ERRORS`` — so adding a
    type there without extending this file fails here rather than leaving the
    new type untested at these three boundaries."""
    assert tuple(type(e) for e in _PROGRAMMING_ERROR_CASES) == PROGRAMMING_ERRORS


# ---------------------------------------------------------------------------
# What the wide catch IS for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("loader", _LOADERS, ids=_IDS)
def test_an_environment_failure_is_still_reclassified(
    loader: _Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half: tightening the ladder must not turn a missing download
    into a crash. A real environment failure keeps its documented translation
    into a candidate-unavailable signal."""
    _install(loader, loader.env_error, monkeypatch)
    with pytest.raises(loader.reclassified):
        loader.registry.get(loader.name)


@pytest.mark.parametrize("loader", _LOADERS, ids=_IDS)
def test_auto_still_degrades_on_an_environment_failure(
    loader: _Loader, monkeypatch: pytest.MonkeyPatch
) -> None:
    """And the chain still walks past it to the dependency-free engine, which
    is the behaviour the wide catch was added for in the first place."""
    _install(loader, loader.env_error, monkeypatch)
    out = loader.drive(loader.auto())
    assert loader.degraded(out), (
        f"{loader.name} did not degrade to its fallback engine on "
        f"{loader.env_error!r}"
    )
