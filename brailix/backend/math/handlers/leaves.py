"""Leaf-element handlers for the math backend.

Covers the atomic MathML leaves — ``<mi>`` (identifier / function name),
``<mn>`` (number run), ``<mo>`` (operator / relation / delimiter / shape /
big-op symbol), ``<mspace>`` (forced line break), and ``<mtext>`` (literal
text) — plus the tiny ``_emit_as_mo`` shim that lets other paths feed a
bare string through the ``<mo>`` machinery.

This module is a dispatch sink: it imports nothing from sibling handler
submodules.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

from brailix.backend._digits import (
    DigitRoles,
    DigitRunPolicy,
    emit_digit_run,
)
from brailix.backend._inline import rebase_translated_cells
from brailix.backend.math.context import MathBrailleContext
from brailix.backend.math.utils import (
    _NUMBER_BREAKING_ROLES,
    _ROLE_TO_CELL_ROLE,
    _emit_structure,
    _last_is_blank,
    _math_prose_punct,
    _mi_routes_to_function,
    _previous_suppresses_space_before,
    _unknown_cell,
)
from brailix.core.chars import nonstandard_char_hint
from brailix.ir.braille import BrailleCell, blank_cell, line_break_cell

# Math <mn> digit runs are labelled "math_digit"; the shared emitter owns
# the number-sign / decimal / thousands / full-width-digit logic.
_MATH_DIGIT_ROLES = DigitRoles(digit="math_digit")
_MATH_DIGIT_POLICY = DigitRunPolicy(
    roles=_MATH_DIGIT_ROLES,
    # A full-width digit inside a formula is a source writing error —
    # warn instead of silently normalising it (matches the policy for
    # full-width letters / operators).
    fold_nonascii=False,
    warn_source="backend.math",
    unknown_code="MATH_UNKNOWN_DIGIT",
    missing_code="MATH_MISSING_NUMBER_PART",
)


def _warn_unknown_char(
    mctx: MathBrailleContext,
    cells: list[BrailleCell],
    *,
    code: str,
    kind: str,
    text: str,
) -> None:
    """Warn that ``text`` has no braille mapping and append an unknown cell.

    Shared by the identifier-char / mo / mtext-per-char leaf paths, whose
    unknown-character tails were byte-identical apart from the warning
    ``code`` and the human-readable ``kind`` noun ("identifier" / "symbol"
    / "text char"). Appends a :func:`nonstandard_char_hint` to the message
    when one is available. Does NOT touch ``need_number_sign`` — callers
    set it (identifier / mo) or skip it (the mtext loop) as their context
    requires.
    """
    hint = nonstandard_char_hint(text)
    mctx.backend.warnings.warn(
        code=code,
        message=f"no braille mapping for math {kind} {text!r}"
        + (f" — {hint}" if hint else ""),
        surface=text,
        span=mctx.span,
        source="backend.math",
    )
    cells.append(_unknown_cell(text, mctx.span))


def _emit_mi(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Identifier.

    MathML convention: a single-char ``<mi>`` is a variable (``x``,
    ``π``, ``Δ``); a multi-char ``<mi>`` is a function name (``sin``,
    ``log``, ``arcsin``). For function names the profile's ``functions``
    table holds the abbreviation; unknown names fall back to a
    letter-by-letter spelling.

    In chemistry mode an ``<mi>`` is an element symbol (``H``, ``Si``),
    not a variable or function — route it to the chem element emitter.

    A multi-char ``<mi>`` is only a *function* when it routes there
    (registered name, or non-letter content like a literal ``\\foo``);
    a plain multi-letter run — an OMML/MTEF word token, ``\\mathrm{ABC}``,
    or a run the coalescer merged — is a letter word and takes per-class
    letter signs instead.
    """
    if mctx.chem:
        from brailix.backend.math import chem as _chem

        _chem.emit_element(cells, mctx, elem)
        return
    text = (elem.text or "").strip()
    if not text:
        return
    if len(text) > 1:
        if _mi_routes_to_function(text, mctx.profile):
            _emit_function_name(cells, mctx, text)
        else:
            _emit_letter_runs(cells, mctx, text)
        return
    _emit_identifier_char(cells, mctx, text)


def _emit_one_letter(
    cells: list[BrailleCell], mctx: MathBrailleContext, ch: str
) -> bool:
    """Emit one letter, sharing its class/script prefix across a same-class
    baseline run. Returns ``False`` (emitting nothing) when ``ch`` is not a
    letter, so callers can fall back to the symbol / punctuation chain.

    The letter sign (class/script prefix) is written only when the active
    run's class differs from this letter's — consecutive same-class letters
    reuse it, *even across a script base* (``ab^2`` / ``a^2b`` are one
    lowercase run, not two) or a coalesced word (``ab`` / ``ABC``). A class
    change starts a new sign (``Abc`` → ⠠⠁⠰⠃⠉, ``πr`` → ⠨⠏⠰⠗). Math keeps a
    single sign for an all-capital run (``ABC`` → ⠠⠁⠃⠉) — the per-class run
    structure carries the case; the whole-word-capitals ⠠⠠ doubling is an
    embedded-English convention (``backend.latin``), not a math one.

    For the *first* letter of a run this emits exactly what
    ``profile.letter(ch)`` would (``letter_prefix.{class}`` + bare letter);
    the run state only ever *omits* a prefix a same-class predecessor
    already wrote, so single-word output is unchanged — only cross-structure
    adjacency (the case the old per-element prefix double-counted) differs.
    """
    profile = mctx.profile
    cls = profile.letter_class(ch)
    bare = profile.bare_letter(ch)
    if cls is None or bare is None:
        return False
    if mctx.letter_run_class != cls:
        cells.extend(
            BrailleCell(
                dots=dots,
                role="math_identifier",
                source_span=mctx.span,
                source_text=ch,
            )
            for dots in profile.math_structure(f"letter_prefix.{cls}")
        )
        mctx.letter_run_class = cls
    cells.append(
        BrailleCell(
            dots=bare,
            role="math_identifier",
            source_span=mctx.span,
            source_text=ch,
        )
    )
    mctx.need_number_sign = True
    return True


def _emit_identifier_char(
    cells: list[BrailleCell], mctx: MathBrailleContext, ch: str
) -> None:
    """Emit one identifier character.

    A letter shares its class/script prefix with an adjacent same-class
    baseline run (see :func:`_emit_one_letter`). A non-letter character
    breaks any active run, then falls through the lookup chain:
    math_symbol (catches operators / shapes / extras that surface as
    ``<mi>`` instead of ``<mo>`` — latex2mathml emits ``\\pm`` and other
    binary operators as ``<mi>±</mi>`` rather than ``<mo>±</mo>``) →
    punctuation → unknown + warning.

    Symbol-table fallback re-dispatches through :func:`_emit_mo` so the
    symbol's spacing and role (op / rel / big_op...) take effect —
    otherwise mid-formula ``\\pm`` would lose its ``space_before`` flag.
    """
    if _emit_one_letter(cells, mctx, ch):
        return
    # Not a letter — it interrupts any active letter run.
    mctx.break_letter_run()
    profile = mctx.profile
    if profile.math_symbol(ch) is not None:
        _emit_as_mo(cells, mctx, ch)
        return
    punct = _math_prose_punct(profile.punctuation, ch)
    if punct:
        cells.extend(
            BrailleCell(
                dots=dots,
                role="math_identifier",
                source_span=mctx.span,
                source_text=ch,
            )
            for dots in punct
        )
        mctx.need_number_sign = True
        return
    _warn_unknown_char(
        mctx, cells, code="MATH_UNKNOWN_IDENTIFIER", kind="identifier", text=ch
    )
    mctx.need_number_sign = True


def _emit_letter_runs(
    cells: list[BrailleCell], mctx: MathBrailleContext, text: str
) -> None:
    """Emit a stretch of letters (a multi-letter ``<mi>`` word — a coalesced
    run, ``\\mathrm{ABC}``, or an OMML/MTEF word token) with per-class
    letter signs.

    Delegates each character to :func:`_emit_identifier_char`, so the
    case/script sign follows the same shared-run rule as everywhere else:
    one sign per same-class stretch (``Abc`` → ⠠⠁⠰⠃⠉, ``πr`` → ⠨⠏⠰⠗,
    ``ABC`` → ⠠⠁⠃⠉), shared with an adjacent same-class letter outside the
    word and reset by a class change or any non-letter character. The
    run state lives on :attr:`MathBrailleContext.letter_run_class`, so a
    leading letter whose class matches the preceding identifier reuses that
    sign instead of repeating it.
    """
    for ch in text:
        _emit_identifier_char(cells, mctx, ch)
    mctx.need_number_sign = True


def _emit_function_name(
    cells: list[BrailleCell], mctx: MathBrailleContext, name: str
) -> None:
    """Emit a multi-char identifier as a function application.

    Shape: ``function_prefix`` cells + name cells. Names registered
    in the profile's functions table emit those cells directly;
    unknown names fall back to spelling: an all-letter name via
    :func:`_emit_letter_runs` (one letter sign per same-class run, not
    one per letter), a name with non-letter characters per-character
    via :func:`_emit_identifier_char`.

    latex2mathml sometimes emits ``<mi>\\arccot</mi>`` (literal
    backslash, because it doesn't recognise the command) instead of
    ``<mi>arccot</mi>``. We strip a leading backslash before the table
    lookup so authors don't have to register both spellings.
    """
    _emit_structure(cells, mctx, "indicator.symbol", role="math_function_prefix")
    lookup_name = name.lstrip("\\") if name.startswith("\\") else name
    registered = mctx.profile.math_function(lookup_name)
    if registered is not None:
        cells.extend(
            BrailleCell(
                dots=dots,
                role="math_function_name",
                source_span=mctx.span,
                source_text=name,
            )
            for dots in registered
        )
    elif all(
        mctx.profile.letter_class(ch) is not None for ch in lookup_name
    ):
        # Spelled fallback on the cleaned name (backslash stripped): the
        # name is a letter run, so the letter-sign rule applies — one
        # sign per same-class stretch, not one per letter.
        _emit_letter_runs(cells, mctx, lookup_name)
    else:
        # Per-char fallback for names with non-letter content.
        for ch in lookup_name:
            _emit_identifier_char(cells, mctx, ch)
    # A function application is its own unit: a spelled-out name must not
    # share a letter sign with a following argument letter (``\arccot x``
    # — x is a separate variable, not the tail of the name).
    mctx.break_letter_run()
    mctx.need_number_sign = True


def _emit_mn(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    text = (elem.text or "").strip()
    if not text:
        return
    profile = mctx.profile
    # A baseline digit run interrupts a letter run (``a`` and ``b`` in
    # ``a2b`` are not adjacent letters). Script-position digits never reach
    # here on the run-sharing path — single digits go through the lowered
    # form, multi-digit script content runs inside the script's save/restore.
    mctx.break_letter_run()
    emit_digit_run(
        cells,
        text,
        profile=profile,
        warnings=mctx.backend.warnings,
        policy=_MATH_DIGIT_POLICY,
        want_number_sign=(
            mctx.need_number_sign and profile.feature("math.number_sign", True)
        ),
        span_at=lambda _i: mctx.span,
        number_sign_span=mctx.span,
    )
    mctx.need_number_sign = False


def _emit_as_mo(
    cells: list[BrailleCell], mctx: MathBrailleContext, text: str
) -> None:
    """Helper: emit ``text`` as if it had arrived as a ``<mo>`` element.

    The math backend reuses ``_emit_mo`` from a couple of fallback paths
    (``<mi>`` content that turns out to be a symbol, ``<mtext>`` char
    runs). Building a tiny ``ET.Element`` keeps a single source of truth
    in :func:`_emit_mo` for spacing / role / number-sign behaviour.
    """
    elem = ET.Element("mo")
    elem.text = text
    _emit_mo(cells, mctx, elem)


def _emit_mo(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Operator / relation / delim / punct / shape / big-op symbol.

    Per-symbol role and spacing live in the profile's symbols table.
    Spacing is gated by the ``math.op_spacing`` feature; specific
    operators control their own ``space_before`` / ``space_after``.

    In chemistry mode, the gas / precipitate arrows render via the chem
    operator path (cells attached with no leading space); any ``<mo>`` the
    chem table doesn't recognise falls through to the ordinary path below.

    A ``data-bk-warn="repeated-operator"`` tag (set by the chem frontend on
    the second of two consecutive connectors) raises a non-fatal warning —
    the operator still renders, so the faithful output is unchanged; the
    writer is just told the doubled ``==`` looks like a typo.
    """
    # An operator / relation / delimiter / shape / symbol all interrupt a
    # letter run, on every path below (chem arrow, ordinary symbol, and the
    # function-name / letter / punct fallbacks for unmapped chars).
    mctx.break_letter_run()
    if elem.get("data-bk-warn") == "repeated-operator":
        op = (elem.text or "").strip()
        hint = ""
        if op == "<":
            hint = " (did you mean ≪, much-less-than?)"
        elif op == ">":
            hint = " (did you mean ≫, much-greater-than?)"
        mctx.backend.warnings.warn(
            code="MATH_REPEATED_OPERATOR",
            message=(
                f"consecutive duplicate operator {op!r}; likely a typo{hint}"
                " — translated faithfully as written"
            ),
            surface=op or None,
            span=mctx.span,
            source="backend.math",
        )
    if mctx.chem:
        from brailix.backend.math import chem as _chem

        if _chem.emit_operator(cells, mctx, elem):
            return
    text = (elem.text or "").strip()
    if not text:
        return
    profile = mctx.profile

    sym_cells = profile.math_symbol(text)
    role = profile.math_symbol_role(text)
    space_before, space_after = profile.math_symbol_spaces(text)
    spacing_enabled = profile.feature("math.op_spacing", True)
    if role is None and sym_cells is not None:
        # Symbol is in the table but has no role — profile JSON went
        # through the validator so this should not happen for shipped
        # profiles. Warn loudly so it gets caught for hand-built ones.
        mctx.backend.warnings.warn(
            code="MATH_SYMBOL_MISSING_ROLE",
            message=f"math symbol {text!r} has cells but no role; defaulting to op",
            surface=text,
            span=mctx.span,
            source="backend.math",
        )
    cell_role = _ROLE_TO_CELL_ROLE.get(role or "", "math_op")

    if sym_cells is None:
        # Fallback chain:
        # (a) Multi-char text that's a known function name → route through
        #     the function path (function_prefix + functions cells). This
        #     handles latex2mathml's output for ``\lim``, ``\max``, etc.
        #     which appears as ``<mo>lim</mo>`` rather than ``<mi>lim</mi>``.
        #     Without this, bare ``\lim`` outside a script context falls
        #     to unknown.
        # (b) Single-char identifier-shaped op (rare).
        # (c) Punctuation char.
        if len(text) > 1 and profile.math_function(text) is not None:
            _emit_function_name(cells, mctx, text)
            return
        ident_seq = profile.letter(text) if len(text) == 1 else None
        if ident_seq is not None:
            cells.extend(
                BrailleCell(
                    dots=dots,
                    role="math_identifier",
                    source_span=mctx.span,
                    source_text=text,
                )
                for dots in ident_seq
            )
            mctx.need_number_sign = True
            return
        punct = _math_prose_punct(profile.punctuation, text)
        if punct:
            cells.extend(
                BrailleCell(
                    dots=dots,
                    role="math_op",
                    source_span=mctx.span,
                    source_text=text,
                )
                for dots in punct
            )
            mctx.need_number_sign = True
            return
        _warn_unknown_char(
            mctx, cells, code="MATH_UNKNOWN_SYMBOL", kind="symbol", text=text
        )
        mctx.need_number_sign = True
        return

    if (
        spacing_enabled
        and space_before
        and cells
        and not _last_is_blank(cells)
        and not _previous_suppresses_space_before(cells)
    ):
        cells.append(blank_cell(mctx.span))
    indicator = profile.math_symbol_indicator(text)
    if indicator is not None:
        # Category marker (⠫ symbol / ⠰ operation / ⠈ negation), emitted
        # here from ``structures.indicator.<name>`` — the same backend
        # pathway a function name's ⠫ uses. Keeps the symbol table bare
        # (just the distinguishing cells, or a ref to the negated base)
        # instead of baking the marker into every entry.
        _emit_structure(cells, mctx, f"indicator.{indicator}", role=cell_role)
    cells.extend(
        BrailleCell(
            dots=dots,
            role=cell_role,
            source_span=mctx.span,
            source_text=text,
        )
        for dots in sym_cells
    )
    # ``data-bk-tight`` (set by the frontend on a thousands-grouping comma,
    # ``1,000``) suppresses the trailing space — the comma joins one quantity
    # rather than separating list items, so it reads tight.
    tight = elem.get("data-bk-tight") is not None
    if spacing_enabled and space_after and not tight:
        cells.append(blank_cell(mctx.span))
    # A tight thousands comma is part of ONE quantity, so it must NOT restart
    # the number sign (1,000 = ⠼⠁⠐⠚⠚⠚, a single number sign). Every other
    # number-breaking role — operators, relations, shapes, big-ops, delimiters
    # and *spaced* punctuation (list / coordinate separators) — does.
    if (role in _NUMBER_BREAKING_ROLES or role is None) and not (
        role == "punct" and tight
    ):
        mctx.need_number_sign = True


def _emit_mspace(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """``<mspace linebreak="newline">`` — a forced line break (bare
    ``\\\\`` outside a table environment).

    Emits :data:`LINE_BREAK_CELL`, which the renderers turn into a real
    line break — the same sentinel matrix / equation-system rows use.
    Consecutive breaks collapse to one. Width-only ``<mspace>`` (print
    spacing) is dropped by the normalizer and never reaches dispatch;
    ignore it defensively for direct backend feeds.
    """
    if elem.get("linebreak") != "newline":
        return
    mctx.break_letter_run()
    if not (cells and cells[-1].role == "line_break"):
        cells.append(line_break_cell(mctx.span))
    mctx.need_number_sign = True


def _emit_mtext(
    cells: list[BrailleCell], mctx: MathBrailleContext, elem: ET.Element
) -> None:
    """Literal text inside math (``\\text{...}`` / ``<mtext>``).

    ``\\text{...}`` is natural-language text, so the primary path hands it
    to the Pipeline-injected ``inline_text_translator`` — the same zh /
    latin text seam chem reaction conditions and music lyrics use
    (ARCHITECTURE §12). That makes Chinese render as Chinese braille,
    English as word-level text (one letter prefix per word, not per char),
    and spaces translate correctly — including the U+00A0 latex2mathml
    emits for a literal space inside ``\\text``.

    Falls back to a per-char math-table lookup (symbols → letters →
    punctuation → unknown + warning; spaces become blank cells) when no
    translator is wired: a bare backend run, or a unit test feeding MathML
    straight to the backend.

    In chemistry mode an ``<mtext data-bk-chem-state>`` is a physical-state
    label ((s)/(l)/(g)/(aq)) — routed to the chem state emitter (one
    Latin-lowercase prefix + bare letters) instead.
    """
    # Literal text is its own unit: it interrupts a math letter run on both
    # sides (a letter before / after ``\text{...}`` doesn't share its sign),
    # and its own cells are text, not run-bearing identifiers.
    mctx.break_letter_run()
    if mctx.chem and elem.get("data-bk-chem-state") is not None:
        from brailix.backend.math import chem as _chem

        _chem.emit_state(cells, mctx, elem)
        return
    text = elem.text or ""
    translator = mctx.backend.inline_text_translator()
    if text.strip() and translator is not None:
        # latex2mathml encodes a literal space inside \text as U+00A0;
        # normalise it to a real space so the text path sees a word break.
        # The translator's cells carry throwaway-document spans \u2014 rebase
        # onto the formula's own span so proofread jumps land here.
        cells.extend(
            rebase_translated_cells(
                translator(text.replace("\u00a0", " ")), mctx.span
            )
        )
    else:
        _emit_mtext_per_char(cells, mctx, text)
    mctx.need_number_sign = True


def _emit_mtext_per_char(
    cells: list[BrailleCell], mctx: MathBrailleContext, text: str
) -> None:
    """Per-char ``<mtext>`` fallback for backend-only runs (no injected
    text translator): symbols → letters → punctuation → unknown + warning.
    Spaces (incl. the U+00A0 latex2mathml emits) become blank cells."""
    profile = mctx.profile
    for ch in text:
        if ch in (" ", "\u00a0"):
            cells.append(blank_cell(mctx.span))
            continue
        dots_seq = profile.math_symbol(ch)
        if dots_seq is None:
            dots_seq = profile.letter(ch)
        if dots_seq is None:
            punct = profile.punctuation.get(ch)
            if punct:
                dots_seq = punct
        if dots_seq is None:
            _warn_unknown_char(
                mctx, cells, code="MATH_UNKNOWN_TEXT_CHAR", kind="text char", text=ch
            )
            continue
        cells.extend(
            BrailleCell(
                dots=dots,
                role="math_text",
                source_span=mctx.span,
                source_text=ch,
            )
            for dots in dots_seq
        )


_DISPATCH_PARTIAL = {
    "mi": _emit_mi,
    "mn": _emit_mn,
    "mo": _emit_mo,
    "mspace": _emit_mspace,
    "mtext": _emit_mtext,
}
