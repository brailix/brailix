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
    # bars — but is reset at part / staff boundaries (``_emit_part``): a
    # hairpin never spans a part, so a dangling crescendo can't pair with
    # the next part's stray ``<wedge type="stop"/>``.
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
    # this root. Reset to None at every bar line (``_emit_measure``) —
    # chord spans don't cross bar lines in well-formed MusicXML, so a
    # measure that opens with an orphan ``<chord/>`` warns instead of
    # silently measuring against the previous measure's stale root.
    chord_root: tuple[str, int] | None = None
    # BANA Par. 9.2 (Direction of Intervals): the clef sign / line in
    # effect, set by ``_emit_clef`` and threaded so chord emission picks
    # the *written* note correctly. Treble (G) and alto (C on line 3)
    # write the uppermost chord note with intervals read downward; bass
    # (F) and tenor (C on line 4) write the lowermost with intervals read
    # upward. ``None`` (no clef yet / percussion) keeps the lowermost as
    # written — the pre-9.2 default. Persists until the next ``<clef>``.
    current_clef_sign: str | None = None
    current_clef_line: int | None = None
    # BANA Par. 2.4 (Larger and Smaller Value Signs): the value category
    # of the previously-emitted note / rest — ``"large"`` (8th-and-larger),
    # ``"small"`` (16th-and-smaller), ``"v256"`` (256th), or ``None`` at
    # the start of a reading. A value sign is emitted before a note/rest
    # whose category differs (the eighth/half-note shapes each stand for
    # two values, so a change between large and small needs marking; a
    # 256th always carries its own sign). Reset to ``None`` at part /
    # staff / voice boundaries (each starts a fresh reading); persists
    # across bar lines like the printed value sign itself.
    prev_value_category: str | None = None

    def warn(
        self,
        *,
        code: str,
        message: str,
        surface: str | None = None,
        span: Span | None = None,
        candidates: tuple[str, ...] = (),
        source: str | None = "backend.music",
    ) -> None:
        """Emit a WARN-level diagnostic stamped with the score location.

        Wraps ``backend.warnings.warn`` and fills :attr:`Warning.anchor`
        from :attr:`current_part_id` / :attr:`current_measure_number` —
        the same provenance every ``BrailleCell.source_text`` carries —
        so a frontend can navigate from the warning to its measure.
        Handlers must warn through this method, not the collector
        directly; a bare collector call silently loses the location
        (normalized MusicXML has no text offsets, so ``span`` can't
        recover it).  Outside a part / measure both ids are ``None``
        and the anchor is omitted, which downstream reads as "score
        level, no narrower location".
        """
        anchor: dict[str, str] = {}
        if self.current_part_id is not None:
            anchor["part_id"] = self.current_part_id
        if self.current_measure_number is not None:
            anchor["measure_number"] = self.current_measure_number
        self.backend.warnings.warn(
            code=code,
            message=message,
            surface=surface,
            span=span,
            candidates=candidates,
            source=source,
            anchor=anchor or None,
        )
