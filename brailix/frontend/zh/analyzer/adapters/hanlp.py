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
from importlib import metadata as _metadata
from pathlib import Path
from typing import Any

from brailix.core.context import FrontendContext
from brailix.core.errors import IncompatibleDependencyError, ModelNotInstalledError
from brailix.core.models.asset_registry import (
    ModelAsset,
    is_managed_download,
    register_asset,
)
from brailix.core.models.paths import get_model_dir
from brailix.frontend.zh.analyzer.adapters._spans import recover_spans_by_cursor
from brailix.ir.inline import ChineseToken

# Pinned MTL model id. Update _MTL_DIR in lockstep when bumping the
# constant to a newer HanLP release — the directory name is the URL's
# zip stem and changes with each model revision (see HanLP's
# ``hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH``).
_MTL_DIR = Path("mtl") / "close_tok_pos_ner_srl_dep_sdp_con_electra_small_20210111_124159"
_MODEL_ID = "hanlp_mtl_electra_small_zh"

# HanLP calls ``BertTokenizer.encode_plus`` at inference time, and
# transformers 5.0 removed it (verified 2026-07: 4.57.6 has the method,
# 5.14.0 does not) — so with transformers >= 5 installed, ``hanlp.load``
# succeeds and the crash (AttributeError, which PROGRAMMING_ERRORS
# deliberately propagates) only fires deep inside the first ``analyze``.
# hanlp itself (2.1.3) declares no upper bound on its core dependency, so a
# fresh ``pip install hanlp`` resolves this broken pairing; only its own
# ``full`` / ``tf`` extras publish the verified bound ``transformers<4.55``,
# which our install hint (and the ``hanlp`` extra in pyproject.toml) mirrors.
# The guard rejects only the *proven* breakage (>= 5): 4.55–4.x is merely
# outside upstream's tested range and may well work, and blocking a working
# install would be worse than allowing it. Drop the guard when a HanLP
# release supports transformers 5 (and relax the pyproject bound with it).
_TRANSFORMERS_INCOMPATIBLE_MAJOR = 5
_TRANSFORMERS_REQUIREMENT_HINT = "<4.55"


def _check_transformers_compatibility() -> None:
    """Raise :class:`IncompatibleDependencyError` for a known-broken
    ``transformers`` install.

    Runs before ``import hanlp`` so the ``auto`` chain skips hanlp (and an
    explicit ``analyzer="hanlp"`` gets an actionable message) instead of
    selecting an engine that AttributeErrors on first use. Reads package
    metadata only — nothing heavy is imported. Fires only when hanlp itself
    is installed: a missing hanlp must keep surfacing as MissingExtraError.
    """
    try:
        _metadata.version("hanlp")
    except _metadata.PackageNotFoundError:
        # hanlp isn't installed — fall through to ``import hanlp`` below,
        # whose ImportError the registry rewrites into MissingExtraError.
        return
    try:
        installed = _metadata.version("transformers")
    except _metadata.PackageNotFoundError:
        # hanlp without transformers is a broken install of hanlp itself;
        # its own import error is the more accurate diagnostic.
        return
    try:
        major = int(installed.partition(".")[0])
    except ValueError:
        return  # unrecognized version scheme — don't block on a guess
    if major >= _TRANSFORMERS_INCOMPATIBLE_MAJOR:
        raise IncompatibleDependencyError(
            "hanlp",
            dependency="transformers",
            installed=installed,
            requirement=_TRANSFORMERS_REQUIREMENT_HINT,
            reason=(
                "transformers 5.0 removed BertTokenizer.encode_plus, "
                "which HanLP still calls at inference time"
            ),
        )


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
    # HanLP gives no offsets — recover spans by cursor search (shared with
    # THULAC). Pair each word with its POS tag (positional, tolerant of a
    # missing / short tags list).
    pairs = (
        (w, tags[idx] if tags and idx < len(tags) else None)
        for idx, w in enumerate(words)
    )
    return recover_spans_by_cursor(
        pairs, text, ctx, code_prefix="HANLP", source="zh.hanlp", engine="HanLP"
    )


def _load() -> HanLPChineseAnalyzer:
    """Lazy-import HanLP and build a default tok+pos pipeline.

    Order matters: ``HANLP_HOME`` must be in the environment *before*
    ``import hanlp`` so the library's ``hanlp_home_default()`` reads
    our value rather than caching the OS default. By default HanLP then
    auto-downloads the model on first use; under managed download (a
    front-end opted in via ``set_managed_download``) we instead pre-check
    and raise so that front-end's downloader handles it.
    """
    _check_transformers_compatibility()

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

    # Standalone (non-managed) path: HanLP auto-downloads the model here on
    # first use. A download / IO failure (no network, a truncated or corrupt
    # archive, a read-only models dir) raises deep inside hanlp.load as
    # OSError / RuntimeError / zipfile.BadZipFile — none of which the ``auto``
    # chain catches, so the whole translation would crash instead of degrading
    # to the next tokenizer. Re-raise as ModelNotInstalledError (the model
    # genuinely isn't usable) so ``auto`` falls back to jieba → char, exactly
    # as the managed-download pre-check above already does.
    try:
        pipeline = hanlp.load(
            hanlp.pretrained.mtl.CLOSE_TOK_POS_NER_SRL_DEP_SDP_CON_ELECTRA_SMALL_ZH
        )
    except Exception as exc:  # noqa: BLE001 — any load failure ⇒ candidate unavailable
        raise ModelNotInstalledError(
            model_id=_MODEL_ID, install_dir=hanlp_home / _MTL_DIR
        ) from exc
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
