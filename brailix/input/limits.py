"""Resource limits for the input layer — a size budget for untrusted files.

The input adapters read a whole document into memory before parsing (a
``.txt`` / ``.md`` via :func:`Path.read_bytes`, a ``.mxl`` / ``.docx`` as
a byte blob handed to :mod:`zipfile`, MIDI as bytes to the decoder). That
is fine for a desktop user opening their own files, but a service that
accepts *uploads* needs a ceiling: without one, a multi-gigabyte file
spikes process memory the instant it is read — a cheap denial of service
in a shared deployment.

:class:`InputLimits` is that ceiling, enforced by
:func:`brailix.input.parse_file`
as a ``stat()`` gate **before** any read, so an oversized file
is rejected without ever being loaded. The defaults
(:data:`DEFAULT_INPUT_LIMITS`) are deliberately *generous* — far above any
realistic braille document — so normal use never trips them; a server that
processes untrusted input tightens them, and a caller that wants no ceiling
passes :meth:`InputLimits.unlimited`.

The archive-internal caps (a single ``.mxl`` / ``.docx`` member's
decompressed size, the member count, the total inflated bytes — the
zip-bomb defence) live with their respective adapters
(:mod:`brailix.frontend.music.adapters.mxl`, :mod:`brailix.input.docx`), over
one shared reading of the ZIP central directory
(:mod:`brailix.core._zip`); this module owns only the *outer* whole-file
budget those adapters can't see because it applies before their format is
even known.
"""

from __future__ import annotations

import os as _os
import stat as _stat
import sys as _sys
from dataclasses import dataclass as _dataclass
from pathlib import Path as _Path

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


@_dataclass(frozen=True, slots=True)
class InputLimits:
    """A whole-file size budget for the input layer.

    * ``max_file_bytes`` — the on-disk file-size ceiling, checked by a
      ``stat()`` gate before any read. The primary DoS guard: an oversized
      upload is refused without being loaded into memory.
    * ``max_text_chars`` — the ceiling on the text an adapter hands the
      frontend, checked the moment that text exists. A second, complementary
      bound on the work the frontend then does per character: plain /
      Markdown / sniffed ``.xml``, the raw ``.abc`` source, and the resolved
      MusicXML of a ``.musicxml`` / ``.xml`` / ``.mxl`` / ``.mid`` score
      alike — a score suffix must not be a way around a tightened budget.
      Word documents are the exception: a ``.docx`` has no single decoded
      string, and its always-on archive caps (member count, per-member and
      total decompressed bytes) bound it instead.

    The two are complementary on purpose. A service that accepts large
    binaries but wants a small per-character budget sets a high
    ``max_file_bytes`` and a low ``max_text_chars``; that combination only
    holds if *every* text-producing adapter applies the second gate, which
    is why it lives in the adapters rather than in ``parse_file``'s routing.

    Frozen so an instance can be shared freely (e.g. one server-wide policy).
    Both fields are generous by default (:data:`DEFAULT_INPUT_LIMITS`); a
    service tightens them, and :meth:`unlimited` opts out entirely.
    """

    max_file_bytes: int = _DEFAULT_MAX_FILE_BYTES
    max_text_chars: int = _DEFAULT_MAX_TEXT_CHARS

    def __post_init__(self) -> None:
        """Reject a ceiling that isn't a non-negative ``int``.

        A negative ceiling is not a smaller budget — it is one no file can
        satisfy, so every read fails with a nonsensical "4096 bytes > -2
        bytes" from whatever route happened to run first, far from the
        construction that got it wrong. A ``bool`` is worse than useless for
        the same reason it is accepted at all: ``True`` is silently a
        **one-byte** ceiling and ``False`` a zero-byte one. And the "consume
        at most the ceiling plus one byte" promise in :meth:`read_bounded` is
        spelled ``fp.read(self.max_file_bytes + 1)``, where a negative value
        means ``read(-1)`` — read to EOF, the whole point of the ceiling
        undone. That call is unreachable today only because the ``fstat``
        gate above it rejects every regular file first (``st_size`` is never
        negative); resting a memory bound on the order of two checks is not a
        bound. Validating here makes it one.

        Zero is allowed: "only an empty file / empty text passes" is a
        coherent, if extreme, policy, and the gates read it as one.
        """
        for name in ("max_file_bytes", "max_text_chars"):
            value = getattr(self, name)
            # ``bool`` first: it *is* an ``int`` subclass, so the isinstance
            # check below would wave True/False straight through.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(
                    f"InputLimits.{name} must be an int, got "
                    f"{type(value).__name__} ({value!r})"
                )
            if value < 0:
                raise ValueError(
                    f"InputLimits.{name} must be >= 0, got {value!r} — a "
                    f"negative ceiling rejects every input rather than "
                    f"loosening the budget (use InputLimits.unlimited() for "
                    f"no ceiling)"
                )

    @classmethod
    def unlimited(cls) -> InputLimits:
        """An :class:`InputLimits` that never rejects anything.

        For a trusted local caller (a desktop app opening the user's own
        files, a batch script over known-good inputs) that wants the raw
        pre-limit behaviour. Implemented with ``sys.maxsize`` sentinels so the
        gates are plain comparisons with no special-casing.
        """
        return cls(max_file_bytes=_sys.maxsize, max_text_chars=_sys.maxsize)

    def check_file_size(self, path: str | _os.PathLike[str]) -> None:
        """Reject ``path`` if it is larger than ``max_file_bytes``.

        The cheap pre-read gate: one ``stat()``, no bytes read, so an
        obviously-oversized file is refused before any adapter is even chosen.
        Propagates :class:`FileNotFoundError` for a missing path, exactly as a
        subsequent read would — the gate never masks it.

        **This is a fast reject, not the guarantee.** It describes the path at
        one instant; whatever reads the file afterwards opens that path again,
        and in between it can grow, be atomically replaced, or be a symlink
        repointed at something else. The binding promise — that no more than
        ``max_file_bytes`` is ever *consumed* — belongs to
        :meth:`read_bounded`, which is what every whole-file read in the input
        layer goes through.
        """
        size = _Path(path).stat().st_size
        if size > self.max_file_bytes:
            raise InputTooLargeError(
                "file_bytes",
                size,
                self.max_file_bytes,
                detail=str(path),
            )

    def read_bounded(self, path: str | _os.PathLike[str]) -> bytes:
        """Read ``path`` whole, consuming at most ``max_file_bytes``.

        The gate that actually holds. :meth:`check_file_size` checks the path;
        this checks the bytes, on a handle it holds open for the whole read, so
        there is no instant between the decision and the consumption for the
        file to change underneath:

        * ``fstat`` on the **open descriptor** describes the object being read,
          not whatever the name resolves to a moment later — a path swapped to
          a different file, or a symlink repointed after the check, cannot
          smuggle a larger one in;
        * the read itself asks for one byte past the ceiling, so even a file
          growing *while* it is being read stops there. Measured before this
          existed: a 2-byte file under a 16-byte ceiling was replaced with 4096
          bytes after the gate and all 4096 were parsed;
        * a non-regular file is refused outright. ``st_size`` is meaningless
          for a FIFO or a device — a named pipe reports 0 and then delivers
          without end, which is the size gate reading as "empty, go ahead" on
          the one input that can never be bounded by it.

        Returns the file's bytes. Raises :class:`InputTooLargeError` when the
        content exceeds the ceiling, and propagates :class:`OSError` /
        :class:`FileNotFoundError` as an ordinary read would.
        """
        with _Path(path).open("rb") as fp:
            info = _os.fstat(fp.fileno())
            if not _stat.S_ISREG(info.st_mode):
                raise InputTooLargeError(
                    "file_bytes",
                    0,
                    self.max_file_bytes,
                    detail=f"{path} is not a regular file",
                )
            if info.st_size > self.max_file_bytes:
                raise InputTooLargeError(
                    "file_bytes",
                    info.st_size,
                    self.max_file_bytes,
                    detail=str(path),
                )
            # One past the ceiling: enough to prove it was exceeded without
            # loading a byte more than that proof needs. ``unlimited()``
            # spells its ceiling ``sys.maxsize``, and ``read(maxsize + 1)``
            # overflows the index-sized argument — there is nothing to bound
            # there anyway, so read straight through.
            if self.max_file_bytes >= _sys.maxsize:
                data = fp.read()
            else:
                data = fp.read(self.max_file_bytes + 1)
        if len(data) > self.max_file_bytes:
            raise InputTooLargeError(
                "file_bytes",
                len(data),
                self.max_file_bytes,
                detail=f"{path} (grew while being read)",
            )
        return data

    def read_bounded_text(
        self, path: str | _os.PathLike[str], *, normalize_newlines: bool = False
    ) -> str:
        """Read ``path`` whole through :meth:`read_bounded` and decode it,
        tolerating the UTF-16 BOM Windows tools write.

        The single whole-file text read for the input layer. Every text format
        needs the same two things — the byte ceiling bound to the handle being
        read, and a BOM-aware decode — and they were implemented twice: once
        for plain / Markdown, once for XML. Both took ``limits`` as a
        *defaulted* parameter, so a call site that forgot to pass the caller's
        policy silently fell back to :data:`DEFAULT_INPUT_LIMITS` and read up
        to the default ceiling instead of the requested one. Two call sites
        did exactly that (the generic ``.xml`` route and the ``.abc``
        deferred-score route). As a method there is no default to fall back
        to: reading requires an :class:`InputLimits` in hand, so the policy
        cannot be lost by omission.

        Decoding: a UTF-16 BOM (Notepad's "save as .txt", Finale and some
        Windows XML exporters) selects ``utf-16``, which reads the mark for
        endianness; everything else decodes as ``utf-8-sig``, which strips a
        UTF-8 BOM and otherwise behaves like ``utf-8``. Genuinely
        non-UTF-8/16 bytes still raise :class:`UnicodeDecodeError` — the
        documented contract.

        ``normalize_newlines`` folds CRLF / CR to LF the way a text-mode read
        (universal newlines) does, so a CRLF source reads identically to an LF
        one downstream. XML wants it; the plain / Markdown path deliberately
        keeps the file's own bytes.

        Raises :class:`InputTooLargeError` past ``max_file_bytes``, and
        propagates :class:`OSError` / :class:`FileNotFoundError` as an
        ordinary read would. The *character* ceiling is deliberately not
        applied here: an adapter that resolves the text further (unzipping a
        ``.mxl``, decoding MIDI) must gate the text it finally produces, so
        each caller applies :meth:`check_text_length` to the string it hands
        on.
        """
        raw = self.read_bounded(path)
        if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
            text = raw.decode("utf-16")
        else:
            text = raw.decode("utf-8-sig")
        if normalize_newlines:
            text = text.replace("\r\n", "\n").replace("\r", "\n")
        return text

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
