"""Math frontend subsystem — one subsystem entry point:
:func:`parse_math_tree`.

Source adapters (``latex`` / ``mathml`` / ``omml`` / ``mtef`` / ...) live in
``adapters/`` and are picked from an internal registry based on
:class:`~brailix.core.context.MathContext`. The MathML tree returned
by an adapter, after normalisation, is the math IR itself — there is no
separate IR-builder layer.

In-subsystem callers only need :func:`parse_math_tree`. Outside the
subsystem, import it from the :mod:`brailix.frontend` facade, which is where
it carries a compatibility promise — this module path is internal like every
path outside the facades and the extension surface (see the top-level
:mod:`brailix` docstring), and "entry point" here means "what the pipeline
calls into this subsystem through", not "published API".
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.context import MathContext
from brailix.core.errors import (
    PROGRAMMING_ERRORS,
    MissingExtraError,
    StrictModeError,
)
from brailix.frontend.math.normalizer import normalize
from brailix.frontend.math.utils import merror_wrap

if _TYPE_CHECKING:
    import xml.etree.ElementTree as ET


def parse_math_tree(
    formula: str | bytes, ctx: MathContext
) -> ET.Element | None:
    """Convert a single math fragment to a normalised :class:`ET.Element`
    tree.

    Steps: pick the source adapter from ``ctx.source`` → produce a
    MathML string → run the normalizer (strip namespace / collapse
    singleton ``<mrow>`` / trim whitespace) → return the resulting
    :class:`ET.Element` rooted at ``<math>``.

    Returns ``None`` (and records a ``MATH_ADAPTER_MISSING`` warning
    via ``ctx.warnings``) when the requested source adapter is absent
    or its optional dependency isn't installed; the pipeline keeps
    running.

    Soft-failure backstop: an adapter (or the normalizer) that raises
    anyway — the registry is open to third-party adapters — degrades to
    the standard ``<merror>`` tree instead of crashing the caller; the
    backend renders an unknown cell plus a ``MATH_ERROR`` warning.

    Two exception classes are exempt from that backstop, identically here and
    in the music / graphics entry points (the shared policy is pinned by
    ``tests/frontend/test_soft_failure_policy.py``):

    * :class:`~brailix.core.errors.StrictModeError` — the adapter's own
      ``ctx.warnings`` call raised it, carrying that diagnostic's real code.
      Wrapping it into ``<merror>`` would both defeat STRICT mode (the caller
      asked to fail on any diagnostic and would instead receive a tree) and
      relabel the real code as a parse failure.
    * :data:`~brailix.core.errors.PROGRAMMING_ERRORS` — a code defect is never
      a legitimate response to a formula, so it surfaces loudly instead of
      being disguised as unreadable input.
    """
    from brailix.frontend.math.registry import math_source_registry

    try:
        adapter = math_source_registry.get(ctx.source)
    except MissingExtraError as e:
        ctx.warnings.warn(
            code="MATH_ADAPTER_MISSING",
            message=str(e),
            source="frontend.math",
        )
        return None
    except KeyError as e:
        ctx.warnings.warn(
            code="MATH_ADAPTER_MISSING",
            message=str(e),
            surface=formula if isinstance(formula, str) else None,
            candidates=tuple(math_source_registry.names()),
            source="frontend.math",
        )
        return None

    try:
        mathml = adapter.to_mathml(formula, ctx)
        return normalize(mathml)
    except StrictModeError:
        # The adapter reported a diagnostic and STRICT mode promoted it. It
        # already carries the real code; degrading it to <merror> would hide
        # the failure the caller explicitly asked to be raised.
        raise
    except PROGRAMMING_ERRORS:
        # AttributeError / NameError / AssertionError = our (or the adapter's)
        # bug, not a bad formula. See brailix.core.errors.
        raise
    except Exception as e:  # noqa: BLE001 — pipeline must never crash
        # Adapters promise soft failure (<merror> + warning) and the
        # normalizer promises never to raise, but the registry is
        # deliberately open and our own adapters have slipped before
        # (a lone surrogate from a corrupt MTEF stream blew up the
        # UTF-8 re-encode inside ET parsing).  Degrade to the standard
        # <merror> tree — the backend renders an unknown cell with a
        # MATH_ERROR warning and translation continues.
        surface = formula if isinstance(formula, str) else repr(formula)
        try:
            return normalize(
                merror_wrap(surface[:200], reason=f"adapter failure: {e!r}")
            )
        # The recovery runs the SAME ladder as the parse above. A bare
        # ``except Exception`` here would have re-opened, one level down,
        # exactly the two holes the ladder closes: a STRICT-mode diagnostic
        # raised while building the recovery would come back relabelled
        # ``MATH_ERROR`` instead of carrying its own code, and a defect in
        # ``merror_wrap`` / the normalizer would be filed as "this formula is
        # unreadable" — the one report guaranteed never to be investigated.
        except StrictModeError:
            raise
        except PROGRAMMING_ERRORS:
            raise
        except Exception:  # pragma: no cover — double fault
            ctx.warnings.warn(
                code="MATH_ERROR",
                message=f"math adapter failure: {e!r}",
                source="frontend.math",
            )
            return None


__all__ = ("parse_math_tree",)
