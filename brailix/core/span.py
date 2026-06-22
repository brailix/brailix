"""Source-position tracking for IR nodes.

Every IR node may carry a Span back to the original input text, so that
the renderer can produce proofreading metadata mapping each braille cell
back to its source characters.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class Span:
    """Half-open character range ``[start, end)`` into a source string.

    ``start`` and ``end`` are zero-based code-point offsets. ``end`` may
    equal ``start`` to denote an insertion point.
    """

    start: int
    end: int

    def __post_init__(self) -> None:
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
        """
        if not isinstance(value, (list, tuple)) or len(value) != 2:
            raise ValueError(f"span must be a 2-element sequence; got {value!r}")
        start, end = value
        for v in (start, end):
            if not isinstance(v, int) or isinstance(v, bool):
                raise ValueError(
                    f"span offsets must be integers, not {type(v).__name__}; "
                    f"got {value!r}"
                )
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
