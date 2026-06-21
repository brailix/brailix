"""Inline-math island codec — the lingua franca for *deferred* inline math.

When the input layer extracts an inline math fragment from a host
document (Word OMML, a legacy EQ field, ...) it does **not** convert it.
Instead it embeds the *raw* fragment, tagged with its source dialect, as
a text *island* inside the surrounding paragraph's ``Block.text``. The
frontend's math pass (:func:`brailix.frontend.math.parse_math_tree`,
driven from ``Pipeline._attach_math``) then converts that island exactly
as it converts a user-typed ``$...$`` fragment — so the input layer never
imports the math frontend, and inline math defers just like a display
:class:`~brailix.ir.document.MathBlock` does.

This module is the single definition of the island wire format. It is
imported by both the writer (:mod:`brailix.input.docx`) and the reader
(:mod:`brailix.frontend.normalize`); neither imports the other, and the
``$...$`` convention is no longer duplicated knowledge spread across the
two layers.

Format
------
A tagged island is::

    "$" + US + source + US + payload + "$"

where ``US`` is the ASCII Unit Separator (``\\x1d``). The payload has its
whitespace runs flattened to single spaces and every literal ``$``
replaced by the ASCII Record Separator (``\\x1e``).

Two design points make this safe:

* The leading ``$`` keeps a tagged island lexically a ``$...$`` region,
  so the segmenter recognises it through its existing protected-region
  scan (``frontend.segment._iter_inline_math_spans``) with no new pattern. The
  ``US`` immediately after the ``$`` is the single discriminator between
  a *tagged* island (decoded here) and a plain user-typed ``$x^2$`` /
  ``$<math>...$`` fragment (handled by the normalizer's LaTeX/MathML
  sniff).
* ``US`` and ``RS`` are both illegal in XML 1.0 character data and never
  appear in Word EQ-field text, so they delimit and escape with no risk
  of colliding with real payload content. Flattening newlines and
  escaping ``$`` is what lets the island survive that segmenter scan,
  whose regex rejects an inner ``$`` or newline.

The escaping is transport-level and fully reversed by :func:`unwrap`, so
the source adapter downstream receives the byte-for-byte original payload
(a real ``$``, not an XML character reference).
"""

from __future__ import annotations

import re

# ASCII Unit Separator — delimits the ``$<US>source<US>payload$`` fields.
_TAG = "\x1d"
# ASCII Record Separator — stands in for a literal ``$`` inside the payload
# so the island carries no inner ``$`` (which would end the segmenter's span).
_DOLLAR = "\x1e"
_WS_RE = re.compile(r"\s+")


def wrap(source: str, payload: str) -> str:
    """Encode raw ``payload`` (written in dialect ``source``) as a tagged
    inline-math island ready to embed in a paragraph's ``text``.

    Whitespace runs are flattened to single spaces and literal ``$`` is
    escaped; :func:`unwrap` restores the original payload exactly.
    """
    flat = _WS_RE.sub(" ", payload).strip().replace("$", _DOLLAR)
    return f"${_TAG}{source}{_TAG}{flat}$"


def is_tagged(piece: str) -> bool:
    """True if ``piece`` is a tagged island produced by :func:`wrap`.

    Distinguishes it from a plain user-typed ``$...$`` fragment, which
    opens with ``$`` but not ``$`` + Unit Separator.
    """
    return piece.startswith("$" + _TAG) and piece.endswith("$")


def unwrap(island: str) -> tuple[str, str]:
    """Decode a tagged island back to ``(source, payload)``.

    Inverse of :func:`wrap`. Raises :class:`ValueError` if ``island`` is
    not a well-formed tagged island, so callers should gate on
    :func:`is_tagged` first.
    """
    if not is_tagged(island):
        raise ValueError("not a tagged inline-math island")
    # Drop the ``$`` wrappers, then split the leading-US-prefixed body into
    # ["", source, payload]; maxsplit keeps any (illegal-but-defensive) US in
    # the payload intact.
    parts = island[1:-1].split(_TAG, 2)
    if len(parts) != 3 or parts[0] != "":
        raise ValueError(f"malformed tagged inline-math island: {island!r}")
    return parts[1], parts[2].replace(_DOLLAR, "$")
