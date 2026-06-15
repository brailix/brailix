"""Basic / Current Chinese Braille tone strategy.

Legacy flag-driven behavior — what ``cn_current`` and every
pre-NCB profile uses.  Emit a tone cell whenever:

* the syllable carries a non-empty tone, AND
* it's not the suppressed neutral (``"5"``) tone — controlled by
  ``features.zh.tone_omit_neutral`` (defaults True), AND
* the master switch ``features.zh.tone`` is on (defaults True).

The :func:`BasicTonePolicy.should_emit_tone` body re-reads the two
flags every call so test code that monkeypatches
``profile.features["tone"] = False`` is honoured — caching the
booleans at policy build time would silently skip those changes.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.backend.zh.pinyin_parser import ParsedPinyin
from brailix.backend.zh.tone import register
from brailix.core.config import BrailleProfile


@dataclass(frozen=True, slots=True)
class BasicTonePolicy:
    """Flag-driven tone-emit decision.  Holds a profile reference and
    re-reads the relevant features on every call."""

    profile: BrailleProfile

    def should_emit_tone(
        self,
        *,
        syllable: str,
        parsed: ParsedPinyin,
        next_syllable: str | None = None,
        next_parsed: ParsedPinyin | None = None,
    ) -> bool:
        del syllable, next_syllable, next_parsed  # pure flag logic
        # Read the master switch via the legacy flat ``"tone"`` key, not
        # the dotted ``"zh.tone"``: ``feature()`` tries the literal key
        # first, so a test that sets a top-level
        # ``profile.features["tone"] = False`` is honoured. Normally there
        # is no top-level ``tone`` and the lookup falls through the
        # ``tone → zh.tone`` alias to the nested JSON value (the two agree);
        # ``feature("zh.tone")`` would resolve that nested value first and
        # never see the top-level test override.
        if not self.profile.feature("tone", True):
            return False
        if not parsed.tone:
            return False
        if parsed.tone == "5" and self.profile.feature("tone_omit_neutral", True):
            return False
        return True


# Builder is just the constructor — basic policy carries no extra
# precondition checks (any profile is valid input).
register("basic", lambda profile: BasicTonePolicy(profile=profile))


__all__ = ("BasicTonePolicy",)
