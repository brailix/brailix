"""``parse_graphic_tree`` — the graphics frontend's single public entry.

The graphics counterpart of ``parse_math_tree`` / ``parse_music_tree``:
adapter resolution + ``to_svg`` + normalisation behind one callable, with
the same warning shape (``GRAPHICS_ADAPTER_MISSING`` for an unknown source
or a missing extra) but the graphics vertical's own degrade value — always
an ``<svg>`` tree (an error-marked one on failure), never ``None``, so a
graphic always rasterises to *something*.
"""

from __future__ import annotations

import pytest

from brailix.core.context import GraphicsContext
from brailix.core.errors import (
    RunMode,
    StrictModeError,
    WarningCollector,
)
from brailix.frontend.graphics import parse_graphic_tree
from brailix.frontend.graphics.registry import graphic_source_registry

CIRCLE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10">'
    '<circle cx="5" cy="5" r="4"/></svg>'
)


def _ctx(source: str = "svg") -> GraphicsContext:
    return GraphicsContext(source=source, warnings=WarningCollector())


class TestHappyPath:
    def test_svg_source_parses_to_normalized_tree(self) -> None:
        tree = parse_graphic_tree(CIRCLE, _ctx())
        assert tree.tag == "svg"  # namespace stripped
        (child,) = list(tree)
        assert child.tag == "circle"
        # The normalizer stamps stable element ids for provenance.
        assert tree.get("data-bk-gid") is not None

    def test_bytes_go_to_the_adapter_as_is(self) -> None:
        tree = parse_graphic_tree(CIRCLE.encode("utf-8"), _ctx())
        assert tree.tag == "svg"
        assert tree.get("data-bk-error") is None


class TestAdapterMissing:
    def test_unknown_source_warns_and_degrades_to_error_tree(self) -> None:
        warn = WarningCollector()
        ctx = GraphicsContext(source="does_not_exist", warnings=warn)
        tree = parse_graphic_tree("<svg/>", ctx)
        assert tree is not None and tree.tag == "svg"
        assert tree.get("data-bk-error")
        missing = [w for w in warn if w.code == "GRAPHICS_ADAPTER_MISSING"]
        assert missing
        # The warning names the registered sources so the fix is guessable.
        assert missing[0].candidates

    def test_missing_extra_warns_and_degrades_to_error_tree(self) -> None:
        # A loader whose import fails, registered with an ``extra=`` hint,
        # surfaces as MissingExtraError at get() time — the real path a
        # Pillow-less install hits for the ``image`` source.
        def _loader() -> object:
            raise ImportError("no such dependency")

        with graphic_source_registry.overriding(
            "_test_missing_extra", _loader, extra="graphics"
        ):
            warn = WarningCollector()
            tree = parse_graphic_tree(
                "spec",
                GraphicsContext(source="_test_missing_extra", warnings=warn),
            )
            assert tree.get("data-bk-error")
            missing = [
                w for w in warn if w.code == "GRAPHICS_ADAPTER_MISSING"
            ]
            assert missing
            # The message carries the pip-extra fix, straight from
            # MissingExtraError.
            assert "graphics" in missing[0].message

    def test_strict_mode_raises_on_missing_adapter(self) -> None:
        warn = WarningCollector(mode=RunMode.STRICT)
        ctx = GraphicsContext(source="does_not_exist", warnings=warn)
        with pytest.raises(StrictModeError):
            parse_graphic_tree("<svg/>", ctx)


class TestAdapterFailureBackstop:
    def test_raising_adapter_degrades_to_error_tree(self) -> None:
        class _Exploding:
            source = "_test_exploding"

            def to_svg(self, src, ctx=None):  # noqa: ANN001, ANN201
                raise RuntimeError("third-party adapter bug")

        with graphic_source_registry.overriding(
            "_test_exploding", lambda: _Exploding()
        ):
            tree = parse_graphic_tree(CIRCLE, _ctx("_test_exploding"))
            assert tree.tag == "svg"
            assert "adapter failure" in (tree.get("data-bk-error") or "")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
