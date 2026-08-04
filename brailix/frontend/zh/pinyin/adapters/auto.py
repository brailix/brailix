"""Automatic pinyin resolver selection.

The default Chinese pipeline should use the strongest installed pinyin
backend while still working in a zero-extra environment. ``g2pm`` leads
the chain — it bundles its neural weights in the wheel, so it
disambiguates polyphones offline with no download (unlike ``g2pw``,
whose model is downloaded on demand) — making it the shipping
default. ``auto`` then falls back to ``g2pw``, then ``pypinyin``, and
finally to ``null``.

``null`` is the last link and it is not a fallback in the sense the others
are: it resolves nothing, and a Chinese word with no reading produces **no
cells at all**. Reaching it therefore means "this installation has no pinyin
engine", not "a slightly weaker reading" — so this adapter says so, once, as
a ``NO_PINYIN_ENGINE`` warning the first time it resolves. Without that the
whole document came out blank with nothing but one ``MISSING_PINYIN`` per
character to explain it, which reads as thousands of small problems rather
than the single configuration one it is.
"""

from __future__ import annotations

from dataclasses import dataclass as _dataclass
from dataclasses import field as _field

from brailix.core.context import FrontendContext
from brailix.core.errors import (
    CANDIDATE_UNAVAILABLE_ERRORS,
    UnknownAdapterError,
)
from brailix.frontend.zh.pinyin import PinyinResolver
from brailix.frontend.zh.tokens import ChineseToken

# The chain's last link, and the one that resolves nothing. Named so the
# check below states what it is testing for rather than matching a bare
# string that reads like just another engine.
_NO_ENGINE = "null"


@_dataclass(slots=True)
class AutoPinyinResolver:
    name: str = "auto"
    preferred: tuple[str, ...] = ("g2pm", "g2pw", "pypinyin", _NO_ENGINE)
    _delegate: PinyinResolver | None = _field(default=None, init=False, repr=False)
    _warned: bool = _field(default=False, init=False, repr=False)

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        delegate = self._load_delegate()
        if delegate.name == _NO_ENGINE and not self._warned and ctx is not None:
            # Once per resolver instance, not once per call: the pipeline
            # holds one of these for its lifetime and resolves per block, so
            # warning every time would bury the message it is trying to make
            # visible under one copy per block.
            self._warned = True
            ctx.warnings.warn(
                code="NO_PINYIN_ENGINE",
                message=(
                    "no pinyin engine is installed, so Chinese words have no "
                    "reading and produce no braille cells; install one with "
                    "`pip install brailix[pypinyin]` (or g2pm / g2pw), or "
                    "select an installed resolver"
                ),
                surface=None,
                span=None,
                source="frontend.zh.pinyin",
            )
        return delegate.resolve(tokens, ctx)

    def _load_delegate(self) -> PinyinResolver:
        if self._delegate is not None:
            return self._delegate

        from brailix.frontend.zh.pinyin.registry import resolver_registry

        last_error: Exception | None = None
        for name in self.preferred:
            if name == self.name:
                continue
            try:
                self._delegate = resolver_registry.get(name)
                return self._delegate
            except CANDIDATE_UNAVAILABLE_ERRORS as e:
                # The shared list
                # (:data:`~brailix.core.errors.CANDIDATE_UNAVAILABLE_ERRORS`),
                # not a hand-written subset: this chain used to catch only
                # KeyError and MissingExtraError,
                # so a resolver that grew a version-compatibility check and
                # reported it as IncompatibleDependencyError — the type whose
                # own documentation promises the auto chains skip it — would
                # have crashed the compile instead of degrading to pypinyin.
                last_error = e

        if last_error is not None:
            raise last_error
        raise UnknownAdapterError("auto pinyin resolver has no candidates")


def _load() -> AutoPinyinResolver:
    return AutoPinyinResolver()
