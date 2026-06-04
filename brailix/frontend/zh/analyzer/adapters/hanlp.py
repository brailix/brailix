"""HanLP-backed Chinese analyzer adapter.

HanLP is heavy (downloads large transformer models on first use). We
import it lazily inside :func:`_load` so users who pick a different
adapter never pay the import cost. If the package isn't installed,
the registry surfaces a :class:`MissingExtraError` pointing at the
``hanlp`` pip extra.

This file deliberately does *not* import ``hanlp`` at module top
level — keep it that way, or the lazy-loading contract breaks.

Model cache directory: :func:`_load` sets ``HANLP_HOME`` to the
portable ``models/hanlp/`` before importing hanlp, so weights live
there rather than in ``~/.hanlp/``.  By default HanLP then
auto-downloads the model on first use, like any NLP library.  A
front-end that manages downloads itself can call
:func:`brailix.core.models.set_managed_download`, after which this
adapter raises :class:`ModelNotInstalledError` for an absent model
instead of letting HanLP fetch it.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.errors import ModelNotInstalledError
from brailix.core.models.asset_registry import (
    ModelAsset,
    is_managed_download,
    register_asset,
)
from brailix.core.models.paths import get_model_dir
from brailix.core.span import Span
from brailix.ir.inline import ChineseToken

# Pinned MTL model id. Update _MTL_DIR in lockstep when bumping the
# constant to a newer HanLP release — the directory name is the URL's
# zip stem and changes with each model revision (see HanLP's
# ``hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH``).
_MTL_DIR = Path("mtl") / "close_tok_pos_ner_srl_dep_sdp_con_electra_small_20210111_124159"
_MODEL_ID = "hanlp_mtl_electra_small_zh"


@dataclass(slots=True)
class HanLPChineseAnalyzer:
    """Wraps a HanLP tok/pos pipeline.

    ``pipeline`` is the callable that takes a string and returns a
    document-like object with ``tok/fine`` and ``pos`` task outputs.
    We accept the pipeline as a constructor argument so tests can
    inject a fake without installing HanLP.
    """

    name: str = "hanlp"
    pipeline: Any = field(default=None)

    def analyze(
        self, text: str, ctx: FrontendContext | None = None
    ) -> list[ChineseToken]:
        if not text:
            return []
        doc = self.pipeline(text)
        words = _extract_words(doc)
        tags = _extract_pos(doc)
        return _tokens_from(words, tags, text, ctx)


def _extract_words(doc: Any) -> list[str]:
    """HanLP's MTL pipeline returns either ``doc['tok/fine']`` or
    ``doc['tok']``; older versions may expose ``.tokens``. Be tolerant."""
    for key in ("tok/fine", "tok/coarse", "tok"):
        try:
            value = doc[key]
        except (KeyError, TypeError):
            continue
        if value is None:
            continue
        # value may be list[str] or list[list[str]] depending on input
        return list(value)
    raise ValueError(f"unrecognized HanLP doc shape: {type(doc).__name__}")


def _extract_pos(doc: Any) -> list[str] | None:
    for key in ("pos/ctb", "pos/pku", "pos"):
        try:
            value = doc[key]
        except (KeyError, TypeError):
            continue
        if value is None:
            continue
        return list(value)
    return None


def _tokens_from(
    words: list[str], tags: list[str] | None, text: str, ctx: FrontendContext | None = None
) -> list[ChineseToken]:
    tokens: list[ChineseToken] = []
    cursor = 0
    for idx, w in enumerate(words):
        # Locate the word in ``text`` starting from cursor. HanLP doesn't
        # always give us spans, so we recover them by linear search from
        # cursor — repeated words like "很好，很好" still land on the right
        # occurrence because cursor advances past previous matches.
        start = text.find(w, cursor)
        if start < 0:
            # Defensive: word doesn't appear in the remaining text at all.
            # HanLP normalised it (e.g. full-width → half-width) or invented
            # a token. Use cursor as a synthetic span so we don't crash, but
            # warn so callers know the surface↔source mapping is unreliable
            # for this word.
            start = cursor
            if ctx is not None:
                ctx.warnings.warn(
                    code="HANLP_WORD_NOT_IN_TEXT",
                    message=f"HanLP returned word {w!r} not found in source at cursor {cursor}",
                    surface=w,
                    span=Span(start, start + len(w)),
                    source="zh.hanlp",
                )
        elif start > cursor:
            # Characters between cursor and start aren't claimed by any
            # token. Could be whitespace HanLP stripped, but for proofread
            # accuracy callers should know about it.
            if ctx is not None:
                ctx.warnings.warn(
                    code="HANLP_SKIPPED_CHARS",
                    message=(
                        f"HanLP skipped {start - cursor} char(s) before "
                        f"word {w!r}; gap text: {text[cursor:start]!r}"
                    ),
                    surface=text[cursor:start],
                    span=Span(cursor, start),
                    source="zh.hanlp",
                )
        end = start + len(w)
        pos = tags[idx] if tags and idx < len(tags) else None
        tokens.append(
            ChineseToken(
                surface=w,
                pos=pos,
                span=Span(start, end),
            )
        )
        cursor = end
    return tokens


def _load() -> HanLPChineseAnalyzer:
    """Lazy-import HanLP and build a default tok+pos pipeline.

    Order matters: ``HANLP_HOME`` must be in the environment *before*
    ``import hanlp`` so the library's ``hanlp_home_default()`` reads
    our value rather than caching the OS default. By default HanLP then
    auto-downloads the model on first use; under managed download (a
    front-end opted in via ``set_managed_download``) we instead pre-check
    and raise so that front-end's downloader handles it.
    """
    hanlp_home = get_model_dir("hanlp")
    os.environ["HANLP_HOME"] = str(hanlp_home)

    import hanlp  # noqa: WPS433 — lazy by design

    # Defer to a front-end's download manager only when one has opted in
    # via set_managed_download; otherwise let HanLP auto-download on first
    # use (the standalone path). The check runs *after* the import so a
    # missing hanlp package still surfaces as MissingExtraError (the
    # registry rewrites the ImportError from the line above).
    if is_managed_download():
        _ensure_model_installed(hanlp_home)

    pipeline = hanlp.load(hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH)
    return HanLPChineseAnalyzer(pipeline=pipeline)


def _ensure_model_installed(hanlp_home: Path) -> None:
    """Raise :class:`ModelNotInstalledError` if the MTL model is absent.

    Used under managed download to defer the fetch to a front-end's
    downloader: the check happens before any ``hanlp.load`` call has a
    chance to auto-download. A present-but-empty directory is also
    treated as missing (a half-finished download leaves an empty folder
    that would otherwise fool the existence check).
    """
    install_dir = hanlp_home / _MTL_DIR
    if not install_dir.is_dir() or not any(install_dir.iterdir()):
        raise ModelNotInstalledError(model_id=_MODEL_ID, install_dir=install_dir)


# Register the asset so a model-manager front-end can list / download /
# delete this model without importing the adapter directly.
# Lambda not Path: get_model_dir() has the side-effect of creating
# models/hanlp/, which we don't want to trigger at module import time.
register_asset(
    ModelAsset(
        name=_MODEL_ID,
        display_name_key="model.hanlp_mtl_electra_small_zh.display_name",
        install_dir_factory=lambda: get_model_dir("hanlp") / _MTL_DIR,
    )
)
