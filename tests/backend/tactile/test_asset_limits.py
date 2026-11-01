"""Tests for the graphic-asset read budget — :class:`GraphicAssetLimits`.

The tactile ``<image>`` href used to be an unbounded ``read_bytes()`` on any
path a document cared to name (adversarial review 2026-10-31, red-3 F1 /
blue-2 CONFIRMED P0). These pin the budget that closed it: the two ceilings,
the filesystem switch, the ``unlimited()`` opt-out, and the reading
discipline borrowed from :class:`brailix.input.InputLimits` (fstat on the
open descriptor, one byte past the ceiling, no non-regular files).
"""

from __future__ import annotations

import os
import stat as stat_mod
import sys
from pathlib import Path

import pytest

from brailix.backend.tactile._asset_limits import (
    DEFAULT_GRAPHIC_ASSET_LIMITS,
    GraphicAssetLimits,
    GraphicAssetTooLargeError,
)


class TestConstruction:
    def test_defaults_are_generous_and_filesystem_is_on(self):
        limits = GraphicAssetLimits()
        assert limits == DEFAULT_GRAPHIC_ASSET_LIMITS
        assert limits.max_asset_bytes == 32 * 1024 * 1024
        # The pixel ceiling shares the raster cap's order: a decoded source
        # is only ever downsampled into a device box capped at the same size.
        assert limits.max_asset_pixels == 30_000_000
        assert limits.allow_filesystem is True

    @pytest.mark.parametrize(
        "field", ["max_asset_bytes", "max_asset_pixels"]
    )
    def test_a_ceiling_must_be_a_non_negative_int(self, field):
        with pytest.raises(ValueError, match=field):
            GraphicAssetLimits(**{field: -1})
        with pytest.raises(ValueError, match=field):
            GraphicAssetLimits(**{field: "big"})
        # bool is an int subclass; True would be a one-byte ceiling.
        with pytest.raises(ValueError, match=field):
            GraphicAssetLimits(**{field: True})

    def test_zero_is_a_coherent_policy(self):
        assert GraphicAssetLimits(max_asset_bytes=0).max_asset_bytes == 0

    def test_the_switch_must_be_a_bool(self):
        with pytest.raises(ValueError, match="allow_filesystem"):
            GraphicAssetLimits(allow_filesystem="no")

    def test_frozen(self):
        limits = GraphicAssetLimits()
        with pytest.raises(AttributeError):
            limits.allow_filesystem = False  # type: ignore[misc]

    def test_unlimited_never_rejects_and_reads_the_filesystem(self, tmp_path):
        path = tmp_path / "big.bin"
        path.write_bytes(b"x" * 64)
        limits = GraphicAssetLimits.unlimited()
        assert limits.max_asset_bytes == sys.maxsize
        assert limits.allow_filesystem is True
        assert limits.read_bounded(str(path)) == b"x" * 64


class TestCheckGates:
    def test_check_bytes_at_and_past_the_ceiling(self):
        limits = GraphicAssetLimits(max_asset_bytes=32)
        limits.check_bytes(32)
        with pytest.raises(GraphicAssetTooLargeError) as exc:
            limits.check_bytes(33, detail="data URI payload")
        assert exc.value.kind == "asset_bytes"
        assert (exc.value.actual, exc.value.limit) == (33, 32)

    def test_check_uri_payload_refuses_before_decoding(self):
        # base64 inflates by 4/3, so a payload of 4/3 the budget plus slack
        # cannot fit in any encoding; percent-encoding only shrinks.
        limits = GraphicAssetLimits(max_asset_bytes=32)
        limits.check_uri_payload(32 * 4 // 3 + 8)
        with pytest.raises(GraphicAssetTooLargeError):
            limits.check_uri_payload(32 * 4 // 3 + 9)

    def test_check_pixels_at_and_past_the_ceiling(self):
        limits = GraphicAssetLimits(max_asset_pixels=100)
        limits.check_pixels(10, 10)
        with pytest.raises(GraphicAssetTooLargeError) as exc:
            limits.check_pixels(16, 16)
        assert exc.value.kind == "asset_pixels"
        assert "16x16" in str(exc.value)


class TestReadBounded:
    def test_reads_within_the_ceiling(self, tmp_path: Path) -> None:
        path = tmp_path / "asset.bin"
        path.write_bytes(b"x" * 64)
        assert (
            GraphicAssetLimits(max_asset_bytes=64).read_bounded(str(path))
            == b"x" * 64
        )

    def test_refuses_from_the_fstat_without_loading(self, tmp_path: Path) -> None:
        path = tmp_path / "big.bin"
        path.write_bytes(b"y" * 10_000)
        with pytest.raises(GraphicAssetTooLargeError) as excinfo:
            GraphicAssetLimits(max_asset_bytes=100).read_bounded(str(path))
        assert excinfo.value.actual == 10_000
        assert excinfo.value.limit == 100

    def test_growth_in_the_window_before_the_read_is_measured(
        self, tmp_path: Path, monkeypatch
    ) -> None:
        """The fstat runs on the **open descriptor**, not the path: whatever
        grew / was swapped in since any earlier look is measured as the file
        actually is when the read happens."""
        import pathlib

        target = tmp_path / "doc.bin"
        target.write_bytes(b"ab")
        limits = GraphicAssetLimits(max_asset_bytes=16)

        real_stat = pathlib.Path.stat
        fired = {"n": 0}

        def growing_stat(self, **kwargs):  # noqa: ANN001, ANN202
            result = real_stat(self, **kwargs)
            if self == target and fired["n"] == 0:
                fired["n"] += 1
                # The window: someone looked, the read has not happened.
                target.write_bytes(b"x" * 4096)
            return result

        monkeypatch.setattr(pathlib.Path, "stat", growing_stat)
        with pytest.raises(GraphicAssetTooLargeError) as excinfo:
            # A pre-read look at the path (any earlier stat), then the
            # bounded read — which must see the grown file, not the small
            # one the earlier look described.
            target.stat()
            limits.read_bounded(str(target))
        assert excinfo.value.actual == 4096

    def test_non_regular_files_are_refused(self, tmp_path: Path,
                                           monkeypatch) -> None:
        """``st_size`` is meaningless for a FIFO or a device: a named pipe
        reports 0 and then delivers without end — the size gate reading as
        "empty, go ahead" on the one input it can never bound."""
        path = tmp_path / "pipe"
        path.write_bytes(b"data")

        real_fstat = os.fstat

        def fifo_fstat(fd):  # noqa: ANN001, ANN202
            info = real_fstat(fd)
            fields = list(info)
            # Re-label the mode as a FIFO, keeping everything else.
            fields[0] = (
                info.st_mode & ~stat_mod.S_IFMT(info.st_mode)
            ) | stat_mod.S_IFIFO
            return os.stat_result(fields)

        monkeypatch.setattr(os, "fstat", fifo_fstat)
        with pytest.raises(GraphicAssetTooLargeError, match="not a regular file"):
            GraphicAssetLimits(max_asset_bytes=1000).read_bounded(str(path))

    def test_a_missing_path_raises_oserror_not_toolarge(self, tmp_path: Path) -> None:
        """The gate must not swallow a missing path into a size error — the
        caller classifies failures (and deliberately never displays the
        difference; see _image and the backend's unified warning)."""
        with pytest.raises(OSError):
            GraphicAssetLimits().read_bounded(str(tmp_path / "nope.png"))
