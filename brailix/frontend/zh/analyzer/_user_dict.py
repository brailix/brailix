"""Personal segmentation dictionary — a post-pass over the analyzer's tokens.

Every tokenizer gets some words wrong, and which ones differ per engine:
one splits 国家 into 国 / 家, another glues 自 onto the year in
自二〇二六年. Chinese braille writes a word's characters together and
separates words with a blank cell, so a segmentation mistake is a
*visible* braille mistake — which makes "let the user pin the division of
a word once, for every future document" a real need rather than a tuning
knob.

This runs after whichever adapter tokenized, so the pinned division wins
over any engine's opinion and composes with all of them — the same shape
:func:`brailix.frontend.zh.pinyin._apply_user_dict` has for readings, and
for the same reason: the dictionary is an override layer on top of an
adapter, not an alternative adapter, so it does not belong in the registry.

**One mapping, both directions.** An entry maps a surface to the pieces it
should become:

* ``{"国家": ("国家",)}`` — one piece: *this is a word*. Consecutive tokens
  that spell it (国 / 家) are folded into one.
* ``{"国家通用": ("国家", "通用")}`` — several pieces: *cut it here*. A token
  (or run of tokens) spelling 国家通用 comes out as two words.

The single-piece case is not a special case in the code: matching is on the
concatenated surface either way, and the replacement is whatever the value
says. That is what lets one dictionary serve both fixes.

**Ordering note.** The Chinese frontend chains
``tokenize`` → ``pinyin.annotate`` → ``tokens_to_inline``, so this pass
runs *before* readings are resolved. That is deliberate and free: a folded
token has its reading looked up as a whole word (盲文出版社 →
``mang2 wen2 chu1 ban3 she4``), which is what a resolver is good at, rather
than character by character. No reading has to be carried across the
rewrite because none exists yet.
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.span import Span
from brailix.frontend.zh.tokens import ChineseToken

if _TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

# Multi-character surfaces only, mirroring the pinyin dictionary's policy —
# though for a different reason. A reading is refused on one character
# because it is context-dependent (the 多音字 trap); a *division* of one
# character is refused because there is nothing to divide and nothing to
# join: a single-char key could only ever mean "this character is a word",
# which is already true of every character the tokenizer emits alone.
_MIN_SURFACE_LEN = 2


def apply_user_seg_dict(
    tokens: list[ChineseToken],
    seg_dict: Mapping[str, Sequence[str]],
) -> list[ChineseToken]:
    """Re-divide ``tokens`` according to ``seg_dict``, returning a new list.

    Walks left to right taking the **longest** dictionary match that starts
    at a token boundary and ends at one. Both ends matter:

    * *Starts at a boundary* is what keeps a key from matching across the
      middle of a word. With 中国 / 家庭 tokenized and 国家 in the
      dictionary, the concatenated text does contain 国家 — but no token
      starts there, so nothing matches and 中国家庭 is left alone. A
      substring search would have produced 中 / 国家 / 庭.
    * *Ends at a boundary* is what makes the rewrite total: the replaced
      run's characters are exactly the matched key's, so the surface text
      is preserved (this pass never adds or drops a character).

    Spans are rebuilt from the run's own start, one piece after another, so
    the output stays ordered and non-overlapping — proofreading tooling
    highlights source text through these.

    A run is only eligible if its tokens are **source-adjacent**
    (``prev.span.end == next.span.start``). A gap means the source had
    something between them that the analyzer dropped — a space, most often —
    and those two tokens are not one written unit, so folding them would
    silently delete that separator's effect. (THULAC drops the space in
    ``brailix 是``, leaving spans ``(0,7)`` then ``(8,9)``; jieba emits it as
    its own token. The guard handles both without knowing which ran.)

    ``pos`` is dropped on rewritten tokens: it described the analyzer's
    division, which is precisely what the user overrode. Nothing in the
    Chinese path reads it (it rides along to the IR and stops there), so
    dropping it is honest rather than lossy — unlike Japanese, where POS
    drives word spacing.

    Returns the input list unchanged (not a copy) when there is nothing to
    do, so the common no-dictionary path allocates nothing.
    """
    if not seg_dict or not tokens:
        return tokens
    # Longest key bounds how far a run has to reach before it can't match.
    max_key_len = max(len(k) for k in seg_dict)
    if max_key_len < _MIN_SURFACE_LEN:
        return tokens

    out: list[ChineseToken] = []
    i = 0
    n = len(tokens)
    while i < n:
        pieces, run_end = _longest_match_at(tokens, i, seg_dict, max_key_len)
        if pieces is None:
            out.append(tokens[i])
            i += 1
            continue
        out.extend(_rebuild(tokens[i], pieces))
        i = run_end
    return out


def _longest_match_at(
    tokens: list[ChineseToken],
    start: int,
    seg_dict: Mapping[str, Sequence[str]],
    max_key_len: int,
) -> tuple[Sequence[str] | None, int]:
    """Longest dictionary hit for the run beginning at ``tokens[start]``.

    Returns ``(pieces, end_index)`` — the replacement and the exclusive end
    of the consumed run — or ``(None, start)`` when nothing matches.

    Extends the run one token at a time, stopping early at a source gap
    (see :func:`apply_user_seg_dict`) or once the accumulated surface is
    longer than any key. Keeps the last hit rather than the first, so
    ``国家`` and ``国家通用`` both present resolves to the longer one.
    """
    best: Sequence[str] | None = None
    best_end = start
    acc = ""
    j = start
    while j < len(tokens) and len(acc) < max_key_len:
        if j > start and not _source_adjacent(tokens[j - 1], tokens[j]):
            break
        acc += tokens[j].surface
        j += 1
        hit = seg_dict.get(acc)
        if hit:
            best, best_end = hit, j
    return best, best_end


def _source_adjacent(prev: ChineseToken, cur: ChineseToken) -> bool:
    """Whether ``prev`` and ``cur`` are written with nothing between them.

    Unknown spans (hand-built tokens in a fixture) fall back to list
    adjacency alone — the same convention the boundary predicates in
    :mod:`brailix.frontend.zh.analyzer` use.
    """
    if prev.span is None or cur.span is None:
        return True
    return prev.span.end == cur.span.start


def _rebuild(first: ChineseToken, pieces: Sequence[str]) -> list[ChineseToken]:
    """Materialise ``pieces`` as tokens starting at ``first``'s span.

    Each piece's span is laid out end to end from the run's start, so the
    result covers exactly the source the replaced run covered. A run whose
    first token has no span produces spanless tokens rather than inventing
    coordinates — the caller (``tokenize``) is about to shift spans into
    document coordinates, and a fabricated origin would land real text at
    the wrong offset.
    """
    if first.span is None:
        return [ChineseToken(surface=p) for p in pieces]
    out: list[ChineseToken] = []
    start = first.span.start
    for piece in pieces:
        out.append(ChineseToken(surface=piece, span=Span(start, start + len(piece))))
        start += len(piece)
    return out


def normalize_seg_dict(
    raw: Mapping[str, Sequence[str]],
) -> dict[str, tuple[str, ...]]:
    """Drop entries that could not describe a division of their own key.

    The guard for everything that reaches this module from outside — a
    hand-edited dictionary file, a front-end, a caller's literal. Four ways
    an entry is meaningless, all silently skipped rather than raised on,
    because one bad line must not stop a document from compiling:

    * **A surface shorter than two characters** — nothing to divide (see
      :data:`_MIN_SURFACE_LEN`).
    * **Pieces that aren't a sequence of strings** — ``None``, a number, an
      object that will not iterate, a ``None`` sitting among real pieces.
      This one is *structural*: the three below all assume the value can be
      walked as strings at all, and the walk itself used to be the thing
      that raised (``tuple(None)`` → ``TypeError``, out of a function whose
      contract is to skip and carry on). A bare string is refused too, not
      read: ``{"国家": "国家"}`` would iterate to ``("国", "家")`` and so mean
      the opposite of what it looks like — *cut this apart* where the author
      wrote *this is one word*.
    * **No pieces, or an empty piece** — an empty piece would emit a
      zero-width token that no source text backs.
    * **Pieces that don't spell the surface** — the invariant the rewrite
      rests on. ``{"国家": ("国", "家", "们")}`` would otherwise inject a
      character the document never contained.
    """
    out: dict[str, tuple[str, ...]] = {}
    for surface, pieces in raw.items():
        if not isinstance(surface, str) or len(surface) < _MIN_SURFACE_LEN:
            continue
        if isinstance(pieces, (str, bytes)):
            continue
        try:
            parts = tuple(pieces)
        except TypeError:
            continue
        if not parts or any(not isinstance(p, str) or not p for p in parts):
            continue
        if "".join(parts) != surface:
            continue
        out[surface] = parts
    return out


__all__ = ("apply_user_seg_dict", "normalize_seg_dict")
