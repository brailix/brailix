"""Translate Chinese inline IR (Word / HanziChar) into braille cells.

For each token with a pinyin annotation:

1. Split the multi-syllable pinyin string (``"chong2 qing4"``) on
   whitespace into per-character syllables.
2. For each syllable, parse it with
   :func:`~brailix.backend.zh.pinyin_parser.parse_pinyin` into
   (initial, final, tone).
3. Emit cells in this order:

   * **initial cell** (if non-empty),
   * **final cell**,
   * **tone cell** (if the ``tone`` feature is enabled and tone
     is not the neutral one suppressed by ``tone_omit_neutral``).

Missing pinyin → :code:`MISSING_PINYIN` warning + one unknown cell
per character. Missing initial / final mapping → :code:`MISSING_FINAL`
warning + unknown cell. The pipeline never crashes on data gaps.
"""

from __future__ import annotations

from brailix.backend.zh.pinyin_parser import ParsedPinyin, parse_pinyin
from brailix.backend.zh.tone import tone_policy_for
from brailix.core.config import BrailleProfile
from brailix.core.config.zh_ncb_tables import NcbCharOverrides
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.braille import BrailleCell
from brailix.ir.inline import HanziChar, HanziMarker, Word

# The year marker 年 writes directly against its year digits with no
# number→marker connector (NCB convention); every other date marker
# (月/日/号/时/分/秒, …) takes the connector the way 10页 / 3个 do. This
# rule lives here, not in the language-neutral number backend.
_YEAR_MARKER = "年"

# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def translate_word(
    node: Word, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    return _translate_chinese(
        surface=node.surface,
        pinyin=node.reading,
        span=node.span,
        ctx=ctx,
        profile=profile,
    )


def translate_hanzi_char(
    node: HanziChar, ctx: BackendContext, profile: BrailleProfile
) -> list[BrailleCell]:
    return _translate_chinese(
        surface=node.surface,
        pinyin=node.reading,
        span=node.span,
        ctx=ctx,
        profile=profile,
    )


def translate_date_marker(
    marker: HanziMarker,
    follows_number: bool,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    """Chinese date marker → cells.

    Owns the two Chinese-specific pieces the language-neutral
    :func:`brailix.backend.number.translate_date` skeleton delegates here:
    the number→marker **connector rule** (a connector ⠤ precedes a marker
    that directly follows a Number, except the year marker 年 — NCB
    convention) and the marker's **syllable reading** (via
    :func:`translate_hanzi_char`, so a missing reading still degrades to a
    MISSING_PINYIN warning + unknown cell, never a crash).
    """
    out: list[BrailleCell] = []
    if follows_number and marker.surface != _YEAR_MARKER:
        boundary = (
            Span(marker.span.start, marker.span.start) if marker.span else None
        )
        out.append(
            BrailleCell(
                dots=profile.connector,
                role="connector",
                source_span=boundary,
                source_text="",
            )
        )
    out.extend(
        translate_hanzi_char(
            HanziChar(
                surface=marker.surface,
                span=marker.span,
                reading=marker.reading,
            ),
            ctx,
            profile,
        )
    )
    return out


# ---------------------------------------------------------------------------
# Core
# ---------------------------------------------------------------------------


def _translate_chinese(
    *,
    surface: str,
    pinyin: str | None,
    span: Span | None,
    ctx: BackendContext,
    profile: BrailleProfile,
) -> list[BrailleCell]:
    if not surface:
        return []
    if not pinyin:
        # No pinyin annotation — emit one unknown cell per character
        # and warn so the proofreader can see what fell through.
        ctx.warnings.warn(
            code="MISSING_PINYIN",
            message=f"no pinyin for {surface!r}",
            surface=surface,
            span=span,
            source="backend.zh",
        )
        return [
            BrailleCell(
                dots=(),
                role="unknown",
                source_span=_char_span(span, i),
                source_text=ch,
            )
            for i, ch in enumerate(surface)
        ]

    syllables = pinyin.split()
    if len(syllables) != len(surface):
        # Sanity check: one syllable per character is the assumption
        # everywhere in the project.
        ctx.warnings.warn(
            code="PINYIN_LENGTH_MISMATCH",
            message=(
                f"{len(syllables)} syllables but {len(surface)} characters in "
                f"{surface!r} / {pinyin!r}"
            ),
            surface=surface,
            span=span,
            source="backend.zh",
        )
        # Fall back: render one unknown cell per character.
        return [
            BrailleCell(dots=(), role="unknown", source_span=_char_span(span, i), source_text=ch)
            for i, ch in enumerate(surface)
        ]

    # NCB definite-word shorthand — look up shorthand via the
    # unified zh_exceptions container.  None when the profile didn't opt in
    # (cn_current) or no char_overrides section was defined.
    exceptions = profile.zh_exceptions
    char_overrides = (
        exceptions.char_overrides if exceptions is not None else None
    )

    # Cross-IR-node lookahead: when this is the last syllable of the
    # current Word/HanziChar and the immediately adjacent sibling is
    # another Chinese node (no Space / Punct between them), peek at
    # its first syllable so the cross-syllable boundary rule fires across
    # IR-node boundaries too — not just within a single Word.
    # ``_translate_children`` in :mod:`brailix.backend.block` stashes
    # the next sibling under ``ctx.options['_next_inline_sibling']``
    # before each dispatch; ``None`` here means "no peek available".
    cross_node_next_syl = _peek_next_chinese_syllable(ctx)

    out: list[BrailleCell] = []
    # strict=True is the right contract here — the mismatch case is
    # already handled by the length check above; if we ever reach this
    # zip with different lengths, that's a bug we want to surface.
    for i, (ch, syl) in enumerate(zip(surface, syllables, strict=True)):
        char_span = _char_span(span, i)
        # Explicit ``str | None`` because the cross-node peek branch
        # can legitimately return None — and both downstream
        # consumers (``_try_shorthand`` / ``_syllable_cells``) declare
        # the parameter as ``str | None``.  Without this annotation
        # mypy narrows to ``str`` from the first branch and complains
        # about the second.
        next_syl: str | None
        if i + 1 < len(syllables):
            next_syl = syllables[i + 1]
        else:
            # At the last char of this surface: fall back to the
            # cross-IR-node peek so 慈/爱 split across HanziChar nodes
            # still triggers the boundary rule. (慈/爱 = cí/ài, example chars.)
            next_syl = cross_node_next_syl
        if char_overrides is not None:
            sh_cells = _try_shorthand(ch, next_syl, char_span, char_overrides)
            if sh_cells is not None:
                out.extend(sh_cells)
                continue
        out.extend(
            _syllable_cells(
                ch, syl, next_syl, char_span, ctx, profile,
                word_surface=surface,
                char_index_in_word=i,
            )
        )
    return out


def _peek_next_chinese_syllable(ctx: BackendContext) -> str | None:
    """Return the first syllable of the next adjacent Chinese sibling.

    Reads the hint set by ``brailix.backend.block._translate_children``
    before each dispatch. Returns ``None`` when:

    * no sibling exists (last child),
    * the sibling is not a Chinese node (Punct / Space / Number / ...),
    * the sibling has no pinyin (HanziChar / Word default), or
    * the pinyin string is empty after splitting.
    """
    sibling = ctx.options.get("_next_inline_sibling")
    if sibling is None:
        return None
    pinyin = getattr(sibling, "reading", None)
    if not isinstance(pinyin, str) or not pinyin:
        return None
    syllables = pinyin.split()
    return syllables[0] if syllables else None


def _try_shorthand(
    ch: str,
    next_syllable: str | None,
    span: Span | None,
    char_overrides: NcbCharOverrides,
) -> list[BrailleCell] | None:
    """Look up ``ch`` in the char_overrides shorthand table; return cells or ``None``.

    ``None`` means "no shorthand emitted, fall through to the
    standard syllable path" — the case for non-shorthand characters
    AND for boundary-exception fall-throughs (的/么/你 — de/me/nǐ — followed by a
    zero-initial syllable).  When ``None`` is returned,
    ``_translate_chinese`` runs the regular initial+final+tone
    emission for ``ch``.
    """
    # The shorthand table only needs one bit out of the next syllable
    # ("is the next initial empty?") to decide the boundary exception.
    # We parse here and pass the bool — keeps the data-layer
    # :class:`NcbCharOverrides` free of backend imports
    # (``ParsedPinyin`` lives in the backend's pinyin_parser).
    next_is_zero_initial = False
    if next_syllable is not None:
        try:
            next_parsed = parse_pinyin(next_syllable)
        except ValueError:
            next_parsed = None
        if next_parsed is not None:
            next_is_zero_initial = not next_parsed.has_initial()
    cell_seq = char_overrides.shorthand_cells_for(
        ch, next_is_zero_initial=next_is_zero_initial
    )
    if cell_seq is None:
        return None
    return [
        BrailleCell(
            dots=dots,
            role="zh_shorthand",
            source_span=span,
            source_text=ch,
        )
        for dots in cell_seq
    ]


def _syllable_cells(
    ch: str,
    syllable: str,
    next_syllable: str | None,
    span: Span | None,
    ctx: BackendContext,
    profile: BrailleProfile,
    *,
    word_surface: str = "",
    char_index_in_word: int = 0,
) -> list[BrailleCell]:
    try:
        parsed = parse_pinyin(syllable)
    except ValueError:
        ctx.warnings.warn(
            code="BAD_PINYIN",
            message=f"could not parse pinyin syllable {syllable!r} (for {ch!r})",
            surface=ch,
            span=span,
            source="backend.zh",
        )
        return [BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)]
    next_parsed: ParsedPinyin | None = None
    if next_syllable is not None:
        try:
            next_parsed = parse_pinyin(next_syllable)
        except ValueError:
            next_parsed = None  # peek failure is non-fatal — boundary rule just won't fire
    return _emit_parsed(
        ch, syllable, parsed, next_syllable, next_parsed, span, ctx, profile,
        word_surface=word_surface,
        char_index_in_word=char_index_in_word,
    )


def _emit_parsed(
    ch: str,
    syllable: str,
    parsed: ParsedPinyin,
    next_syllable: str | None,
    next_parsed: ParsedPinyin | None,
    span: Span | None,
    ctx: BackendContext,
    profile: BrailleProfile,
    *,
    word_surface: str = "",
    char_index_in_word: int = 0,
) -> list[BrailleCell]:
    cells: list[BrailleCell] = []

    if parsed.has_initial():
        dots = profile.initials.get(parsed.initial)
        if dots is None:
            ctx.warnings.warn(
                code="MISSING_INITIAL",
                message=f"no braille cell for initial {parsed.initial!r}",
                surface=ch,
                span=span,
                source="backend.zh",
            )
            cells.append(
                BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)
            )
        else:
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="zh_initial",
                    source_span=span,
                    source_text=ch,
                )
            )

    if parsed.final == "":
        if parsed.syllabic:
            # Syllabic-i pattern (zhi/chi/shi/ri/zi/ci/si): the parser
            # deliberately dropped the cosmetic ``i`` because there's no
            # vowel to spell. Emit nothing here — only initial + tone.
            pass
        else:
            # An empty final that is NOT a syllabic-i: the syllable
            # stripped down to a bare initial. This is a degenerate
            # reading such as the syllabic nasal 呣 ``m`` that has no
            # conventional braille syllable (嗯 ``n`` and 哼 ``hng`` are
            # aliased to en / heng in the parser), or a bare retroflex
            # ``r`` (a degenerate erhua reading like ``r5``) — an initial
            # with no spellable rime. Warn so the dropped rime is visible
            # to the proofreader instead of silently vanishing; add a
            # placeholder cell only when no initial cell is already
            # standing in for the syllable.
            ctx.warnings.warn(
                code="MISSING_FINAL",
                message=f"no braille final for syllable {syllable!r}",
                surface=ch,
                span=span,
                source="backend.zh",
            )
            if not parsed.has_initial():
                cells.append(
                    BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)
                )
    else:
        dots = profile.finals.get(parsed.final)
        if dots is None:
            ctx.warnings.warn(
                code="MISSING_FINAL",
                message=f"no braille cell for final {parsed.final!r}",
                surface=ch,
                span=span,
                source="backend.zh",
            )
            cells.append(
                BrailleCell(dots=(), role="unknown", source_span=span, source_text=ch)
            )
        else:
            cells.append(
                BrailleCell(
                    dots=dots,
                    role="zh_final",
                    source_span=span,
                    source_text=ch,
                )
            )

    # Tone — controlled by the profile's tone policy.  The strategy
    # is looked up by name from :mod:`brailix.backend.zh.tone` (a
    # registry; new standards can register their own implementations
    # without changes here).  cn_current selects "basic" via the
    # default; cn_ncb selects "ncb_omission" via
    # features.zh.tone_strategy.
    #
    # The NCB profile also ships char- and word-level disambiguation
    # overrides inside its zh_exceptions resource.
    # When such an override fires for this character, we short-circuit
    # past the policy's tone-omission decision — but only if the policy
    # wasn't already going to emit.  The policy's neutral-tone
    # suppression still wins (a neutral tone never gets a cell).
    policy = tone_policy_for(profile)
    should_emit = policy.should_emit_tone(
        syllable=syllable,
        parsed=parsed,
        next_syllable=next_syllable,
        next_parsed=next_parsed,
    )
    if not should_emit and parsed.tone and parsed.tone != "5":
        exc = profile.zh_exceptions
        if exc is not None:
            if exc.char_overrides is not None and exc.char_overrides.should_force_keep_tone(ch):
                should_emit = True
            elif exc.word_overrides is not None and exc.word_overrides.should_force_keep_tone(
                word_surface=word_surface,
                char_index_in_word=char_index_in_word,
            ):
                should_emit = True
    if should_emit:
        tone_dots = profile.tones.get(parsed.tone, ())
        if tone_dots:
            cells.append(
                BrailleCell(
                    dots=tone_dots,
                    role="zh_tone",
                    source_span=span,
                    source_text=ch,
                )
            )
    return cells


def _char_span(parent: Span | None, index: int) -> Span | None:
    if parent is None:
        return None
    return Span(parent.start + index, parent.start + index + 1)
