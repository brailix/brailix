"""Chinese frontend — the language's tokenizer and reading resolver.

Two independent subsystems live under this umbrella, mirroring the zh
language's two frontend jobs:

* :mod:`~brailix.frontend.zh.analyzer` — text → ``list[ChineseToken]``
  (HanLP / jieba / char adapters), plus the IRBuilder helpers
  (``tokens_to_inline`` / ``shift_token_spans`` /
  ``insert_cross_kind_boundary_spaces``).
* :mod:`~brailix.frontend.zh.pinyin` — fill each token's reading
  (pypinyin / g2pM / g2pW adapters).

Per ARCHITECTURE §7.1 they stay swap-independent: the analyzer must not
import the resolver, so the orchestrator (:class:`brailix.Pipeline`)
chains ``tokenize`` → ``pinyin.annotate`` → ``tokens_to_inline`` rather
than letting one call the other. This umbrella therefore re-exports only
the analyzer's public entry points; reach the resolver via
``brailix.frontend.zh.pinyin``.
"""

from __future__ import annotations

from brailix.frontend.zh.analyzer import (
    insert_cross_kind_boundary_spaces,
    shift_token_spans,
    tokenize,
    tokens_to_inline,
)

__all__ = (
    "tokenize",
    "shift_token_spans",
    "tokens_to_inline",
    "insert_cross_kind_boundary_spaces",
)
