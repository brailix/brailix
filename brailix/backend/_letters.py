"""Shared letter-sign (case / script-class prefix) rule.

《盲文常用数学符号》二.（一）规则1: the case/script sign is written before
the letter; consecutive letters of the SAME class (small/capital Latin,
small/capital Greek) share one sign — only the first letter of the run
takes it; a class change starts a new sign. So ``Abc`` → ⠠⠁⠰⠃⠉ and
``πr`` → ⠨⠏⠰⠗, and the class change is what keeps mixed-case units
(``mW`` → ⠰⠍⠠⠺) lossless.

On top of that rule sits the whole-word-capitals convention: an
all-capital Latin run of two or more letters doubles the capital sign
(``ABC`` → ⠠⠠⠁⠃⠉), after which the letters are written bare. A single
⠠ then unambiguously means "only the first letter is capital".

Consumers: the math identifier emitter
(``backend.math.handlers.leaves._emit_letter_runs``), the quantity-unit
emitter (``backend.number.translate_quantity``), and the Latin text
backend's whole-word-capitals branch (``backend.latin``).
"""

from __future__ import annotations

from collections.abc import Iterator

from brailix.core.config import BrailleProfile


def iter_letter_runs(
    text: str, profile: BrailleProfile
) -> Iterator[tuple[str | None, str]]:
    """Partition ``text`` into maximal same-class letter runs.

    Yields ``(letter_class, run)`` chunks in source order, where
    ``letter_class`` is the profile's ``letter_prefix.*`` bucket key.
    Characters not in any letter table yield ``(None, ch)`` one at a
    time so callers can route them to their own fallback.
    """
    i = 0
    n = len(text)
    while i < n:
        cls = profile.letter_class(text[i])
        if cls is None:
            yield None, text[i]
            i += 1
            continue
        j = i + 1
        while j < n and profile.letter_class(text[j]) == cls:
            j += 1
        yield cls, text[i:j]
        i = j


def letter_sign_repeats(cls: str, run_len: int) -> int:
    """How many times the run's letter sign is written: twice for an
    all-capital Latin run of two or more letters (whole-word capitals,
    ⠠⠠), once otherwise."""
    return 2 if cls == "latin_upper" and run_len >= 2 else 1


__all__ = ("iter_letter_runs", "letter_sign_repeats")
