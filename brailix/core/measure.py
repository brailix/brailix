"""One answer to "is this a usable physical measurement?", for the layers
that both have to ask it.

A tactile page's resolution and millimetre size are validated in two places:
:class:`~brailix.ir.tactile.TactileRaster`, built in code and checked at
construction, and :class:`~brailix.backend.tactile.profile.TactileProfile`,
read out of a JSON file. Both ask exactly the same question of a value before
storing it — reject ``bool``, convert with ``float()``, reject non-finite,
reject ``<= 0``, keep the converted number — and each carried its own copy of
those five steps. The copies had not drifted; what made the duplication worth
removing is that each was wrapped in a long comment explaining *why* each step
is there, which is precisely the shape a future fix lands on one of and not the
other.

Neither layer can host it for the other: :mod:`brailix.ir` carries core
primitives alone (ARCHITECTURE#arch-layers), and a configuration validator is
not an IR concern in the other direction either. So the fact lives here — the
same reasoning that put :mod:`brailix.core.chars` where the frontend and the
backend can both read it without importing each other.

What deliberately does **not** move here is the *diagnosis*. A raster is built
in code, so a bad value is a caller's bug and stays a ``ValueError`` beside the
dimension checks next to it; a profile is a JSON file, so the same bad value is
a :class:`~brailix.core.errors.ConfigurationError` naming the offending file.
The caller passes in the exception type and the words that name what is being
measured; only the arithmetic is shared.
"""

from __future__ import annotations

import math as _math
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


def as_positive_finite(
    value: Any,
    what: str,
    *,
    error: Callable[[str], Exception] = ValueError,
) -> float:
    """``value`` as a ``float``, or ``error(...)`` if it is not finite and > 0.

    ``NaN`` / ``±inf`` are the reason this is not a bare ``<= 0`` test: both are
    ordinary floats that pass every comparison such a guard makes, and both fail
    much later and much further away — ``round(nan)`` raises inside the BMP
    encoder, ``inf`` millimetres reach a PDF ``MediaBox`` as the literal text
    ``inf``, which no reader accepts. JSON can carry either one (Python's
    decoder accepts the ``NaN`` / ``Infinity`` literals by default), so this is
    not a code-only hazard.

    ``bool`` is refused first because it is an ``int`` subclass: ``True`` would
    otherwise resolve to a 1-dpi page, silently.

    The converted number is **returned so the caller can store it**. Anything
    ``float()`` accepts is a legitimate way to spell a measurement — which is
    what lets a loader take a quoted ``"100"`` out of a hand-edited JSON file —
    so validating without keeping the result left a value that merely *converts*
    living on in a field declared ``float``, to raise ``TypeError`` in the first
    arithmetic that touched it.

    ``what`` names the thing being measured and opens every message, so the
    caller decides whether a reader sees ``dpi`` or ``<file>: tactile profile
    field 'dpi'``; ``error`` builds the exception from that message.
    """
    if isinstance(value, bool):  # an int subclass: True would mean 1 dpi
        raise error(f"{what} must be a number, got {value!r}")
    try:
        num = float(value)
    except (TypeError, ValueError):
        raise error(f"{what} must be a number, got {value!r}") from None
    if not _math.isfinite(num):
        raise error(f"{what} must be a finite number, got {num}")
    if num <= 0:
        raise error(f"{what} must be > 0, got {num}")
    return num
