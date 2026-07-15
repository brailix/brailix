"""Resource limits for the input layer — a size budget for untrusted files.

The input adapters read a whole document into memory before parsing (a
``.txt`` / ``.md`` via :func:`Path.read_bytes`, a ``.mxl`` / ``.docx`` as
a byte blob handed to :mod:`zipfile`, MIDI as bytes to the decoder). That
is fine for a desktop user opening their own files, but a service that
accepts *uploads* needs a ceiling: without one, a multi-gigabyte file
spikes process memory the instant it is read — a cheap denial of service
in a shared deployment (ARCHITECTURE.md).

:class:`InputLimits` is that ceiling, enforced by :func:`brailix.input.
parse_file` as a ``stat()`` gate **before** any read, so an oversized file
is rejected without ever being loaded. The defaults
(:data:`DEFAULT_INPUT_LIMITS`) are deliberately *generous* — far above any
realistic braille document — so normal use never trips them; a server that
processes untrusted input tightens them, and a caller that wants no ceiling
passes :meth:`InputLimits.unlimited`.

The archive-internal caps (a single ``.mxl`` / ``.docx`` member's
decompressed size, the member count, the total inflated bytes — the
zip-bomb defence) live with their respective adapters
(:mod:`brailix.frontend.music.adapters.mxl`, :mod:`brailix.input.docx`);
this module owns only the *outer* whole-file budget those adapters can't
see because it applies before their format is even known.
"""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from brailix.core.errors import BrailixError

# 512 MiB. Matches the ``.docx`` adapter's total-uncompressed budget, so the
# outer file gate and the inner archive gate agree on the same order of
# magnitude. A real braille source — even a multi-thousand-page book, a
# full-score ``.mxl``, or a word-list with one block per line — is orders of
# magnitude smaller; this only stops a pathological upload.
_DEFAULT_MAX_FILE_BYTES = 512 * 1024 * 1024

# 50 million characters. Bounds the *decoded* text a text-format adapter hands
# the frontend (which does per-character work), independently of the byte gate
# — a valid but absurd multi-hundred-MB text file is stopped here even when its
# byte count squeaks under the file gate. A large real document is a few
# million characters at most, so 50M is generous headroom.
_DEFAULT_MAX_TEXT_CHARS = 50_000_000


class InputTooLargeError(BrailixError):
    """Raised when an input file (or its decoded text) exceeds an
    :class:`InputLimits` ceiling.

    Carries the human-readable ``str`` plus the machine-usable ``kind``
    (``"file_bytes"`` / ``"text_chars"``), the offending ``actual`` size and
    the ``limit`` it crossed, so a service can log / surface a precise
    "file too large" response instead of parsing a string message.
    """

    def __init__(self, kind: str, actual: int, limit: int, *, detail: str = ""):
        unit = "bytes" if kind == "file_bytes" else "characters"
        msg = (
            f"input exceeds the {kind} limit: {actual} {unit} > {limit} {unit}"
        )
        if detail:
            msg = f"{msg} ({detail})"
        super().__init__(msg)
        self.kind = kind
        self.actual = actual
        self.limit = limit


@dataclass(frozen=True, slots=True)
class InputLimits:
    """A whole-file size budget for the input layer.

    * ``max_file_bytes`` — the on-disk file-size ceiling, checked by a
      ``stat()`` gate before any read. The primary DoS guard: an oversized
      upload is refused without being loaded into memory.
    * ``max_text_chars`` — the decoded-text ceiling for text formats
      (plain / Markdown / sniffed ``.xml``), checked after decode. A second,
      complementary bound on the work the frontend then does per character.

    Frozen so an instance can be shared freely (e.g. one server-wide policy).
    Both fields are generous by default (:data:`DEFAULT_INPUT_LIMITS`); a
    service tightens them, and :meth:`unlimited` opts out entirely.
    """

    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS

    @classmethod
    def unlimited(cls) -> InputLimits:
        """An :class:`InputLimits` that never rejects anything.

        For a trusted local caller (a desktop app opening the user's own
        files, a batch script over known-good inputs) that wants the raw
        pre-limit behaviour. Implemented with ``sys.maxsize`` sentinels so the
        gates are plain comparisons with no special-casing.
        """
        return cls(max_file_bytes=sys.maxsize, max_text_chars=sys.maxsize)

    def check_file_size(self, path: str | os.PathLike[str]) -> None:
        """Reject ``path`` if it is larger than ``max_file_bytes``.

        The pre-read gate: a single ``stat()`` (no bytes read), raising
        :class:`InputTooLargeError` before an oversized file can be loaded.
        Propagates :class:`FileNotFoundError` for a missing path, exactly as a
        subsequent read would — the gate never masks it.
        """
        size = Path(path).stat().st_size
        if size > self.max_file_bytes:
            raise InputTooLargeError(
                "file_bytes",
                size,
                self.max_file_bytes,
                detail=str(path),
            )

    def check_text_length(self, text: str) -> None:
        """Reject ``text`` if it is longer than ``max_text_chars``.

        The post-decode gate for text formats — a wholesale ``read`` stays
        bounded by :meth:`check_file_size`, but the decoded character count is
        the size the frontend actually walks, so it gets its own ceiling.
        """
        n = len(text)
        if n > self.max_text_chars:
            raise InputTooLargeError("text_chars", n, self.max_text_chars)


# The default policy applied by :func:`brailix.input.parse_file` when the
# caller passes no explicit ``limits``. Generous enough that a desktop user
# opening their own document never notices it; low enough that a pathological
# upload is refused. A service handling untrusted input should pass a tighter
# :class:`InputLimits`; a trusted local caller can pass
# :meth:`InputLimits.unlimited`.
DEFAULT_INPUT_LIMITS = InputLimits()
