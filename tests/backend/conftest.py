"""Shared fixtures for the backend test suite.

The ``profile`` fixture is auto-discovered by pytest (no import needed,
so no ruff F401 on an "unused" import). It is module-scoped; sibling
test files that define their own module-local ``profile`` shadow it.
"""

from __future__ import annotations

import pytest

from brailix.core.config import load_profile


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")
