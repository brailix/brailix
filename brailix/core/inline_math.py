"""Inline-math island codec — the lingua franca for *deferred* inline math.

When the input layer extracts an inline math fragment from a host
document (Word OMML, a legacy EQ field, ...) it does **not** convert it.
Instead it embeds the *raw* fragment, tagged with its source dialect, as
a text *island* inside the surrounding paragraph's ``Block.text``. The
frontend's math pass (:func:`brailix.frontend.math.parse_math_tree`,
driven from ``FrontendDriver.attach_math``) then converts that island exactly
as it converts a user-typed ``$...$`` fragment — so the input layer never
imports the math frontend, and inline math defers just like a display
:class:`~brailix.ir.document.MathBlock` does.

This module is the single definition of the island wire format. It is
imported by both the writer (:mod:`brailix.input.docx`) and the reader
(:mod:`brailix.frontend.normalization`); neither imports the other, and the
``$...$`` convention is no longer duplicated knowledge spread across the
two layers.

Format
------
A tagged island is::

    "$" + US + source + US + payload + "$"

where ``US`` is the ASCII Unit Separator (``\\x1d``). Inside the payload,
whitespace runs are flattened to single spaces and the result trimmed —
where "whitespace" means every ``\\s`` character **except** the four ASCII
separator controls U+001C–U+001F (FS/GS/RS/US), which the codec treats as
payload data. Two characters the format needs for itself are escaped with
the ASCII Record Separator (``\\x1e``): a payload-borne RS is doubled
(``RS RS``), and a literal ``$`` becomes ``RS GS`` (``\\x1e\\x1f``).

Two design points make this safe:

* The leading ``$`` keeps a tagged island lexically a ``$...$`` region,
  so the segmenter recognises it through its existing protected-region
  scan (``frontend.segmentation._iter_inline_math_spans``) with no new pattern. The
  ``US`` immediately after the ``$`` is the single discriminator between
  a *tagged* island (decoded here) and a plain user-typed ``$x^2$`` /
  ``$<math>...$`` fragment (handled by the normalizer's LaTeX/MathML
  sniff).
* ``US`` and ``RS`` are both illegal in XML 1.0 character data and never
  appear in Word EQ-field text, so they delimit and escape with no risk
  of colliding with real payload content. A payload that carries them
  anyway — or the neighbouring FS/GS controls, which Python's ``\\s``
  also matches — no longer loses them silently: the flattening skips all
  four, and a payload-borne RS is escaped *before* the ``$`` escaping
  starts, so :func:`unwrap` restores the payload byte for byte. Flattening
  newlines and escaping ``$`` is still what lets the island survive that
  segmenter scan, whose regex rejects an inner ``$`` or newline.

What survives the round trip
---------------------------

The *escaping* is transport-level and fully reversed by :func:`unwrap`: the
source adapter downstream receives a real ``$`` — and a real U+001C–U+001F
control, should the payload have carried one — never a sentinel or an XML
character reference. The *whitespace flattening* is not reversed, and cannot
be — it is what lets the island pass the segmenter's protected-region scan,
whose pattern rejects an inner newline. So :func:`unwrap` returns the payload
exactly as :func:`wrap` normalised it (single spaces between tokens, no
leading or trailing space), not the original bytes.

That loss is safe for the dialects this carries, and only for them: OMML and
EQ-field math are whitespace-insensitive between tokens, so ``x  +  1`` and
``x + 1`` parse identically. A future payload where inner whitespace is
*significant* — a verbatim block, a whitespace-sensitive dialect — could not
ride this format and would need a transport that escapes newlines instead of
collapsing them.
"""

from __future__ import annotations

import re as _re

# ASCII Unit Separator — delimits the ``$<US>source<US>payload$`` fields.
_TAG = "\x1d"
# ASCII Record Separator — the payload field's escape character: it both
# doubles for a payload-borne RS and prefixes the ``$`` escape, so the field
# carries no inner ``$`` (which would end the segmenter's span).
_DOLLAR = "\x1e"
# ASCII Group Separator — the marker half of the ``$`` escape (``RS GS``).
# Adjacent to the format's other controls and equally illegal in XML 1.0,
# so a real payload never needs it escaped in its own right: it passes
# through literally, and only an RS immediately before one is an escape.
_ESCAPED_DOLLAR = "\x1f"
# Whitespace the codec flattens: every ``\s`` character EXCEPT the four
# separator controls U+001C..U+001F. Those are the format's own machinery,
# yet ``\s`` matches them (``'\x1c'.isspace()`` is True), so a plain
# ``\s+`` ate payload-borne ones — ``wrap("omml", "a\x1eb")`` round-tripped
# to ``"a b"``. The lookahead sits INSIDE the ``+`` group, so a run like
# ``" \x1c"`` cannot swallow the control as its second character the way a
# leading-only ``(?![\x1c-\x1f])\s+`` guard would let it.
_WS_RE = _re.compile(r"(?:(?![\x1c-\x1f])\s)+")
# The same class anchored at the field's edges, replacing ``str.strip()``:
# the default strip set is the Unicode whitespace characters, which INCLUDE
# the four controls the codec must preserve.
_WS_EDGE_RE = _re.compile(r"^(?:(?![\x1c-\x1f])\s)+|(?:(?![\x1c-\x1f])\s)+$")
# Payload-field unescaping. Alternation order is load-bearing: the two-char
# escapes must outrank the lone-RS fallback at the same position, or
# ``RS RS RS GS`` (a payload RS followed by a ``$``) would misparse.
_DECODE_RE = _re.compile(
    "|".join(
        _re.escape(token)
        for token in (_DOLLAR + _DOLLAR, _DOLLAR + _ESCAPED_DOLLAR, _DOLLAR)
    )
)


def wrap(source: str, payload: str) -> str:
    """Encode raw ``payload`` (written in dialect ``source``) as a tagged
    inline-math island ready to embed in a paragraph's ``text``.

    Whitespace runs are flattened to single spaces (and the result
    trimmed), then the two reserved characters are escaped — a
    payload-borne record separator doubled first, a literal ``$`` after,
    in that order (doubling after would double the RS the ``$`` escape
    introduces). The U+001C–U+001F controls are payload data: they are
    neither flattened nor escaped. :func:`unwrap` reverses the escaping
    exactly; the whitespace normalisation is deliberately one-way — see
    the module docstring for why, and for which payloads that makes this
    format unsuitable.
    """
    flat = _WS_RE.sub(" ", payload)
    flat = _WS_EDGE_RE.sub("", flat)
    flat = flat.replace(_DOLLAR, _DOLLAR + _DOLLAR)
    flat = flat.replace("$", _DOLLAR + _ESCAPED_DOLLAR)
    return f"${_TAG}{source}{_TAG}{flat}$"


def is_tagged(piece: str) -> bool:
    """True if ``piece`` is a tagged island produced by :func:`wrap`.

    Distinguishes it from a plain user-typed ``$...$`` fragment, which
    opens with ``$`` but not ``$`` + Unit Separator.
    """
    return piece.startswith("$" + _TAG) and piece.endswith("$")


def _decode_payload(field: str) -> str:
    """Unescape a payload field: ``RS RS`` → RS, ``RS GS`` → ``$``.

    A lone ``RS`` (or one followed by anything else) also decodes as ``$`` —
    the shape the wire format used for a ``$`` before payload-borne record
    separators were escaped, so an island written by an older build of this
    codec still decodes the way it was written.
    """
    return _DECODE_RE.sub(
        lambda m: _DOLLAR if m.group() == _DOLLAR + _DOLLAR else "$", field
    )


def unwrap(island: str) -> tuple[str, str]:
    """Decode a tagged island back to ``(source, payload)``.

    Inverse of :func:`wrap` up to whitespace: the escaping is undone exactly
    (including payload-borne U+001C–U+001F controls, which ride the field
    literally), the whitespace flattening :func:`wrap` applied is not
    (module docstring). Raises :class:`ValueError` if ``island`` is not a
    well-formed tagged island, so callers should gate on :func:`is_tagged`
    first.
    """
    if not is_tagged(island):
        raise ValueError("not a tagged inline-math island")
    # Drop the ``$`` wrappers, then split the leading-US-prefixed body into
    # ["", source, payload]; maxsplit keeps any payload-borne US intact
    # (escaped only by the maxsplit bound, not by substitution).
    parts = island[1:-1].split(_TAG, 2)
    if len(parts) != 3 or parts[0] != "":
        raise ValueError(f"malformed tagged inline-math island: {island!r}")
    return parts[1], _decode_payload(parts[2])


def sniff_untagged_source(body: str) -> str:
    """Dialect name for an *untagged* ``$...$`` fragment's body:
    ``"mathml"`` when it opens with ``<math`` (Word-import synthesised
    MathML — the LaTeX grammar cannot begin with an XML element, so one
    prefix check discriminates), else ``"latex"``.

    The discriminator is shared by the markdown input (a ``$$...$$``
    block body) and the frontend normalizer (a user-typed inline
    fragment); it lives here, beside the island codec both layers
    already share, so the two cannot drift on what counts as MathML —
    they were verbatim copies, and extending one would have silently
    mislabeled the other's fragments.
    """
    return "mathml" if body.lstrip().startswith("<math") else "latex"
