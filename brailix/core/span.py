"""Source-position tracking for IR nodes.

Every IR node may carry a Span back to the original input text, so that
the renderer can produce proofreading metadata mapping each braille cell
back to its source characters.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from collections.abc import Iterable
    from typing import Any


def _reject_non_int_offsets(start: Any, end: Any) -> None:
    """Raise unless both offsets are genuine (non-``bool``) integers.

    Split out of :meth:`Span.__post_init__` so the path that always holds
    costs two ``type(...) is int`` identity tests and nothing else: a Span is
    built for every token, every inline node and every braille cell of every
    compile, so the check has to be effectively free in the case where it
    passes. Only a value that fails the fast test reaches this function, which
    then decides whether it is a legitimate ``int`` subclass or a rejection.

    An ``int`` subclass that is not ``bool`` (an :class:`enum.IntEnum`, a numpy
    integer) is accepted, matching what ``isinstance`` has always allowed at
    the deserialization boundary. ``bool`` is refused despite being one, for
    the same reason :func:`brailix.ir._serde.check_wire_value` refuses it:
    ``Span(True, True)`` would silently become ``Span(1, 1)`` and point the
    cell↔source map at a character the caller never named.
    """
    for value in (start, end):
        if not isinstance(value, int) or isinstance(value, bool):
            raise ValueError(
                f"span offsets must be integers, not {type(value).__name__}; "
                f"got start={start!r} end={end!r}"
            )


@_dataclass(frozen=True, slots=True)
class Span:
    """Half-open character range ``[start, end)`` into a source string.

    ``start`` and ``end`` are zero-based code-point offsets. ``end`` may
    equal ``start`` to denote an insertion point.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
        # Type first, order second, and BOTH on every construction path. The
        # genuine-int rule used to live in :meth:`from_tuple` alone, so the
        # deserialization boundary and the direct constructor promised
        # different things about the same class: ``Span.from_tuple([1.5, 2.5])``
        # raised while ``Span(1.5, 2.5)`` was built and travelled on into
        # slicing, merging, shifting and proofreading jumps, and ``Span(True,
        # True)`` quietly became offset 1. Span is the compiler's provenance
        # currency — one contract, wherever a span comes from.
        if type(self.start) is not int or type(self.end) is not int:
            _reject_non_int_offsets(self.start, self.end)
        if self.start < 0 or self.end < self.start:
            raise ValueError(f"invalid span: start={self.start} end={self.end}")

    @property
    def length(self) -> int:
        return self.end - self.start

    def is_empty(self) -> bool:
        return self.start == self.end

    def contains(self, other: Span) -> bool:
        """True if ``other`` lies entirely within this span."""
        return self.start <= other.start and other.end <= self.end

    def overlaps(self, other: Span) -> bool:
        return self.start < other.end and other.start < self.end

    def merge(self, other: Span) -> Span:
        """Return the smallest span containing both."""
        return Span(min(self.start, other.start), max(self.end, other.end))

    def shift(self, offset: int) -> Span:
        return Span(self.start + offset, self.end + offset)

    def to_tuple(self) -> tuple[int, int]:
        return (self.start, self.end)

    @classmethod
    def from_tuple(cls, value: Any) -> Span:
        """Build a Span from a 2-element ``[start, end]`` sequence — the shape
        a span round-trips as, whether in JSON (a list) or in memory (a tuple).

        Raises :class:`ValueError` on any other shape (wrong length, not a
        sequence, non-integer elements) so a malformed payload fails loudly at
        the IR boundary instead of silently smuggling a non-Span into a
        ``span`` field. Offsets must be genuine ``int``\\ s: a ``float`` like
        ``3.9`` is rejected rather than truncated to ``3`` (which would point
        the cell↔source map at the wrong character), and ``bool`` (an ``int``
        subclass) is rejected too. This is the single canonical JSON-to-Span
        entry point; the IR deserializers route every span through it.

        Only the *shape* is checked here. The offsets themselves are the
        constructor's business (:meth:`__post_init__`), which enforces the same
        genuine-int rule for a hand-built span — so this method cannot be
        stricter than the class, which is exactly how the two contracts drifted
        apart while the rule was written out only here.
        """
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"span must be a 2-element sequence; got {value!r}")
        start, end = value
        return cls(start, end)


def merge_spans(spans: Iterable[Span]) -> Span | None:
    """Return the bounding span of an iterable, or None if empty."""
    it = iter(spans)
    try:
        acc = next(it)
    except StopIteration:
        return None
    for s in it:
        acc = acc.merge(s)
    return acc
