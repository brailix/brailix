"""National Common Braille (GF0019-2018) tone strategy — tone omission grouped by initial.

Wraps :class:`brailix.core.config.zh_ncb_tables.NcbToneOmission`
(loaded by the profile loader from ``tables.zh.tone_omission``) and
runs the multi-step decision tree from the NCB study guide, Lessons 2–9:

1. ``tone==""`` → no emit.
2. ``tone=="5"`` (neutral tone) → never marked.
3. **Boundary rule** (Lesson 9): an initial-only syllable
   (``parsed.final == ""``, e.g. zhi/chi/shi/ri/zi/ci/si) followed
   by a zero-initial syllable (``next_parsed.initial == ""``) within the same
   word → **emit** (overrides per-initial / zero-initial omission).
4. **Per-initial rule** (Lesson 2–7, when ``parsed.initial`` is set):
   look up ``table.by_initial[initial]``:

   * if syllable in ``keep_syllables`` → emit
   * elif ``parsed.tone == omit_tone`` → don't emit
   * else → emit

5. **Zero-initial rule** (Lesson 8, when ``parsed.initial == ""``):
   look up ``table.zero_initial``:

   * if syllable in ``keep_syllables`` → emit
   * elif syllable in ``omit_syllables`` → don't emit
   * elif ``parsed.tone == default_omit_tone`` → don't emit
   * else → emit

The builder raises :class:`ConfigurationError` at registration-time
lookup if a profile selects ``ncb_omission`` without providing
``tables.zh.tone_omission`` — surfaces the misconfig at startup, not
at the first translated syllable (where it'd be a much noisier
silent-fall-through bug).
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.backend.zh.pinyin_parser import ParsedPinyin
from brailix.backend.zh.tone import register
from brailix.core.config import BrailleProfile
from brailix.core.config.zh_ncb_tables import NcbToneOmission
from brailix.core.errors import ConfigurationError


@dataclass(frozen=True, slots=True)
class NcbOmissionPolicy:
    """National Common Braille tone-omission decision wrapper (grouped by initial)."""

    table: NcbToneOmission

    def should_emit_tone(
        self,
        *,
        syllable: str,
        parsed: ParsedPinyin,
        next_syllable: str | None = None,
        next_parsed: ParsedPinyin | None = None,
    ) -> bool:
        del next_syllable  # consumed via next_parsed
        if not parsed.tone:
            return False
        if parsed.tone == "5":
            return False  # neutral tone is never marked

        # Boundary rule (Lesson 9): syllabic-i initial-only + next
        # zero-initial → keep tone, overriding everything else.
        if (
            self.table.boundary_rule_enabled
            and parsed.has_initial()
            and parsed.final == ""
            and next_parsed is not None
            and not next_parsed.has_initial()
        ):
            return True

        syl_norm = _normalize_syllable_key(syllable)

        if parsed.has_initial():
            rule = self.table.by_initial.get(parsed.initial)
            if rule is None:
                return True  # unknown initial → conservative: emit
            keep = rule.get("keep_syllables", ())
            if syl_norm in keep:
                return True
            return parsed.tone != rule.get("omit_tone")

        # zero-initial branch
        keep = self.table.zero_initial.get("keep_syllables", ())
        if syl_norm in keep:
            return True
        omit = self.table.zero_initial.get("omit_syllables", ())
        if syl_norm in omit:
            return False
        return parsed.tone != self.table.zero_initial.get("default_omit_tone")


def _normalize_syllable_key(syllable: str) -> str:
    """Canonicalize a syllable string for lookup in keep/omit lists.

    Matches the first-line normalization in
    :func:`brailix.backend.zh.pinyin_parser.parse_pinyin` so the lookup
    tables can use the same orthographic form the guide uses
    (``wo3``, ``tou1``, ``zi4``).
    """
    return syllable.strip().lower().replace("v", "ü")


def _build(profile: BrailleProfile) -> NcbOmissionPolicy:
    """Constructor that checks the precondition.

    Profiles selecting ``ncb_omission`` must also declare the
    ``tone_omission`` sub-section inside ``tables.zh.exceptions`` —
    without it ``profile.zh_exceptions.tone_omission`` is ``None``
    and this strategy has no table to consult.  Loud failure here
    beats silent wrong behavior on the first syllable.
    """
    exc = profile.zh_exceptions
    if exc is None or exc.tone_omission is None:
        raise ConfigurationError(
            f"profile {profile.name!r}: tone_strategy='ncb_omission' "
            f"requires tables.zh.exceptions to declare a tone_omission section"
        )
    return NcbOmissionPolicy(table=exc.tone_omission)


register("ncb_omission", _build)


__all__ = ("NcbOmissionPolicy",)
