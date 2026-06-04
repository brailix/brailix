"""Built-in default names — single source of truth.

Whenever the library needs to pick a "first reasonable choice" of
profile / segmenter / normalizer / renderer / etc, it pulls the name
from this module instead of hard-coding a string. Changing the
shipping default is then a one-line edit and references stay in sync.

These are **names registered with the corresponding registries**, not
type names. Users who write custom profiles or adapters register them
under their own name and pass that name to :class:`Pipeline`.

Nothing here is locked to Chinese; it just happens that the only
profile currently shipping is ``cn_current``. When more profiles
arrive these defaults stay; users override per-Pipeline.
"""

from __future__ import annotations

# Profile + language (BrailleProfile / DocumentIR metadata)
DEFAULT_PROFILE: str = "cn_current"
DEFAULT_LANGUAGE: str = "zh-CN"

# Frontend adapter chain
DEFAULT_SEGMENTER: str = "default"
DEFAULT_NORMALIZER: str = "default"
# Both analyzer and resolver default to "auto" so the heaviest
# installed implementation is picked at runtime — users don't need
# to know the registry to get good behavior out of the box.
# Override with a specific adapter name (``"jieba"``, ``"hanlp"``,
# ``"pypinyin"`` ...) when reproducibility matters.
DEFAULT_ZH_ANALYZER: str = "auto"
DEFAULT_PINYIN_RESOLVER: str = "auto"

# Output
DEFAULT_RENDERER: str = "unicode"
