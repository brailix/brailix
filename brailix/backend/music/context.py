"""Mutable per-score state for the music backend.

The translator threads one :class:`MusicBrailleContext` instance
through a single :class:`~brailix.ir.inline.MusicInline` translation.
The entry point in :mod:`brailix.backend.music` constructs a fresh
context per node so state never leaks across scores.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from brailix.core.config import BrailleProfile
from brailix.core.context import BackendContext
from brailix.core.span import Span


@dataclass(slots=True)
class MusicBrailleContext:
    """Mutable per-score state for the music backend.

    Fields:

    * ``profile`` / ``backend`` — passed through to handlers so they
      can look up cells and emit warnings.
    * ``span`` — the source span of the current score root or a
      narrower span pushed down by data-bk-span (M3+).
    * ``prev_pitch`` — the previous note's ``(step, octave)`` tuple,
      used by BANA Par. 3.2.2 octave inference. ``None`` means "first
      note of line" — the next octave prefix is always emitted.
    * ``octave_rule`` — strategy override. ``"interval16"`` is the
      BANA default (skip-based inference); ``"every_measure"`` resets
      ``prev_pitch`` at every measure boundary; ``"always"`` resets
      before every note so an octave prefix is always emitted (useful
      for teaching / strict facsimile).
    """

    profile: BrailleProfile
    backend: BackendContext
    span: Span | None = None
    prev_pitch: tuple[str, int] | None = None
    octave_rule: Literal["interval16", "every_measure", "always"] = "interval16"
    # BANA Par. 6.2: tracks ``(pitch, accidental_entity)`` already emitted
    # within the current measure so duplicate accidentals on the same
    # (step, octave) line don't re-print when
    # ``features.music.accidental_persist_in_measure`` is true (default).
    # Reset at every measure boundary by ``_emit_measure``.
    measure_accidentals: set[tuple[tuple[str, int], str]] = field(
        default_factory=set
    )
    # M3.4 (BANA Table 22): which kind of hairpin is currently active
    # ("crescendo" / "diminuendo" / None). ``<wedge type="stop"/>``
    # consults this to pick the matching terminator cell, then clears
    # it. Persists across measures — hairpins routinely span multiple
    # bars in real scores.
    pending_hairpin: str | None = None
    # M8 (proofread provenance): current ``<part id="...">`` and
    # ``<measure number="...">`` values, threaded down so every
    # ``BrailleCell.source_text`` carries enough context for a
    # proofread tool to highlight which measure / part a cell came
    # from. ``None`` outside the corresponding container. M8 doesn't
    # try to reconstruct absolute MusicXML offsets — only the human-
    # readable measure / part labels exporters already emit.
    current_part_id: str | None = None
    current_measure_number: str | None = None
    # S6 (BANA Par. 9.1): pitch of the most recently emitted root
    # note (a ``<note>`` *without* a ``<chord/>`` child). Subsequent
    # ``<note><chord/>`` siblings emit interval cells measured from
    # this root. Cleared (left to last root) across measures — chord
    # spans don't cross bar lines in well-formed MusicXML.
    chord_root: tuple[str, int] | None = None
