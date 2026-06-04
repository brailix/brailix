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

from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError
from brailix.core.span import Span
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class ThulacChineseAnalyzer:
    """Wraps a THULAC ``seg_only`` segmenter.

    ``cut_fn`` takes a string and returns THULAC's ``text=False`` shape:
    an iterable of ``[word, tag]`` pairs (the tag is empty in seg-only
    mode). It's injectable so tests can exercise the span-recovery logic
    without loading the ~100 MB model. The real one is plugged in by
    :func:`_load`.
    """

    name: str = "thulac"
    cut_fn: Callable[[str], Any] = field(default=None)  # type: ignore[assignment]

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[ChineseToken]:
        if not text:
            return []
        tokens: list[ChineseToken] = []
        cursor = 0
        for pair in self.cut_fn(text):
            word = pair[0]
            # THULAC inserts per-line ``['\n', '']`` markers and can emit
            # whitespace-only tokens; neither is real content, and a ''
            # word would make ``find`` match at the cursor and stall.
            if not word or word.isspace():
                continue
            # Locate the word from the cursor. THULAC gives us no spans,
            # so we recover them by linear search — the cursor advances
            # past each match so repeats resolve to the right occurrence.
            start = text.find(word, cursor)
            if start < 0:
                # THULAC normalised the word (its preprocessor may fold
                # some characters) or it isn't in the remaining text.
                # Use the cursor as a synthetic span so we don't crash,
                # but warn: the surface↔source mapping is unreliable here.
                start = cursor
                if ctx is not None:
                    ctx.warnings.warn(
                        code="THULAC_WORD_NOT_IN_TEXT",
                        message=(
                            f"THULAC returned word {word!r} not found in "
                            f"source at cursor {cursor}"
                        ),
                        surface=word,
                        span=Span(start, start + len(word)),
                        source="zh.thulac",
                    )
            elif start > cursor and ctx is not None:
                # Characters between cursor and start aren't claimed by
                # any token — THULAC's input cleaning dropped them.
                # Proofreading needs to know about the gap.
                ctx.warnings.warn(
                    code="THULAC_SKIPPED_CHARS",
                    message=(
                        f"THULAC skipped {start - cursor} char(s) before "
                        f"word {word!r}; gap text: {text[cursor:start]!r}"
                    ),
                    surface=text[cursor:start],
                    span=Span(cursor, start),
                    source="zh.thulac",
                )
            end = start + len(word)
            tokens.append(
                ChineseToken(
                    surface=word,
                    pos=None,
                    span=Span(start, end),
                )
            )
            cursor = end
        return tokens


# THULAC's seg_only decoder loads these two binary models from the
# package's ``models/`` dir. They ship inside the wheel, but a missing
# model is a real failure mode: Nuitka's --include-package-data skips
# ``.bin`` files (the portable build has to name them explicitly), and an
# AV engine can quarantine a ``.bin``. Either way, without a pre-check the
# absence surfaces as a ``FileNotFoundError`` raised deep inside thulac at
# first-tokenize time — which the ``auto`` chain can't catch, so the whole
# translation crashes instead of falling back to the next tokenizer.
_CWS_MODEL_FILES: tuple[str, ...] = ("cws_model.bin", "cws_dat.bin")


def _ensure_cws_models_present(models_dir: Path) -> None:
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

    # Verify the seg models are on disk before constructing the segmenter,
    # so a missing/quarantined model degrades to the next ``auto`` candidate
    # instead of a FileNotFoundError raised mid-tokenize (which ``auto``
    # can't catch). thulac.__file__ points at the package's __init__.py.
    _ensure_cws_models_present(Path(thulac.__file__).parent / "models")

    # ``seg_only=True`` loads only the CWS model (no POS), which is both
    # smaller and faster and is all the pinyin path needs.
    segmenter = thulac.thulac(seg_only=True)

    def cut_fn(text: str) -> Any:
        # ``text=False`` → list of ``[word, tag]`` pairs (tag is '' in
        # seg-only mode) rather than a single space-joined string.
        return segmenter.cut(text, text=False)

    return ThulacChineseAnalyzer(cut_fn=cut_fn)
