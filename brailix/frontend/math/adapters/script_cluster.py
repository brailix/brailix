r"""Script-cluster source adapter — Word super/subscript formatting → MathML.

When a Word author types a formula as *formatted text* (``x²`` via Ctrl+=,
``H₂O`` via the Font dialog) rather than as a real OMML equation, the docx
input layer gathers the run of super/subscript-bearing characters into a
*cluster* and **linearises** it to a compact LaTeX-like source string —
baseline characters verbatim, a superscript run as ``^{...}``, a subscript
run as ``_{...}`` (e.g. ``H_{2}O``, ``x^{2}``, ``Fe^{3+}``). That string is
all the input layer produces: reconstructing the MathML, and deciding
whether the cluster is chemistry or generic math, is *analysis* and lives
here in the frontend (ARCHITECTURE#arch-layers, "source → MathML is the frontend's
job").

Two source names share this adapter:

* ``script_cluster``       — always generic math (``<msup>`` / ``<msub>`` /
  ``<msubsup>``), built with a tiny stdlib walker. No optional dependency.
* ``script_cluster_chem``  — try chemistry first: a cluster that
  conservatively reads as a chemical formula (a clean ``\ce`` parse plus a
  chemical *signature* — a multi-letter element, two or more elements, a
  charge, or a state label) is converted through :func:`...chem.convert_ce`
  and carries ``data-bk-chem``; anything else falls back to generic math.

The input layer selects the name purely from the ``chem_detection``
*config* flag (so passing it is not analysis); the chemical *judgment*
itself runs here. Output is a MathML string for the normalizer, exactly
like every other math source adapter.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from xml.sax.saxutils import escape

from brailix.core.context import MathContext
from brailix.frontend.math.adapters.chem import convert_ce, find_elements
from brailix.frontend.math.utils import _MATHML_NS

# ``-`` is canonicalised to the real minus (U+2212) so the math backend's
# symbol table matches — a raw hyphen-minus surfaces as MATH_UNKNOWN_SYMBOL.
_CLUSTER_OP_CANON = {"-": "−"}
# Physical-state labels a chemical formula may carry — a stronger signal for
# the conservative chemistry detector.
_CHEM_STATE_TOKENS = ("s", "l", "g", "aq")


# ---------------------------------------------------------------------------
# Source-string parsing: latex-like ``base ^{..} _{..}`` → (char, vert) atoms
# ---------------------------------------------------------------------------


def _parse_atoms(source: str) -> list[tuple[str, str | None]]:
    """Parse the input layer's linearised cluster back into ``(char, vert)``
    atoms — the inverse of ``input.docx._blocks._linearise_cluster``.

    ``^{...}`` tags its characters ``"super"``, ``_{...}`` tags them
    ``"sub"``, everything else is a baseline character (``vert=None``). The
    cluster alphabet is ASCII alphanumerics plus ``+-()[]`` — none of which
    is ``^`` / ``_`` / ``{`` / ``}`` — so no escaping is needed and a stray
    metacharacter (only possible from a malformed payload) degrades to a
    literal baseline char rather than raising.
    """
    atoms: list[tuple[str, str | None]] = []
    i, n = 0, len(source)
    while i < n:
        c = source[i]
        if c in "^_" and i + 1 < n and source[i + 1] == "{":
            close = source.find("}", i + 2)
            if close == -1:
                atoms.append((c, None))
                i += 1
                continue
            kind = "super" if c == "^" else "sub"
            atoms.extend((ch, kind) for ch in source[i + 2 : close])
            i = close + 1
        else:
            atoms.append((c, None))
            i += 1
    return atoms


# ---------------------------------------------------------------------------
# Generic math: (char, vert) atoms → MathML
# ---------------------------------------------------------------------------


def _scripts_to_mathml(run: list[tuple[str, str | None]]) -> str:
    """Build the inner MathML for a cluster of ``(char, vert)`` atoms.

    Walks left to right: a baseline atom (one ``<mi>`` per letter, one
    ``<mn>`` per digit run, one ``<mo>`` per operator) absorbs the
    immediately following super/subscript run(s) into an ``<msup>`` /
    ``<msub>`` / ``<msubsup>``. A leading script with no base (rare) is
    emitted as a plain atom so no character is dropped.
    """
    atoms: list[str] = []
    i = 0
    n = len(run)
    while i < n:
        _ch, vert = run[i]
        if vert is not None:
            content, i = _read_script_run(run, i, vert)
            atoms.append(content)
            continue
        base, i = _read_base_atom(run, i)
        sup = sub = None
        while i < n and run[i][1] is not None:
            kind = run[i][1]
            content, i = _read_script_run(run, i, kind)
            if kind == "super":
                sup = content if sup is None else f"<mrow>{sup}{content}</mrow>"
            else:
                sub = content if sub is None else f"<mrow>{sub}{content}</mrow>"
        if sup is not None and sub is not None:
            atoms.append(f"<msubsup>{base}{sub}{sup}</msubsup>")
        elif sup is not None:
            atoms.append(f"<msup>{base}{sup}</msup>")
        elif sub is not None:
            atoms.append(f"<msub>{base}{sub}</msub>")
        else:
            atoms.append(base)
    return "".join(atoms)


def _read_base_atom(run: list[tuple[str, str | None]], i: int) -> tuple[str, int]:
    """Read one baseline atom at ``i``: a whole digit run as ``<mn>``, a
    single letter as ``<mi>``, or one operator as ``<mo>`` (so a trailing
    script binds to the last letter, ``xy²`` = x·y²)."""
    ch, _ = run[i]
    if ch.isdigit():
        j = i
        while j < len(run) and run[j][1] is None and run[j][0].isdigit():
            j += 1
        digits = "".join(c for c, _ in run[i:j])
        return f"<mn>{digits}</mn>", j
    if ch.isalpha():
        return f"<mi>{escape(ch)}</mi>", i + 1
    return f"<mo>{escape(_CLUSTER_OP_CANON.get(ch, ch))}</mo>", i + 1


def _read_script_run(
    run: list[tuple[str, str | None]], i: int, kind: str | None
) -> tuple[str, int]:
    """Read the maximal run of chars whose vert equals ``kind`` and return
    its MathML wrapped in an ``<mrow>`` (the normalizer collapses a
    single-child mrow, so this always gives ``<msup>`` / ``<msub>`` exactly
    two children)."""
    j = i
    while j < len(run) and run[j][1] == kind:
        j += 1
    inner = _scripts_to_mathml([(c, None) for c, _ in run[i:j]])
    return f"<mrow>{inner}</mrow>", j


# ---------------------------------------------------------------------------
# Chemistry detection (conservative)
# ---------------------------------------------------------------------------


def _linearise_for_chem(run: list[tuple[str, str | None]]) -> str | None:
    """Linearise a cluster to ``\\ce`` text, or ``None`` if it can't be a
    formula.

    Baseline chars pass through verbatim (element letters / groups); a
    subscript run must be digits — a chemical count — and is inlined
    (``H`` ``2`` ``O`` → ``H2O``); a superscript run must be charge-shaped
    (digits then a required ``+`` / ``-``) and becomes ``^{...}``. A
    non-digit subscript (``H_i``) or a bare-exponent superscript (``x²``)
    returns ``None`` so the caller keeps the math reading."""
    parts: list[str] = []
    i = 0
    n = len(run)
    while i < n:
        ch, vert = run[i]
        if vert is None:
            parts.append(ch)
            i += 1
            continue
        j = i
        while j < n and run[j][1] == vert:
            j += 1
        chunk = "".join(c for c, _ in run[i:j])
        if vert == "sub":
            if not chunk.isdigit():
                return None
            parts.append(chunk)
        else:  # super → must be a charge, not an exponent
            if not re.fullmatch(r"\d*[+-]", chunk):
                return None
            parts.append("^{" + chunk + "}")
        i = j
    return "".join(parts)


def _has_chem_signature(linear: str) -> bool:
    """True when the linearised cluster shows evidence it's really chemistry
    and not a coincidental single element letter: a multi-letter element, two
    or more elements, a charge (``^``), or a physical-state label."""
    elements = find_elements(linear)
    if any(len(e) >= 2 for e in elements):
        return True
    if len(elements) >= 2:
        return True
    if "^" in linear:
        return True
    return any(f"({t})" in linear for t in _CHEM_STATE_TOKENS)


# ---------------------------------------------------------------------------
# Adapter
# ---------------------------------------------------------------------------


@dataclass
class ScriptClusterMathSourceAdapter:
    """Linearised Word script cluster → MathML string.

    ``chem`` (set per registered source name) gates the chemistry attempt;
    the chemical *judgment* — does this cluster actually read as a formula —
    is made here, not in the input layer.
    """

    source: str = "script_cluster"
    chem: bool = False

    def to_mathml(
        self, formula: str | bytes, ctx: MathContext | None = None
    ) -> str:
        if isinstance(formula, bytes):
            formula = formula.decode("utf-8", "replace")
        atoms = _parse_atoms(formula)
        if self.chem:
            linear = _linearise_for_chem(atoms)
            if linear is not None:
                chem_mathml = convert_ce(linear)
                if "<merror" not in chem_mathml and _has_chem_signature(linear):
                    return chem_mathml
        return f'<math xmlns="{_MATHML_NS}">{_scripts_to_mathml(atoms)}</math>'


def _load() -> ScriptClusterMathSourceAdapter:
    return ScriptClusterMathSourceAdapter(source="script_cluster", chem=False)


def _load_chem() -> ScriptClusterMathSourceAdapter:
    return ScriptClusterMathSourceAdapter(source="script_cluster_chem", chem=True)
