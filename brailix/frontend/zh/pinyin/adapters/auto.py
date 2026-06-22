"""Automatic pinyin resolver selection.

The default Chinese pipeline should use the strongest installed pinyin
backend while still working in a zero-extra environment. ``g2pm`` leads
the chain — it bundles its neural weights in the wheel, so it
disambiguates polyphones offline with no download (unlike ``g2pw``,
whose model is downloaded on demand) — making it the shipping
default. ``auto`` then falls back to ``g2pw``, then ``pypinyin``, and
finally to ``null``.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from brailix.core.context import FrontendContext
from brailix.core.errors import MissingExtraError, UnknownAdapterError
from brailix.core.protocols import PinyinResolver
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class AutoPinyinResolver:
    name: str = "auto"
    preferred: tuple[str, ...] = ("g2pm", "g2pw", "pypinyin", "null")
    _delegate: PinyinResolver | None = field(default=None, init=False, repr=False)

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        return self._load_delegate().resolve(tokens, ctx)

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
            except (KeyError, MissingExtraError) as e:
                last_error = e

        if last_error is not None:
            raise last_error
        raise UnknownAdapterError("auto pinyin resolver has no candidates")


def _load() -> AutoPinyinResolver:
    return AutoPinyinResolver()
