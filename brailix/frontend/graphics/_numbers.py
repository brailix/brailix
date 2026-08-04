"""One answer to "can this number be drawn?", for the graphics frontend.

Every graphic source that is a *spec* rather than a document — the geometry
primitives and the figure specs built on top of them — arrives as decoded
JSON, and JSON can carry ``NaN`` and ``Infinity``: Python's decoder accepts
both literals by default, so this is not a code-only hazard any more than it
is for :mod:`brailix.core.measure`.

Two modules read those numbers, and each had its own three-line conversion
that asked only "does ``float()`` accept it?".  Neither rejected a non-finite
one, so ``{"cx": Infinity}`` reached the SVG as the literal attribute
``cx="inf"`` — well-formed XML that is not a coordinate — and ``inf`` in a
figure's ``max`` reached ``round()`` inside the tick generator as an
``OverflowError``, thrown from the middle of a drawing routine.  The tactile
backend defends itself against both further downstream, which is why the
symptom was a blank page rather than a crash; the cost is that the diagnosis
arrives from the rasterizer, naming a coordinate the author never wrote.

So the *finiteness* judgement lives here, once, and the two callers keep their
own **policies** — the same split :mod:`brailix.core.measure` makes:

* a spec field converts with :func:`as_finite` and falls back to the caller's
  default, because a figure with one unreadable field is still a figure;
* a whole spec is scanned with :func:`non_finite_paths` before anything is
  drawn from it, because a shape placed at infinity is not a shape, and the
  author is better told which field than shown a blank page.

What deliberately does **not** move here is
:func:`~brailix.core.measure.as_positive_finite`.  That one validates a
*physical measurement* — a page's dpi and millimetre size — where zero and
negative are as meaningless as ``NaN``.  These are *logical coordinates*: a
point at ``x = -40`` mm is ordinary, and so is ``0``.  The two share the word
"finite" and nothing else, and folding them together would mean one function
with a flag deciding which of two unrelated questions it is being asked.
"""

from __future__ import annotations

import math as _math
from typing import TYPE_CHECKING as _TYPE_CHECKING

if _TYPE_CHECKING:
    from typing import Any


def as_finite(value: Any, default: float | None = 0.0) -> float | None:
    """``value`` as a finite ``float``, or ``default`` if it is not one.

    ``bool`` is refused for the reason it is refused in
    :func:`~brailix.core.measure.as_positive_finite`: it is an ``int``
    subclass, so ``{"width": true}`` would otherwise silently draw a canvas
    one unit wide rather than fall back to the default the caller passed.

    ``default`` is returned, not raised, because a spec is data: a field that
    cannot be read is a field the generator fills in for itself, and the
    caller that wants to *report* the bad value asks
    :func:`non_finite_paths` instead. Pass ``default=None`` to tell the two
    outcomes apart.
    """
    if isinstance(value, bool):
        return default
    try:
        num = float(value)
    except (TypeError, ValueError):
        return default
    return num if _math.isfinite(num) else default


def non_finite_paths(spec: Any, *, limit: int = 5) -> list[str]:
    """Where a decoded spec carries ``inf`` / ``NaN``, as ``field=value``.

    Paths rather than a bare "this spec is invalid", because the author of a
    figure is working without a canvas: ``data[3].value=inf`` says which
    number to go and fix, and "the figure could not be drawn" does not. At
    most ``limit`` of them — a spec with a thousand bad values has one
    mistake, not a thousand, and the message is read aloud.

    Iterative rather than recursive: the argument is whatever ``json.loads``
    returned, so its nesting depth is the *input's* to choose, and a recursive
    walk would answer a hostile spec with a ``RecursionError`` from a function
    whose whole job is to keep hostile specs out of the drawing code.
    """
    found: list[str] = []
    stack: list[tuple[Any, str]] = [(spec, "")]
    while stack:
        node, path = stack.pop()
        # ``bool`` before ``float``: ``isinstance(True, float)`` is False, but
        # keeping the two checks in this order documents that booleans are a
        # deliberate non-answer here rather than an oversight.
        if isinstance(node, bool):
            continue
        if isinstance(node, float):
            if not _math.isfinite(node):
                found.append(f"{path or '<spec>'}={node}")
            continue
        if isinstance(node, dict):
            stack.extend(
                (child, f"{path}.{key}" if path else str(key))
                for key, child in node.items()
            )
        elif isinstance(node, (list, tuple)):
            stack.extend(
                (child, f"{path}[{i}]") for i, child in enumerate(node)
            )
    # Sorted, not in traversal order: the stack visits siblings back to front,
    # and a diagnostic that names a different field each run for the same spec
    # is one nobody can quote in a bug report.
    return sorted(found)[:limit]
