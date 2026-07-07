"""Root pytest configuration shared by every test tier.

Defines the ``requires`` marker: tag a test — or a whole class — with
``@pytest.mark.requires("<module>")`` and it is skipped when that optional
adapter dependency can't be imported.  This keeps the minimal / per-extra CI
tiers green: an adapter test that asserts real conversion output turns into a
clean *skip* when its extra is absent, instead of a spurious failure.  It also
makes the tiers selectable — ``-m "requires"`` / ``-m "not requires"`` — for a
per-dependency job matrix.  The tier layout is documented in
``[tool.pytest.ini_options]`` in pyproject.toml.
"""

from __future__ import annotations

import importlib.util

import pytest


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip any ``@pytest.mark.requires("mod")`` item whose ``mod`` is absent.

    Uses :func:`importlib.util.find_spec` so a missing extra is detected
    without importing (and running) the module.  Multiple ``requires`` markers
    stack — a test needing both an analyzer and a pinyin engine skips if
    *either* is missing.
    """
    for item in items:
        for marker in item.iter_markers(name="requires"):
            module = marker.args[0] if marker.args else None
            if not module:
                continue
            try:
                present = importlib.util.find_spec(module) is not None
            except (ImportError, ValueError):
                present = False
            if not present:
                item.add_marker(
                    pytest.mark.skip(
                        reason=f"needs optional dependency {module!r}"
                    )
                )
