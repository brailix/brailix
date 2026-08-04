"""g2pW-backed pinyin resolver.

g2pW is the deep-learning polyphone disambiguator from Yang et al.
We import it lazily inside :func:`_load`. The wrapper accepts an
injected predictor for testability.

Low-confidence readings emit a ``LOW_CONFIDENCE_PINYIN`` warning so
human proofreaders can review them.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from dataclasses import field as _field
from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.context import FrontendContext
from brailix.core.errors import PROGRAMMING_ERRORS, MissingExtraError
from brailix.frontend.zh.pinyin.adapters._align import resolve_by_char_alignment
from brailix.frontend.zh.tokens import ChineseToken

if _TYPE_CHECKING:
    from typing import Any

LOW_CONFIDENCE_THRESHOLD = 0.75


@_dataclass(slots=True)
class G2pwPinyinResolver:
    """Wraps a g2pW predictor.

    ``predictor`` is g2pW's own ``G2PWConverter``, which is a **batch** API:
    it takes a *list* of sentences and returns a list holding one result per
    sentence, each a list of numeric-tone syllables — one entry per
    *character* of that sentence, ``None`` where it has no reading for one.
    """

    name: str = "g2pw"
    predictor: Any = _field(default=None)
    low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        if not tokens:
            return []
        sentence = "".join(t.surface for t in tokens)
        pinyins, confidences = _normalize_predictor_output(
            self.predictor([sentence])
        )
        return resolve_by_char_alignment(
            tokens,
            pinyins,
            ctx,
            source="pinyin.g2pw",
            engine="g2pW",
            confidences=confidences,
            low_confidence_threshold=self.low_confidence_threshold,
        )


def _normalize_predictor_output(value: Any) -> tuple[list[str], list[float] | None]:
    """One sentence's syllables (and confidences, if any) from what the
    predictor returned.

    ``G2PWConverter`` batches: ``conv(["我在重庆"])`` returns
    ``[["wo3", "zai4", "chong2", "qing4"]]`` — the syllables sit one level in.
    Reading that outer list as the syllables is what this used to do, and it
    made ``len(syllables)`` **1 for every input**, so the per-character
    alignment in :func:`resolve_by_char_alignment` saw a length divergence
    every single time and cleared every reading. The result was braille
    byte-for-byte identical to ``resolver="null"`` — blank cells, one
    ``PINYIN_LENGTH_MISMATCH`` warning nobody was reading, and no other sign
    that the engine had contributed nothing at all.

    The optional ``(syllables, confidences)`` tuple form is for an injected
    predictor only. The shipped ``G2PWConverter`` *computes* per-character
    confidences and then drops them — its ``__call__`` returns the predictions
    alone — so nothing that ships reaches the low-confidence path below.
    """
    confidences: list[float] | None = None
    if isinstance(value, tuple) and len(value) == 2:
        value, confs = value
        if confs is not None:
            confidences = list(_one_sentence(confs))
    return list(_one_sentence(value)), confidences


def _one_sentence(batched: Any) -> Any:
    """The single sentence's entries out of a per-sentence batch.

    Tolerates an already-flat sequence — an injected predictor may hand one
    over — by looking at what the first entry *is*: a syllable is a string (or
    ``None`` for a character with no reading), a batched sentence is a
    sequence. Deciding on the shape rather than assuming one is what keeps a
    flat list from being read as a batch, which would silently take its first
    syllable apart character by character.
    """
    batch = list(batched)
    if not batch:
        return []
    first = batch[0]
    if first is None or isinstance(first, (str, float, int)):
        return batch
    return list(first)


def _load() -> G2pwPinyinResolver:
    import g2pw  # noqa: WPS433 — lazy by design

    try:
        # ``style="pinyin"`` is not the default — ``G2PWConverter`` defaults to
        # ``"bopomofo"`` and returns 注音 (``ㄨㄛ3``), which is a perfectly good
        # reading and completely unusable here: every downstream consumer of
        # ``ChineseToken.pinyin`` (the backend's syllable parser, the tone
        # rules, the user dictionary) reads numeric-tone Hanyu Pinyin. Asking
        # for it explicitly is one word; converting 注音 afterwards would be a
        # second transcription table to keep correct.
        #
        predictor = g2pw.G2PWConverter(style="pinyin")
    except PROGRAMMING_ERRORS:
        # Ahead of the broad catch below: that catch raises a
        # candidate-unavailable signal, which ``auto`` answers by silently
        # resolving to pypinyin / null. A code defect (ours, or an upstream API
        # that moved) would therefore change the readings this document is
        # translated with while the compile still reported success. Same ladder
        # as the math / music / graphics soft-failure boundaries
        # (brailix.core.errors.PROGRAMMING_ERRORS).
        raise
    except Exception as e:  # noqa: BLE001
        # G2PWConverter downloads its model on first construction; a network /
        # IO failure raises URLError / OSError / BadZipFile / RuntimeError, none
        # of them the ImportError the registry maps to MissingExtraError. Raise
        # MissingExtraError (the same convention thulac uses for a missing
        # model) so the ``auto`` chain catches it and degrades to
        # pypinyin / null instead of crashing the whole translation.
        raise MissingExtraError(
            adapter="g2pw",
            extra="g2pw",
            hint=(
                "the g2pW model could not be loaded (download / IO failure); "
                "install with pip install brailix[g2pw] and ensure the model "
                "can be fetched on first use."
            ),
        ) from e
    # Run inference in-process. ``num_workers`` on the converter is a torch
    # ``DataLoader`` worker *process* count, and the model config ships ``2``:
    # on Windows (no fork) each worker starts by re-importing the parent's
    # ``__main__``, which in a frozen desktop build means re-executing the
    # application's own ``.exe``, and in a plain script means a
    # ``freeze_support`` error or an outright hang. Both were reproduced.
    # A paragraph of prose is a batch of one, so there is no work here for a
    # pool to divide — only a way for the process model to go wrong.
    #
    # Set after construction because the constructor cannot express it:
    # ``self.num_workers = num_workers if num_workers else config.num_workers``
    # reads 0 as "unset" and restores the config's 2. Assigning the attribute
    # is the same knob without the falsy-zero hole, and ``__call__`` reads it
    # when it builds the DataLoader.
    predictor.num_workers = 0
    return G2pwPinyinResolver(predictor=predictor)
