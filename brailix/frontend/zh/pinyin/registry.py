"""Registry for pinyin resolver adapters.

The ``auto`` and ``null`` adapters are always present and have no
third-party dependencies. ``auto`` prefers ``g2pm``, then ``g2pw``,
then ``pypinyin``, and otherwise falls back to ``null``. ``g2pm``,
``g2pw`` and ``pypinyin`` register lazily via extras for explicit
selection.
"""

from __future__ import annotations

from brailix.core.registry import Registry
from brailix.frontend.zh.pinyin import PinyinResolver

resolver_registry: Registry[PinyinResolver] = Registry(
    "pinyin", protocol=PinyinResolver
)


def _register_builtin() -> None:
    from brailix.frontend.zh.pinyin.adapters import (  # noqa: F401
        auto,
        g2pm,
        g2pw,
        null,
        pypinyin,
    )

    resolver_registry.register("auto", auto._load)
    resolver_registry.register("null", null._load)
    # The module, not the extra: the ``g2pm`` extra installs ``g2pM``, and a
    # probe that guessed the module from the extra name would report the
    # shipping default missing on every platform with a case-sensitive
    # filesystem. See Registry.register.
    resolver_registry.register(
        "g2pm", g2pm._load, extra="g2pm", probe="g2pM"
    )
    resolver_registry.register(
        "g2pw", g2pw._load, extra="g2pw", probe="g2pw"
    )
    resolver_registry.register(
        "pypinyin", pypinyin._load, extra="pypinyin", probe="pypinyin"
    )


_register_builtin()
