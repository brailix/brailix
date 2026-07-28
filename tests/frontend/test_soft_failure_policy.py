"""One soft-failure policy, three verticals — pinned in a single place.

Math, music and graphics each expose one public parse entry
(:func:`~brailix.frontend.math.parse_math_tree`,
:func:`~brailix.frontend.music.parse_music_tree`,
:func:`~brailix.frontend.graphics.parse_graphic_tree`) that wraps the adapter
call in a deliberately wide ``except``: a registered adapter's failure modes
are open, and one unreadable formula must not fail a whole document.

What each vertical *recovers to* differs on purpose — ``<merror>``,
``<music-error>``, an error-marked ``<svg>`` — and stays each subsystem's own
business. What must **not** differ is the policy deciding what may be recovered
from at all, and it had drifted: math and graphics swallowed ``AttributeError``
while music re-raised it, and all three swallowed
:class:`~brailix.core.errors.StrictModeError`, so an adapter's own diagnostic
under STRICT mode came back as a degraded tree instead of the failure the
caller had asked for.

The fix is one shared *contract test*, not one shared helper: the three
frontend subsystems must stay independently replaceable (ARCHITECTURE §7.1), so
welding their entry points onto a common code path would trade a real
architectural property for duplicate-line removal. A test may span them; the
production code may not.

Each vertical is exercised through a fake adapter installed in its real
registry, so the check is on the entry point's own ladder rather than on any
particular built-in adapter.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

import pytest

from brailix.core.context import GraphicsContext, MathContext, MusicContext
from brailix.core.errors import RunMode, StrictModeError, WarningCollector
from brailix.frontend.graphics import parse_graphic_tree
from brailix.frontend.graphics.registry import graphic_source_registry
from brailix.frontend.math import parse_math_tree
from brailix.frontend.math.registry import math_source_registry
from brailix.frontend.music import parse_music_tree
from brailix.frontend.music.registry import music_source_registry

_PROBE = "policy_probe"


class _FakeAdapter:
    """One adapter shape for all three verticals.

    The three protocols differ only in method name (``to_mathml`` /
    ``to_musicxml`` / ``to_svg``), so one class satisfying all three keeps the
    fault injection identical across verticals — the point of the exercise.
    ``behaviour`` is called with ``ctx`` and either raises or returns the
    vertical's source string.
    """

    source = _PROBE

    def __init__(self, behaviour: Callable[[Any], str]) -> None:
        self._behaviour = behaviour

    def to_mathml(self, formula: Any, ctx: Any = None) -> str:
        return self._behaviour(ctx)

    def to_musicxml(self, src: Any, ctx: Any = None) -> str:
        return self._behaviour(ctx)

    def to_svg(self, src: Any, ctx: Any = None) -> str:
        return self._behaviour(ctx)


@dataclass(frozen=True)
class _Vertical:
    """How to drive one vertical's public parse entry, and how to recognise
    its recovery product."""

    name: str
    registry: Any
    parse: Callable[[str, Any], ET.Element | None]
    context: Callable[[WarningCollector], Any]
    good_source: str
    is_recovery: Callable[[ET.Element], bool]


_VERTICALS = (
    _Vertical(
        name="math",
        registry=math_source_registry,
        parse=parse_math_tree,
        context=lambda w: MathContext(
            source=_PROBE, profile="cn_current", mode="display", warnings=w
        ),
        good_source="<math><mi>x</mi></math>",
        is_recovery=lambda t: t.find("merror") is not None,
    ),
    _Vertical(
        name="music",
        registry=music_source_registry,
        parse=parse_music_tree,
        context=lambda w: MusicContext(
            source=_PROBE, profile="cn_current", warnings=w
        ),
        good_source=(
            '<score-partwise version="4.0">'
            '<part-list><score-part id="P1"><part-name>V</part-name>'
            "</score-part></part-list>"
            '<part id="P1"><measure number="1">'
            "<note><rest/><duration>1</duration><type>quarter</type></note>"
            "</measure></part></score-partwise>"
        ),
        is_recovery=lambda t: t.find("music-error") is not None,
    ),
    _Vertical(
        name="graphics",
        registry=graphic_source_registry,
        parse=parse_graphic_tree,
        context=lambda w: GraphicsContext(source=_PROBE, warnings=w),
        good_source=(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
            'width="10mm" height="10mm"><circle cx="5" cy="5" r="4"/></svg>'
        ),
        is_recovery=lambda t: t.get("data-bk-error") is not None,
    ),
)

_IDS = [v.name for v in _VERTICALS]


def _run(
    vertical: _Vertical,
    behaviour: Callable[[Any], str],
    *,
    mode: RunMode = RunMode.NORMAL,
) -> tuple[ET.Element | None, WarningCollector]:
    warns = WarningCollector(mode=mode)
    with vertical.registry.overriding(_PROBE, lambda: _FakeAdapter(behaviour)):
        return vertical.parse("x", vertical.context(warns)), warns


# ---------------------------------------------------------------------------
# Exempt from the backstop: strict mode
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vertical", _VERTICALS, ids=_IDS)
def test_strict_mode_error_from_the_adapter_propagates(
    vertical: _Vertical,
) -> None:
    """An adapter reporting through ``ctx.warnings`` under STRICT mode raises
    :class:`StrictModeError` *inside* the adapter; the entry point must let it
    through with its original code.

    Swallowing it defeated STRICT mode entirely — the caller asked for "any
    diagnostic is a failure" and instead received a degraded tree, with the
    real code (say ``MTEF_UNSUPPORTED``) buried in a ``data-reason`` string and
    relabelled as a parse failure.
    """

    def warns_then_returns(ctx: Any) -> str:
        ctx.warnings.warn(code="ADAPTER_DIAGNOSTIC", message="from the adapter")
        return vertical.good_source

    with pytest.raises(StrictModeError) as excinfo:
        _run(vertical, warns_then_returns, mode=RunMode.STRICT)
    assert excinfo.value.warning.code == "ADAPTER_DIAGNOSTIC"


@pytest.mark.parametrize("vertical", _VERTICALS, ids=_IDS)
def test_normal_mode_records_the_same_diagnostic_and_keeps_the_tree(
    vertical: _Vertical,
) -> None:
    """The strict rung must not leak into NORMAL mode: the very same adapter
    warning is recorded and the successfully produced tree still comes back,
    un-degraded."""

    def warns_then_returns(ctx: Any) -> str:
        ctx.warnings.warn(code="ADAPTER_DIAGNOSTIC", message="from the adapter")
        return vertical.good_source

    tree, warns = _run(vertical, warns_then_returns)
    assert tree is not None
    assert not vertical.is_recovery(tree)
    assert [w.code for w in warns] == ["ADAPTER_DIAGNOSTIC"]


# ---------------------------------------------------------------------------
# Exempt from the backstop: programming errors
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vertical", _VERTICALS, ids=_IDS)
@pytest.mark.parametrize(
    "exc",
    [AttributeError("no such attribute"), NameError("nope"), AssertionError()],
    ids=["AttributeError", "NameError", "AssertionError"],
)
def test_programming_errors_propagate(
    vertical: _Vertical, exc: BaseException
) -> None:
    """A code defect is never a legitimate response to bad input, so it must
    stay a loud, locatable crash instead of being reported as unreadable
    content (:data:`brailix.core.errors.PROGRAMMING_ERRORS`)."""

    def boom(ctx: Any) -> str:
        raise exc

    with pytest.raises(type(exc)):
        _run(vertical, boom)


# ---------------------------------------------------------------------------
# What the backstop IS for
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("vertical", _VERTICALS, ids=_IDS)
@pytest.mark.parametrize(
    "exc",
    [ValueError("malformed"), TypeError("wrong shape"), KeyError("missing")],
    ids=["ValueError", "TypeError", "KeyError"],
)
def test_input_shaped_errors_soft_fail_to_the_domain_recovery(
    vertical: _Vertical, exc: BaseException
) -> None:
    """The types third-party parsers raise on malformed input are exactly what
    the wide ``except`` exists for: each vertical degrades to its own recovery
    product and the pipeline keeps running.

    ``TypeError`` / ``ValueError`` / ``KeyError`` stay OUT of
    ``PROGRAMMING_ERRORS`` for this reason — an open registry of third-party
    parsers raises them on bad content, where soft failure is the intended
    behaviour."""

    def boom(ctx: Any) -> str:
        raise exc

    tree, _warns = _run(vertical, boom)
    assert tree is not None, f"{vertical.name} lost its recovery tree"
    assert vertical.is_recovery(tree)
