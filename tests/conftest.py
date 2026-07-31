"""Root pytest configuration shared by every test tier.

Defines the ``requires`` marker: tag a test — or a whole class — with
``@pytest.mark.requires("<module>")`` and it is skipped when that optional
adapter dependency is not installed.  This keeps the minimal / per-extra CI
tiers green: an adapter test that asserts real conversion output turns into a
clean *skip* when its extra is absent, instead of a spurious failure.  It also
makes the tiers selectable — ``-m "requires"`` / ``-m "not requires"`` — for a
per-dependency job matrix.  The tier layout is documented in
``[tool.pytest.ini_options]`` in pyproject.toml.

**"Installed" here means findable, not importable**, and the difference is
worth stating rather than glossing.  The check is
:func:`importlib.util.find_spec`, which locates the module without executing
it; a package that is present but *broken* — a missing transitive dependency, a
shared library or ABI mismatch, a module whose top level raises — is found, so
the test is not skipped and fails on the import instead.  That is the intended
trade: importing for real at collection time would run every optional
dependency's module-level code (model loads, native library probes) on every
run, for a question that is almost always "is the extra installed at all".  A
broken install *should* be loud; what this marker exists to keep quiet is an
absent one.
"""

from __future__ import annotations

import importlib.util
import os

import pytest

# --- Hypothesis profile ------------------------------------------------------
#
# The property-based suites (tests/*/test_*_properties.py) all run under one
# shared profile so their behaviour is uniform and CI-friendly:
#
# * ``deadline=None`` — the first example of a run may pay a one-off adapter
#   cost (tokenizer model load, jieba dict build) that has nothing to do with
#   the property under test; a per-example deadline would flake on it.
# * ``derandomize=True`` under CI — CI verifies *checked-in* properties
#   reproducibly; exploring fresh random seeds is the local dev loop's job.
#   (GitHub Actions always sets ``CI``.)
# * ``print_blob=True`` — a failure prints the ``@reproduce_failure`` blob so
#   a CI-found counterexample can be replayed locally verbatim.
#
# Guarded import: hypothesis is a dev-group dependency, but a hand-rolled
# partial install (pip + a couple of extras) should still run the rest of the
# suite — the property modules themselves ``pytest.importorskip`` it.
try:
    from hypothesis import HealthCheck as _HealthCheck
    from hypothesis import settings as _hyp_settings
except ImportError:
    pass
else:
    # Mutation runs (scripts/run_mutmut.py sets MUTMUT_RUN=1) re-execute the
    # suite several times inside ONE process, so a class-based @given test
    # sees a fresh instance per pass and trips the differing_executors
    # health check. That's an artifact of mutmut's in-process runner, not a
    # test bug — suppress it there and ONLY there.
    _suppressed = (
        [_HealthCheck.differing_executors] if os.environ.get("MUTMUT_RUN") else []
    )
    _hyp_settings.register_profile(
        "brailix",
        deadline=None,
        derandomize=bool(os.environ.get("CI")),
        print_blob=True,
        suppress_health_check=_suppressed,
    )
    _hyp_settings.load_profile("brailix")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip any ``@pytest.mark.requires("mod")`` item whose ``mod`` is absent.

    Uses :func:`importlib.util.find_spec`, which answers "is it installed?"
    without importing (and so without running) the module — see the module
    docstring for why a present-but-broken package deliberately does *not*
    skip.  Multiple ``requires`` markers stack — a test needing both an
    analyzer and a pinyin engine skips if *either* is missing.
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
