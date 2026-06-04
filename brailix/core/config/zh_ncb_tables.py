"""NCB (National Common Braille / GF0019-2018) profile data shape.

Single :class:`NcbExceptions` container with three sub-records:
``tone_omission`` (Lessons 2–9 per-initial tone-omission rules),
``char_overrides`` (Lessons 7 + 10 + 9§8 character-level overrides), and
``word_overrides`` (Lesson 9§8 word-level tone retention).  All three are
loaded from one resource file —
``resources/cn/ncb/exceptions.json`` — following the design principle that
*all NCB-specific difference lives in one consolidated "exceptions"
resource* so the backend stays small and generic.

This module is in the config layer because the data shapes are
**profile data**, not backend behavior.  The lookup methods on these
classes are pure dict access — small enough to live on the dataclass.
The actual tone-emission decision algorithm lives in
:mod:`brailix.backend.zh.tone.ncb_omission` because it's behavior.

Layering rule: this module imports nothing from :mod:`brailix.backend`.
That's why :meth:`NcbCharOverrides.shorthand_cells_for` takes a plain
``next_is_zero_initial: bool`` instead of a ``ParsedPinyin`` —
backend computes the bool from its parser and passes it in.

JSON layout convention (since 2026-05-25): char/word entries are
**arrays of objects** with explicit ``surface`` fields and ASCII
``_id`` slugs, *not* dicts keyed by Chinese chars.  Editing, grep,
and diff stay keyboard-friendly.  Loader translates to
``dict[surface, ...]`` for the backend.  Same rationale as the
math-symbol entity-name convention in ``ARCHITECTURE.md``:
ASCII keys = ergonomic tooling.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Tone omission section (Lessons 2–9)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NcbToneOmission:
    """Tone-omission-grouped-by-initial sub-section of :class:`NcbExceptions`.

    Decision logic lives in
    :class:`brailix.backend.zh.tone.ncb_omission.NcbOmissionPolicy`
    — this dataclass is pure data.

    * ``by_initial[initial]`` — per-initial rule.  Each entry has
      ``omit_tone`` (tone digit string like ``"4"``), optional
      ``keep_syllables`` (list of pinyin syllables exempted from the
      omission, e.g. ``["zi4"]`` for the z group), and metadata keys
      (``_lesson`` / ``_note`` etc., ignored at runtime).
    * ``zero_initial`` — zero-initial (final forms a syllable on its own)
      rule. Carries ``default_omit_tone`` plus ``omit_syllables`` /
      ``keep_syllables`` lists for the two-way overrides Lesson 8 lays out
      (wǒ / yě / yǒu / yī / ér / o̅ omit; their tone-4 counterparts keep).
    * ``boundary_rule_enabled`` — Lesson 9 cross-syllable rule.
    """

    by_initial: dict[str, dict[str, Any]] = field(default_factory=dict)
    zero_initial: dict[str, Any] = field(default_factory=dict)
    boundary_rule_enabled: bool = True


# ---------------------------------------------------------------------------
# Character-level overrides section (Lessons 7 + 9§8 + 10)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _Shorthand:
    """Definite-word shorthand sub-record on a :class:`_CharOverride`.

    ``cells`` is the form used when no boundary exception fires.
    ``boundary_exception`` is True for the 5 chars (的/么/你/他/它)
    that lose the shorthand when immediately followed by a syllable
    whose final forms a syllable on its own; 她 is False (always
    shortens).  When the boundary fires:

    * ``boundary_spelling`` is non-None (e.g. 他=⠞⠔, 它=⠈⠞⠔ per the
      study guide's Lesson 7 special spelling) → emit those cells in
      place of the shorthand.
    * ``boundary_spelling`` is None (的/么/你) → caller falls through
      to the standard syllable path.
    """

    cells: tuple[tuple[int, ...], ...]
    boundary_exception: bool = False
    boundary_spelling: tuple[tuple[int, ...], ...] | None = None


@dataclass(frozen=True, slots=True)
class _CharOverride:
    """One entry in :class:`NcbCharOverrides`.

    Each instance describes 0+ behaviors for a single Chinese character:

    * ``shorthand`` (Lesson 10): a definite-word abbreviation; may
      carry boundary-exception data (Lesson 7 special spelling when
      ``boundary_spelling`` is set, fall-through otherwise).
    * ``keep_tone`` (Lesson 9 §8): forcibly keep this character's
      tone cell regardless of what the per-initial rule says.

    An entry without ``shorthand`` simply has no shorthand behavior
    (e.g. 再 / 问 — only ``keep_tone`` fires).
    """

    surface: str
    shorthand: _Shorthand | None = None
    keep_tone: bool = False


@dataclass(frozen=True, slots=True)
class NcbCharOverrides:
    """Character-level override sub-section of :class:`NcbExceptions`.

    Merges what used to be two separate concepts (word-shorthand and
    char-level disambiguation) into one char-keyed map.  Both kinds of
    override are "look up by surface character and apply N behaviors",
    so they share the same index.
    """

    by_char: dict[str, _CharOverride] = field(default_factory=dict)

    def shorthand_cells_for(
        self,
        ch: str,
        *,
        next_is_zero_initial: bool = False,
    ) -> tuple[tuple[int, ...], ...] | None:
        """Return the shorthand cell sequence for ``ch``, or ``None``.

        ``None`` means "no shorthand emitted, fall through to the
        standard syllable path" — the case for:

        * ``ch`` not in the table at all
        * ``ch`` has no ``shorthand`` sub-record (only ``keep_tone``)
        * boundary exception fires AND ``boundary_spelling`` is None
        """
        spec = self.by_char.get(ch)
        if spec is None or spec.shorthand is None:
            return None
        sh = spec.shorthand
        if sh.boundary_exception and next_is_zero_initial:
            return sh.boundary_spelling  # may itself be None → fall through
        return sh.cells

    def should_force_keep_tone(self, ch: str) -> bool:
        """``True`` iff ``ch`` carries an explicit ``keep_tone`` override."""
        spec = self.by_char.get(ch)
        return spec is not None and spec.keep_tone


# ---------------------------------------------------------------------------
# Word-level overrides section (Lesson 9 §8)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NcbWordOverrides:
    """Word-level tone-retention sub-section of :class:`NcbExceptions`.

    Applied when the IR carries the target as a single :class:`Word`
    node (i.e. the segmenter saw it as a unit).  If the segmenter
    split the word into multiple HanziChar nodes, this layer doesn't
    fire and the rule needs to be expressed via
    :class:`NcbCharOverrides` instead (or the segmenter granularity
    adjusted).
    """

    by_word: dict[str, tuple[bool, ...]] = field(default_factory=dict)

    def should_force_keep_tone(
        self,
        *,
        word_surface: str,
        char_index_in_word: int,
    ) -> bool:
        """``True`` iff ``word_surface`` is in the table AND its
        ``keep_tone_per_char[char_index_in_word]`` is True."""
        flags = self.by_word.get(word_surface)
        if flags is None:
            return False
        if 0 <= char_index_in_word < len(flags):
            return flags[char_index_in_word]
        return False


# ---------------------------------------------------------------------------
# Top-level container
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class NcbExceptions:
    """All NCB-specific data — loaded from one ``exceptions.json``.

    Lives on :attr:`BrailleProfile.zh_exceptions`.  Profiles that
    don't opt into NCB (cn_current) leave it at ``None``; the
    backend's NCB call sites check for ``None`` first and no-op.

    "Three sub-records in one container" follows the design principle:
    *all NCB-specific difference in one consolidated resource* so the
    backend doesn't sprout one new module per quirk and the profile
    doesn't sprout one new field per quirk either.
    """

    tone_omission: NcbToneOmission | None = None
    char_overrides: NcbCharOverrides | None = None
    word_overrides: NcbWordOverrides | None = None


__all__ = (
    "NcbCharOverrides",
    "NcbExceptions",
    "NcbToneOmission",
    "NcbWordOverrides",
    "_CharOverride",
    "_Shorthand",
)
