"""Shared digit-run → braille-cell emission for the number and math backends.

Both backends turn a run of digit characters into cells with the same
rules: an optional leading number sign, then per-char mapping of ``.`` /
``,`` / digits through the profile's ``decimal_point`` / ``thousands_sep``
/ ``digits`` tables, with non-ASCII decimal digits (full-width ``２``,
Arabic-Indic, ...) folded to their ASCII key.

They differ only in role labels, warning provenance, source-span
granularity, and *when* the number sign fires — all passed in. Keeping
the loop in one place means a digit-handling fix lands for both at once:
previously the full-width fallback existed only in the number backend, so
prose ``２`` rendered while ``<mn>２</mn>`` warned-and-dropped.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Callable
from dataclasses import dataclass

from brailix.core.config import BrailleProfile
from brailix.core.errors import WarningCollector
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell


@dataclass(frozen=True, slots=True)
class DigitRoles:
    """Role labels for the cells a digit run emits.

    Only ``digit`` differs between callers (``"digit"`` for prose numbers,
    ``"math_digit"`` for ``<mn>``); the rest are shared.
    """

    digit: str
    decimal_point: str = "decimal_point"
    thousands_sep: str = "thousands_sep"
    number_sign: str = "number_sign"


def ascii_decimal_digit(ch: str) -> str | None:
    """ASCII key for a Unicode *decimal* digit (``２`` → ``"2"``), or
    ``None`` when ``ch`` has no decimal value.

    Uses ``unicodedata.decimal`` (not ``.digit``) on purpose: superscripts
    like ``²`` have a digit value but no decimal value, and must stay
    unknown rather than render as a plain ``2``.
    """
    try:
        return str(unicodedata.decimal(ch))
    except (TypeError, ValueError):
        return None


def emit_digit_run(
    cells: list[BrailleCell],
    digits: str,
    *,
    profile: BrailleProfile,
    warnings: WarningCollector,
    roles: DigitRoles,
    want_number_sign: bool,
    span_at: Callable[[int], Span | None],
    warn_source: str,
    unknown_code: str,
    missing_code: str,
    number_sign_span: Span | None = None,
) -> None:
    """Append the braille cells for ``digits`` onto ``cells``.

    ``want_number_sign`` is the caller's already-feature-gated decision
    (the number backend gates on ``number_sign``, the math backend on
    ``math.number_sign`` *and* its per-run latch); the leading sign still
    only fires when the profile actually defines one. ``span_at(i)`` gives
    the source span for the i-th character's cell — per-char for prose
    numbers, a constant inline span for math. Unknown / unmapped chars get
    a warning (under ``unknown_code`` / ``missing_code`` + ``warn_source``)
    plus a blank ``unknown`` cell so proofreaders see the gap.
    """
    if not digits:
        return
    if want_number_sign and profile.number_sign:
        cells.append(
            BrailleCell(
                dots=profile.number_sign,
                role=roles.number_sign,
                source_span=number_sign_span,
            )
        )
    for i, ch in enumerate(digits):
        sp = span_at(i)
        if ch == ".":
            dots, role = profile.decimal_point, roles.decimal_point
        elif ch == ",":
            dots, role = profile.thousands_sep, roles.thousands_sep
        else:
            key = ch if ch in profile.digits else ascii_decimal_digit(ch)
            if key is None or key not in profile.digits:
                warnings.warn(
                    code=unknown_code,
                    message=f"no braille mapping for digit-run char {ch!r}",
                    surface=ch,
                    span=sp,
                    source=warn_source,
                )
                cells.append(
                    BrailleCell(dots=(), role="unknown", source_span=sp, source_text=ch)
                )
                continue
            dots, role = profile.digits[key], roles.digit
        # Profile may legitimately lack a decimal_point / thousands_sep
        # mapping (empty tuple): warn rather than emit a meaningless dots=().
        if not dots:
            warnings.warn(
                code=missing_code,
                message=f"profile has no cells for {role!r} (char {ch!r})",
                surface=ch,
                span=sp,
                source=warn_source,
            )
            cells.append(
                BrailleCell(dots=(), role="unknown", source_span=sp, source_text=ch)
            )
            continue
        cells.append(
            BrailleCell(dots=dots, role=role, source_span=sp, source_text=ch)
        )
