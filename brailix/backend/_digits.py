"""Shared digit-run → braille-cell emission for the number and math backends.

Both backends turn a run of digit characters into cells with the same
loop: an optional leading number sign, then per-char mapping of ``.`` /
``,`` / digits through the profile's ``decimal_point`` / ``thousands_sep``
/ ``digits`` tables.

They differ in role labels, warning provenance, source-span granularity,
*when* the number sign fires — and, deliberately, in how non-ASCII
decimal digits (full-width ``２``, Arabic-Indic, ...) are treated, all
passed in by the caller:

* **prose numbers fold** (``fold_nonascii=True``) — full-width digits are
  routine typography in CJK running text, so ``２`` reads as ``2``;
* **math numbers do not** (``fold_nonascii=False``) — a full-width digit
  inside a formula is a writing error in the source document. Folding it
  would silently translate a formula the author needs to fix, so it warns
  and emits a blank unknown cell instead (domain-expert rule; the same
  policy already applies to full-width letters and operators).
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


@dataclass(frozen=True, slots=True)
class DigitRunPolicy:
    """Per-caller policy for a digit run.

    The parts that stay constant for a given caller but differ between
    the prose-number and math backends: the role labels, the non-ASCII
    decimal digit rule (prose folds ``２`` -> ``2``, math warns — see the
    module docstring), and the warning provenance / codes.
    """

    roles: DigitRoles
    fold_nonascii: bool
    warn_source: str
    unknown_code: str
    missing_code: str


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
    policy: DigitRunPolicy,
    want_number_sign: bool,
    span_at: Callable[[int], Span | None],
    number_sign_span: Span | None = None,
) -> None:
    """Append the braille cells for ``digits`` onto ``cells``.

    ``want_number_sign`` is the caller's already-feature-gated decision
    (the number backend gates on ``number_sign``, the math backend on
    ``math.number_sign`` *and* its per-run latch); the leading sign still
    only fires when the profile actually defines one. ``policy`` carries
    the per-caller constants — role labels, the non-ASCII decimal digit
    rule (``policy.fold_nonascii``: prose folds, math warns — see the
    module docstring), and the warning provenance / codes. ``span_at(i)``
    gives the source span for the i-th character's cell — per-char for
    prose numbers, a constant inline span for math. Unknown / unmapped
    chars get a warning (under ``policy.unknown_code`` /
    ``policy.missing_code`` + ``policy.warn_source``) plus a blank
    ``unknown`` cell so proofreaders see the gap.
    """
    if not digits:
        return
    # A digit run must begin with a digit or a decimal point — the number
    # sign is meaningless before anything else (a leading thousands separator,
    # or a stray non-digit a malformed <mn> fed in). Prose guarantees this
    # upstream (segment.py); the math <mn> path does not, so enforce it here
    # rather than emit a dangling number sign with no digit behind it. A
    # leading fullwidth digit is "clean" only when fold_nonascii is on (prose
    # folds; math treats it as the writing error it is and warns).
    first = digits[0]
    starts_clean = (
        first == "."
        or first in profile.digits
        or (policy.fold_nonascii and ascii_decimal_digit(first) in profile.digits)
    )
    if want_number_sign and profile.number_sign:
        if starts_clean:
            cells.append(
                BrailleCell(
                    dots=profile.number_sign,
                    role=policy.roles.number_sign,
                    source_span=number_sign_span,
                )
            )
        else:
            warnings.warn(
                code=policy.missing_code,
                message=(
                    f"digit run {digits!r} does not start with a digit or "
                    f"decimal point; number sign suppressed"
                ),
                surface=digits,
                span=span_at(0),
                source=policy.warn_source,
            )
    for i, ch in enumerate(digits):
        sp = span_at(i)
        if ch == ".":
            dots, role = profile.decimal_point, policy.roles.decimal_point
        elif ch == ",":
            dots, role = profile.thousands_sep, policy.roles.thousands_sep
        else:
            key: str | None = ch
            if ch not in profile.digits:
                key = ascii_decimal_digit(ch) if policy.fold_nonascii else None
            if key is None or key not in profile.digits:
                warnings.warn(
                    code=policy.unknown_code,
                    message=f"no braille mapping for digit-run char {ch!r}",
                    surface=ch,
                    span=sp,
                    source=policy.warn_source,
                )
                cells.append(
                    BrailleCell(dots=(), role="unknown", source_span=sp, source_text=ch)
                )
                continue
            dots, role = profile.digits[key], policy.roles.digit
        # Profile may legitimately lack a decimal_point / thousands_sep
        # mapping (empty tuple): warn rather than emit a meaningless dots=().
        if not dots:
            warnings.warn(
                code=policy.missing_code,
                message=f"profile has no cells for {role!r} (char {ch!r})",
                surface=ch,
                span=sp,
                source=policy.warn_source,
            )
            cells.append(
                BrailleCell(dots=(), role="unknown", source_span=sp, source_text=ch)
            )
            continue
        cells.append(
            BrailleCell(dots=dots, role=role, source_span=sp, source_text=ch)
        )
