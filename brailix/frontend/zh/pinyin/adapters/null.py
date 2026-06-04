"""Null pinyin resolver: leaves the ``pinyin`` field unset.

Used by the fallback pipeline when no real pinyin engine is installed.
Downstream backends should not assume every token has pinyin — they
treat ``None`` as "use a deterministic char-by-char fallback" or
emit a warning.
"""

from __future__ import annotations

from dataclasses import dataclass

from brailix.core.context import FrontendContext
from brailix.ir.inline import ChineseToken


@dataclass(slots=True)
class NullPinyinResolver:
    name: str = "null"

    def resolve(
        self,
        tokens: list[ChineseToken],
        ctx: FrontendContext | None = None,
    ) -> list[ChineseToken]:
        # Return tokens unchanged. We deliberately don't mutate the
        # incoming list, so callers can compare before/after.
        return list(tokens)


def _load() -> NullPinyinResolver:
    return NullPinyinResolver()
