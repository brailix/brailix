"""THULAC-backed Chinese analyzer adapter.

THULAC (Tsinghua University lexical analysis tool) ships its
segmentation model *inside* the pip package (``thulac/models/``), so
— unlike HanLP — there is no
first-run download: the adapter tokenizes fully offline the moment
``thulac`` is importable. That offline-out-of-the-box behavior, plus
solid segmentation accuracy, is why ``auto`` prefers THULAC as the
default tokenizer.

We build the segmenter with ``seg_only=True`` so only the ~100 MB CWS
model loads. The full seg+POS ``model_c`` is ~390 MB and we don't use
POS — the downstream pinyin path doesn't need it (same reasoning as the
jieba adapter). POS tags are therefore left as ``None``.

THULAC's ``cut`` doesn't return source offsets, so we recover each
word's span by linear search from a moving cursor — the same approach
the HanLP adapter uses. The cursor advances past every match, so
repeated words (e.g. "很好，很好") still land on the right occurrence,
and THULAC's input cleaning dropping a stray character only shows up as
a ``THULAC_SKIPPED_CHARS`` warning rather than a misaligned span.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from pathlib import Path as _Path
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError
from brailix.frontend.zh.analyzer.adapters._spans import recover_spans_by_cursor
from brailix.frontend.zh.tokens import ChineseToken

if _TYPE_CHECKING:
    from collections.abc import Callable
    from typing import Any


@_dataclass(slots=True)
class ThulacChineseAnalyzer:
    """Wraps a THULAC ``seg_only`` segmenter.

    ``cut_fn`` takes a string and returns THULAC's ``text=False`` shape:
    an iterable of ``[word, tag]`` pairs (the tag is empty in seg-only
    mode). It's injectable so tests can exercise the span-recovery logic
    without loading the ~100 MB model. The real one is plugged in by
    :func:`_load`.
    """

    name: str = "thulac"
    cut_fn: Callable[[str], Any] = _field(default=None)  # type: ignore[assignment]

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[ChineseToken]:
        if not text:
            return []
        # THULAC gives no offsets and seg-only mode has no POS — recover
        # spans by cursor search (shared with HanLP). skip_blank drops the
        # per-line ``['\n', '']`` markers / whitespace-only tokens.
        pairs = ((pair[0], None) for pair in self.cut_fn(text))
        return recover_spans_by_cursor(
            pairs,
            text,
            ctx,
            code_prefix="THULAC",
            source="zh.thulac",
            engine="THULAC",
            skip_blank=True,
        )


# THULAC's seg_only decoder loads these two binary models from the
# package's ``models/`` dir. They ship inside the wheel, but a missing
# model is a real failure mode: Nuitka's --include-package-data skips
# ``.bin`` files (the portable build has to name them explicitly), and an
# AV engine can quarantine a ``.bin``. Either way, without a pre-check the
# absence surfaces as a ``FileNotFoundError`` raised deep inside thulac at
# first-tokenize time — which the ``auto`` chain can't catch, so the whole
# translation crashes instead of falling back to the next tokenizer.
_CWS_MODEL_FILES: tuple[str, ...] = ("cws_model.bin", "cws_dat.bin")


def _ensure_cws_models_present(models_dir: _Path) -> None:
    """Raise :class:`MissingExtraError` if a CWS seg model is absent/empty.

    ``MissingExtraError`` (rather than thulac's own ``FileNotFoundError``)
    is deliberate: the ``auto`` resolver catches it and falls back to the
    next tokenizer (jieba → char), and its "reinstall the extra" remedy is
    correct — thulac bundles these models in the wheel, so reinstalling
    restores them. A present-but-empty file (half-written download / a
    truncated AV restore) is treated as missing.
    """
    for name in _CWS_MODEL_FILES:
        path = models_dir / name
        if not path.is_file() or path.stat().st_size == 0:
            raise MissingExtraError(
                adapter="thulac",
                extra="thulac",
                hint=(
                    f"THULAC segmentation model {name} is missing or empty "
                    f"(expected at {path}); it ships with the thulac package, "
                    "so reinstalling restores it."
                ),
            )


def _load() -> ThulacChineseAnalyzer:
    """Lazy-import THULAC and build a seg-only segmenter."""
    import thulac  # noqa: WPS433 — lazy by design

    # An import that succeeds does not mean the package is there. A directory
    # named ``thulac`` with no code in it imports fine as a **namespace
    # package**, and one exists on exactly the installs this matters for: an
    # application bundle that stops shipping the engine replaces its
    # executable but leaves the engine's data directory behind, and the
    # application's own directory is the first entry on ``sys.path``. The
    # package then has ``__file__ is None``, and the model lookup below —
    # which locates ``models/`` relative to it — raised ``TypeError: argument
    # should be a str or an os.PathLike object ... not 'NoneType'``. That is
    # not one of the errors the ``auto`` chain skips, so instead of falling
    # through to the next tokenizer it came out of ``tokenize``, out of
    # ``translate_block``, and failed **every block of every document** —
    # a whole document blank, with an error naming a path type rather than a
    # missing engine.
    #
    # So the shape is answered here, where it can still be named: no
    # ``__file__`` means the code is not installed, which is exactly what
    # MissingExtraError says and what ``auto`` knows to skip.
    package_file = getattr(thulac, "__file__", None)
    if package_file is None:
        raise MissingExtraError(
            adapter="thulac",
            extra="thulac",
            hint=(
                "a directory named 'thulac' is importable but contains no "
                "code (a namespace package) — usually the data directory an "
                "older install left behind. Delete it, or reinstall the "
                "extra to restore the package."
            ),
        )

    # Verify the seg models are on disk before constructing the segmenter,
    # so a missing/quarantined model degrades to the next ``auto`` candidate
    # instead of a FileNotFoundError raised mid-tokenize (which ``auto``
    # can't catch). thulac.__file__ points at the package's __init__.py.
    _ensure_cws_models_present(_Path(package_file).parent / "models")

    # ``seg_only=True`` loads only the CWS model (no POS), which is both
    # smaller and faster and is all the pinyin path needs.
    segmenter = thulac.thulac(seg_only=True)

    def cut_fn(text: str) -> Any:
        # ``text=False`` → list of ``[word, tag]`` pairs (tag is '' in
        # seg-only mode) rather than a single space-joined string.
        return segmenter.cut(text, text=False)

    return ThulacChineseAnalyzer(cut_fn=cut_fn)
