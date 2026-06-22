"""g2pM-backed pinyin resolver.

g2pM (Park & Kim, 2019 — "g2pM: A Neural Grapheme-to-Phoneme Conversion
Package for Mandarin Chinese") is a polyphone disambiguator whose
bi-LSTM weights ship *inside* the pip package as bundled numpy ``.pkl``
files. So — unlike g2pW — there is no model download: it disambiguates
polyphones fully offline the moment ``g2pM`` imports, on numpy alone (no
torch/TF). That offline, context-aware behavior is why ``auto`` prefers
it as the default pinyin source.

Two output-format quirks are normalized in :func:`_load`'s converter so
the syllables match what
:func:`brailix.backend.zh.pinyin_parser.parse_pinyin` expects:

* g2pM spells ü as ``u:`` (``nu:3`` = 女, ``lu:e4`` = 略). The parser
  only knows ``ü`` / ``v``, so we rewrite ``u:`` → ``ü``; without this
  the backend's finals lookup misses and the syllable mistranslates.
* the neutral (light) tone is already ``5`` (``de5``), which the parser
  treats as tone 5 — no change needed.

We call g2pM with ``char_split=True`` so it returns exactly one syllable
per input character. The ``char_split=False`` path concatenates
single-character (non-Chinese) tokens into their neighbours, which would
desync the by-cursor slicing below.

Like pypinyin, g2pM exposes no confidence scores, so ``confidence`` is
always ``None`` (the ``LOW_CONFIDENCE_PINYIN`` warning is a g2pW-only
feature).
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError
from brailix.frontend.zh.pinyin.adapters._align import resolve_by_char_alignment
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class G2pmPinyinResolver:
    """Wraps a g2pM model.

    ``converter`` takes a str and returns a list[str] of one
    numeric-tone syllable per character. It's injectable for testing
    so the alignment logic can be checked without loading the model;
    the real converter (built in :func:`_load`) also applies the
    ``u:`` → ``ü`` normalization.
    """

    name: str = "g2pm"
    converter: Callable[[str], list[str]] = field(default=None)  # type: ignore[assignment]

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        if not tokens:
            return []
        sentence = "".join(t.surface for t in tokens)
        syllables = list(self.converter(sentence))
        return resolve_by_char_alignment(
            tokens,
            syllables,
            ctx,
            source="pinyin.g2pm",
            engine="g2pM",
        )


def _load() -> G2pmPinyinResolver:
    import g2pM  # noqa: WPS433 — lazy (note the capital M: PyPI ``g2pM``)

    try:
        model = g2pM.G2pM()
    except Exception as e:  # noqa: BLE001
        # g2pM loads its bundled numpy ``.pkl`` weights at construction; a
        # corrupt pickle, a numpy version mismatch, or a frozen / Nuitka build
        # that failed to bundle the data file raises something other than the
        # ImportError the registry maps to MissingExtraError. Raise it here
        # (the same convention g2pw / thulac use) so the ``auto`` chain — which
        # prefers g2pm as the default — catches it and degrades to
        # pypinyin / null instead of crashing the whole translation.
        raise MissingExtraError(
            adapter="g2pm",
            extra="g2pm",
            hint=(
                "the g2pM model could not be loaded (corrupt bundled weights, "
                "a numpy version mismatch, or a frozen build missing the .pkl "
                "data file); install with pip install brailix[g2pm]."
            ),
        ) from e

    def converter(text: str) -> list[str]:
        # tone=True keeps numeric tones (incl. neutral=5); char_split=True
        # guarantees one entry per character; u: → ü matches the parser.
        return [
            syllable.replace("u:", "ü")
            for syllable in model(text, tone=True, char_split=True)
        ]

    return G2pmPinyinResolver(converter=converter)
