"""Lazy tag → handler dispatch shared by the tree-walking backends.

Both the math (MathML) and music (MusicXML) backends translate a
normalized ``xml.etree.ElementTree.Element`` tree by dispatching each
element to a tag-specific handler.  The handler table lives in each
subsystem's ``handlers`` package and can't be imported at
dispatcher-module load time — handlers need the dispatcher's
``_emit_element`` for recursive descent — so both resolve the table
lazily on first use.

This helper captures *only* that lazy-resolution plumbing.  Each
subsystem keeps its own context type, its own handler table, and its
own ``_emit_element`` (math wraps a ``data-bk-span`` override around the
call; music doesn't), so the two backends stay independently
replaceable — only the cache boilerplate is shared, not the rules.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

# A handler takes ``(cells, context, element)`` and appends cells in
# place.  Kept loose (``...``) because each subsystem's context type
# differs; the concrete signature is enforced inside each handler table.
Handler = Callable[..., None]

_Loader = Callable[[], tuple[Mapping[str, Handler], Handler]]


class LazyTagDispatcher:
    """Resolve ``elem.tag`` to a handler, loading the table on first use.

    ``loader`` returns ``(table, fallback)``: ``table`` maps a tag
    string to its handler, ``fallback`` handles unknown tags.  It is
    invoked at most once (the result is cached) and only when the first
    element is dispatched — by which point the handlers module has
    finished importing, breaking the dispatcher ↔ handlers cycle.
    """

    __slots__ = ("_loader", "_table", "_fallback")

    def __init__(self, loader: _Loader) -> None:
        self._loader = loader
        self._table: Mapping[str, Handler] | None = None
        self._fallback: Handler | None = None

    def resolve(self, tag: str) -> Handler:
        if self._table is None:
            self._table, self._fallback = self._loader()
        assert self._fallback is not None  # set together with _table
        return self._table.get(tag, self._fallback)
