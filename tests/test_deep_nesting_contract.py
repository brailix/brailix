"""Cross-cutting contract: a deeply-nested tree IR must never crash the
pipeline with a ``RecursionError``.

Three verticals carry an XML tree *as* their IR — MathML for math, MusicXML for
music, SVG for tactile graphics — and each hands that tree to a recursive walk.
The "pipeline never crashes; soft-fail to a warning" invariant (the
``backend.math`` / ``backend.music`` package docstrings' "The pipeline never
crashes", the normalizers' "never raises", ``rasterize``'s "never raises on bad
geometry") held at the element-handler level but not against *depth*: an
adversarially deep — or merely corrupt — tree from an untrusted ``.docx`` OLE
object, an ``.mxl`` container, direct MathML / MusicXML, or a reloaded document
would overflow Python's stack.

The fix is one bounded-depth probe (``core._xml.tree_depth_exceeds``, iterative
so the guard is itself depth-safe) at each boundary that recurses, plus an
iterative ``strip_namespace`` at the IR-deserialization boundary. This test is
the regression that keeps a future tree walk from silently reintroducing the
crash.

**Why it covers all three together.** Math was guarded first and music was not,
so a 6000-level ``<score-partwise>`` chain crashed the music backend for as long
as the two lived side by side; graphics was guarded in its normalizer only, so
the same chain crashed the tactile backend whenever a tree arrived from a
serialized payload instead of from the frontend. Neither gap was visible from
inside its own vertical's test package — the asymmetry only shows when the
verticals are asserted against the same contract, which is what this module is
for. A fourth tree IR belongs here on the day it is added.

Each vertical is checked at every entry point that can *receive* a tree, not
just the one that normally does, because the whole point of the backend-side
guard is the paths that skip the frontend: deserialization from a project file
and a directly-constructed IR node. Each also gets a "real content still
renders" case, so a cap set absurdly low would fail here rather than quietly
degrading every document.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

from brailix.backend.math import translate as math_translate
from brailix.backend.math import translate_tree as math_translate_tree
from brailix.backend.music import translate_tree as music_translate_tree
from brailix.backend.tactile import rasterize
from brailix.backend.tactile.profile import load_tactile_profile
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import WarningCollector
from brailix.frontend.graphics.normalizer import normalize as normalize_svg
from brailix.frontend.math.normalizer import normalize as normalize_mathml
from brailix.frontend.music.normalizer import normalize as normalize_musicxml
from brailix.ir.document import GraphicBlock, ScoreBlock, block_from_dict
from brailix.ir.inline import MathInline, from_dict

# Far past Python's default recursion limit and the backends' empirical
# ~470-level overflow point, so a recursive walk would certainly crash.
_DEEP = 6000


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


def _nested(tag: str, depth: int, leaf: str = "") -> str:
    return f"<{tag}>" * depth + leaf + f"</{tag}>" * depth


def _nested_tree(root_tag: str, child_tag: str, depth: int) -> ET.Element:
    root = ET.Element(root_tag)
    cur = root
    for _ in range(depth):
        cur = ET.SubElement(cur, child_tag)
    return root


# ---------------------------------------------------------------------------
# Math (MathML)
# ---------------------------------------------------------------------------


def _deep_mathml(depth: int = _DEEP) -> str:
    return "<math>" + _nested("mrow", depth, "<mn>1</mn>") + "</math>"


def _deep_math_tree(depth: int = _DEEP) -> ET.Element:
    root = _nested_tree("math", "mrow", depth)
    deepest = root
    while len(deepest):
        deepest = deepest[0]
    ET.SubElement(deepest, "mn").text = "1"
    return root


class TestDeepMathNeverCrashes:
    def test_normalizer_soft_fails_to_merror(self) -> None:
        root = normalize_mathml(_deep_mathml())  # must not raise
        # Degraded to a single bare <merror> rather than overflowing the
        # recursive passes (namespace stripped, like the parse-error path).
        assert root.tag == "math"
        assert [c.tag for c in root] == ["merror"]

    def test_ir_deserialization_does_not_raise(self) -> None:
        # safe_fromstring (expat) parses deep XML iteratively; the IR boundary
        # then strips namespaces — iteratively now, so no RecursionError
        # escapes from_dict (the documented soft-fail boundary).
        node = from_dict(
            {"type": "math_inline", "surface": "x", "tree": _deep_mathml()}
        )
        assert isinstance(node, MathInline)
        assert node.tree is not None
        assert node.tree.tag == "math"

    def test_backend_translate_soft_fails(self, profile) -> None:
        ctx = BackendContext(profile="cn_current")
        node = MathInline(surface="deep", source="mathml", tree=_deep_math_tree())
        cells = math_translate(node, ctx, profile)  # must not raise
        assert cells  # at least the single unknown fallback cell
        assert any(w.code == "MATH_ERROR" for w in ctx.warnings.warnings)

    def test_backend_translate_tree_soft_fails(self, profile) -> None:
        ctx = BackendContext(profile="cn_current")
        cells = math_translate_tree(_deep_math_tree(), ctx, profile)  # must not raise
        assert cells
        assert any(w.code == "MATH_ERROR" for w in ctx.warnings.warnings)

    def test_a_real_formula_still_renders(self, profile) -> None:
        # Guard against the depth cap being set so low it rejects real math:
        # a normal fraction must still translate to real cells, no MATH_ERROR.
        ctx = BackendContext(profile="cn_current")
        tree = normalize_mathml("<math><mfrac><mn>1</mn><mn>2</mn></mfrac></math>")
        cells = math_translate_tree(tree, ctx, profile)
        assert cells
        assert not any(w.code == "MATH_ERROR" for w in ctx.warnings.warnings)


# ---------------------------------------------------------------------------
# Music (MusicXML)
# ---------------------------------------------------------------------------

# Nesting the container tag into itself is what the dispatcher actually
# recurses through: score → part → measure → note sequence all hand children
# back to ``_emit_element``, so a self-nested container is the shortest tree
# that reaches the recursion.
_DEEP_MUSIC_CONTAINERS = ("score-partwise", "part", "measure", "note")

_REAL_SCORE = """<score-partwise version="3.1">
  <part-list><score-part id="P1"><part-name>Music</part-name></score-part></part-list>
  <part id="P1">
    <measure number="1">
      <attributes><divisions>1</divisions>
        <time><beats>4</beats><beat-type>4</beat-type></time></attributes>
      <note><pitch><step>C</step><octave>4</octave></pitch>
        <duration>1</duration><type>quarter</type></note>
    </measure>
  </part>
</score-partwise>"""


def _deep_musicxml(depth: int = _DEEP) -> str:
    return _nested("score-partwise", depth + 1)


class TestDeepMusicNeverCrashes:
    def test_normalizer_does_not_raise(self) -> None:
        # The normalizer's own passes are iterative, so it neither crashes nor
        # needs a cap of its own; it hands the deep tree straight on, which is
        # precisely why the backend cannot trust its input.
        root = normalize_musicxml(_deep_musicxml())  # must not raise
        assert root.tag == "score-partwise"

    def test_ir_deserialization_does_not_raise(self) -> None:
        block = block_from_dict(
            {
                "type": "score",
                "text": "x",
                "tree": _deep_musicxml(),
            }
        )
        assert isinstance(block, ScoreBlock)
        assert block.tree is not None
        assert block.tree.tag == "score-partwise"

    def test_backend_translate_tree_soft_fails(self, profile) -> None:
        ctx = BackendContext(profile="cn_current")
        cells = music_translate_tree(
            _nested_tree("score-partwise", "score-partwise", _DEEP), ctx, profile
        )  # must not raise
        assert cells
        assert any(w.code == "MUSIC_ERROR" for w in ctx.warnings.warnings)

    @pytest.mark.parametrize("tag", _DEEP_MUSIC_CONTAINERS)
    def test_every_recursing_container_soft_fails(self, tag, profile) -> None:
        """One guard at the entry point covers every container, but only
        because it runs *before* dispatch — assert that per container rather
        than trusting one representative tag, since each has its own handler
        and its own descent into children."""
        ctx = BackendContext(profile="cn_current")
        cells = music_translate_tree(_nested_tree(tag, tag, _DEEP), ctx, profile)
        assert cells
        assert any(w.code == "MUSIC_ERROR" for w in ctx.warnings.warnings)

    def test_a_real_score_still_renders(self, profile) -> None:
        # A cap low enough to reject an ordinary score would fail here.
        ctx = BackendContext(profile="cn_current", block_type="score")
        cells = music_translate_tree(
            normalize_musicxml(_REAL_SCORE), ctx, profile
        )
        assert any(c.role == "music_note" for c in cells)
        assert not any(w.code == "MUSIC_ERROR" for w in ctx.warnings.warnings)


# ---------------------------------------------------------------------------
# Tactile graphics (SVG)
# ---------------------------------------------------------------------------

_REAL_SVG = (
    '<svg width="100mm" height="100mm" viewBox="0 0 100 100">'
    '<line x1="10" y1="10" x2="90" y2="90"/>'
    "</svg>"
)


def _deep_svg(depth: int = _DEEP) -> str:
    return (
        '<svg width="100mm" height="100mm" viewBox="0 0 100 100">'
        + _nested("g", depth, '<line x1="0" y1="0" x2="10" y2="10"/>')
        + "</svg>"
    )


@pytest.fixture(scope="module")
def tactile_profile():
    return load_tactile_profile("generic")


class TestDeepGraphicNeverCrashes:
    def test_normalizer_soft_fails_to_blank(self) -> None:
        root = normalize_svg(_deep_svg())  # must not raise
        assert root.get("data-bk-error") is not None
        assert list(root) == []

    def test_ir_deserialization_does_not_raise(self) -> None:
        """The gap that made the tactile backend crash: a project file stores
        the SVG as a string and ``block_from_dict`` re-parses it directly, so
        the normalizer's cap never runs on a reopened document."""
        block = block_from_dict(
            {"type": "graphic", "text": "g", "tree": _deep_svg()}
        )
        assert isinstance(block, GraphicBlock)
        assert block.tree is not None
        assert block.tree.tag == "svg"

    def test_backend_rasterize_soft_fails(self, tactile_profile) -> None:
        block = block_from_dict(
            {"type": "graphic", "text": "g", "tree": _deep_svg()}
        )
        warnings = WarningCollector()
        raster = rasterize(block.tree, tactile_profile, warnings)  # must not raise
        # A blank page of the right physical size, not a crash.
        assert raster.width > 0 and raster.height > 0
        assert not any(raster.data)
        assert any(w.code == "GRAPHICS_SOFT_FAIL" for w in warnings.warnings)

    def test_a_real_drawing_still_rasterizes(self, tactile_profile) -> None:
        warnings = WarningCollector()
        raster = rasterize(normalize_svg(_REAL_SVG), tactile_profile, warnings)
        assert any(raster.data)
        assert not any(w.code == "GRAPHICS_SOFT_FAIL" for w in warnings.warnings)
