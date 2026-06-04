"""Registry for math source-format adapters.

Adapters convert raw formula text from a specific source format
(LaTeX, MathML, ASCIIMath, ...) into a normalized MathML string. The
math backend then walks the MathML element tree directly — there is no
separate IR-builder layer. Adding a new source format means adding
exactly one adapter; the backend doesn't change.
"""

from __future__ import annotations

from brailix.core.protocols import MathSourceAdapter
from brailix.core.registry import Registry

math_source_registry: Registry[MathSourceAdapter] = Registry(
    "math.source", protocol=MathSourceAdapter
)


def _register_builtin() -> None:
    from brailix.frontend.math.adapters import (  # noqa: F401
        chem,
        eq_field,
        latex,
        mathml,
        mtef,
        omml,
    )

    math_source_registry.register("mathml", mathml._load)
    math_source_registry.register("latex", latex._load, extra="latex")
    # mhchem ``\ce{...}`` → chemistry MathML. Pure-stdlib (no ``extra``),
    # so it works without the ``latex`` package; the ``latex`` adapter also
    # delegates here when it detects a top-level ``\ce``. See
    # ``brailix/frontend/math/adapters/chem.py``.
    math_source_registry.register("chem", chem._load)
    # OMML adapter is pure-stdlib (only ElementTree) — no ``extra`` so it
    # works without optional packages. The docx *input* adapter still
    # requires ``python-docx`` to extract the OMML from a .docx file,
    # but the math-frontend conversion itself has no third-party deps.
    math_source_registry.register("omml", omml._load)
    # MTEF adapter is also pure-stdlib. The docx input adapter needs
    # ``olefile`` to crack the OLE compound document open before it can
    # hand the MTEF payload here, so the third-party requirement sits
    # one layer up; this dialect translator itself has no deps.
    math_source_registry.register("mtef", mtef._load)
    # Word EQ field — legacy equation format still used by Chinese
    # teaching materials. Pure-stdlib; docx adapter extracts the
    # ``instrText`` string and hands it over.
    math_source_registry.register("eq_field", eq_field._load)


_register_builtin()
