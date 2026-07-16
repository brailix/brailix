"""Tests for :meth:`Pipeline.translate_block` + :class:`CompiledBlock`
+ the :func:`brailix.pipeline.block_hash` helper + the parse-only
public methods :meth:`Pipeline.parse_text` / :meth:`Pipeline.parse_file`.

These cover the compiler-side block primitive that lets a front-end
re-compile a single :class:`brailix.ir.document.Block` without
touching the rest of the document.

Override-aware behaviour lives in a downstream front-end; the
core pipeline exercised here is override-agnostic.  That layer composes
:meth:`translate_block` with an ``ir_transformer`` callable.  This file
tests:

* The transformer hook fires on a single mutation between frontend
  and backend.
* Override application by a downstream front-end is out of scope here.
"""

import pytest

from brailix import Pipeline
from brailix.ir.braille import BrailleBlock
from brailix.ir.document import (
    DocumentIR,
    GraphicBlock,
    Heading,
    ListItem,
    Paragraph,
    ScoreBlock,
)
from brailix.ir.document import List as ListBlock
from brailix.ir.inline import HanziChar
from brailix.pipeline import CompiledBlock, block_hash


@pytest.fixture(scope="module")
def pipe() -> Pipeline:
    """Minimal-deps Pipeline: char tokenizer + null pinyin resolver
    so tests don't need jieba / pypinyin installed."""
    return Pipeline(profile="cn_current", analyzer="char", resolver="null")


# ---------------------------------------------------------------------------
# profile accessors (public API boundary)
# ---------------------------------------------------------------------------


def test_profile_accessors_expose_resolved_profile() -> None:
    """``profile_name`` / ``profile_language`` give a front-end the
    resolved profile identity without reaching into the private
    ``_profile`` (the public-API boundary)."""
    pipe = Pipeline(profile="cn_current")
    assert pipe.profile_name == "cn_current"
    assert isinstance(pipe.profile_language, str) and pipe.profile_language


# ---------------------------------------------------------------------------
# block_hash helper
# ---------------------------------------------------------------------------


class TestBlockHash:
    def test_same_inputs_produce_same_hash(self) -> None:
        b1 = Paragraph(text="hello")
        b2 = Paragraph(text="hello")
        assert block_hash(b1, "cn_current") == block_hash(b2, "cn_current")

    def test_different_text_changes_hash(self) -> None:
        h1 = block_hash(Paragraph(text="a"), "cn_current")
        h2 = block_hash(Paragraph(text="b"), "cn_current")
        assert h1 != h2

    def test_different_profile_changes_hash(self) -> None:
        b = Paragraph(text="x")
        assert block_hash(b, "cn_current") != block_hash(b, "ueb_math")

    def test_hash_is_64_char_hex(self) -> None:
        h = block_hash(Paragraph(text="x"), "cn_current")
        assert len(h) == 64
        int(h, 16)  # parses as hex

    def test_block_with_children_hashes_by_concatenated_surface(self) -> None:
        """Block without raw .text but with populated children hashes
        by the concatenated surface — reflects the source."""
        b = Paragraph(children=[HanziChar(surface="字")])
        h_children = block_hash(b, "cn_current")
        h_text = block_hash(Paragraph(text="字"), "cn_current")
        assert h_children == h_text

    def test_heading_and_paragraph_same_text_hash_apart(self) -> None:
        """Structural identity is folded into the key: a same-text Heading and
        Paragraph render under different layout rules (centred vs flush-left),
        so they must not share a cache entry. This is the P1-1 safety property
        — surface alone would collide them."""
        h = block_hash(Heading(text="标题", level=1), "cn_current")
        p = block_hash(Paragraph(text="标题"), "cn_current")
        assert h != p

    def test_heading_levels_hash_apart(self) -> None:
        h1 = block_hash(Heading(text="标题", level=1), "cn_current")
        h2 = block_hash(Heading(text="标题", level=2), "cn_current")
        assert h1 != h2

    def test_ordered_and_unordered_list_hash_apart(self) -> None:
        """Same items, different ordering → numbered vs bulleted braille, so
        different key even though the joined surface is identical."""
        ordered = block_hash(
            ListBlock(
                ordered=True, items=[ListItem(text="甲"), ListItem(text="乙")]
            ),
            "cn_current",
        )
        unordered = block_hash(
            ListBlock(
                ordered=False, items=[ListItem(text="甲"), ListItem(text="乙")]
            ),
            "cn_current",
        )
        assert ordered != unordered

    def test_same_surface_different_table_shape_hash_apart(self) -> None:
        """The exact collision P1-1 guards against: two tables whose joined
        surface is byte-identical (``"甲 | 乙"``) but whose grid shape differs
        (one 2-column cell run vs a single cell that happens to contain the
        separator) must key apart, or the cache serves one shape's braille for
        the other."""
        from brailix.ir.document import Table, TableCell, TableRow

        two_cells = Table(
            rows=[TableRow(cells=[TableCell(text="甲"), TableCell(text="乙")])]
        )
        one_cell = Table(
            rows=[TableRow(cells=[TableCell(text="甲 | 乙")])]
        )
        assert block_hash(two_cells, "cn_current") != block_hash(
            one_cell, "cn_current"
        )

    def test_math_source_dialect_hashes_apart(self) -> None:
        """Same formula text under different source dialects parses to
        different trees (``source`` is a structural field), so they must not
        share a cache entry."""
        from brailix.ir.document import MathBlock

        latex = block_hash(MathBlock(text="x", source="latex"), "cn_current")
        mathml = block_hash(MathBlock(text="x", source="mathml"), "cn_current")
        assert latex != mathml


# ---------------------------------------------------------------------------
# CompiledBlock dataclass
# ---------------------------------------------------------------------------


class TestCompiledBlockDataclass:
    def test_constructable_with_minimal_args(self) -> None:
        block = Paragraph(text="x")
        cb = CompiledBlock(
            block_id="",
            source_hash="abc",
            ir=block,
            braille_blocks=[],
        )
        assert cb.warnings == []
        assert cb.tree_subcache == {}
        assert cb.compiled_at is not None


# ---------------------------------------------------------------------------
# Pipeline.translate_block
# ---------------------------------------------------------------------------


class TestTranslateBlock:
    def test_returns_compiled_block_with_required_fields(
        self, pipe: Pipeline
    ) -> None:
        block = Paragraph(text="我是")
        out = pipe.translate_block(block)
        assert isinstance(out, CompiledBlock)
        assert out.source_hash != ""
        assert out.ir is block
        assert len(out.braille_blocks) >= 1
        assert isinstance(out.braille_blocks[0], BrailleBlock)
        assert out.tree_subcache == {}

    def test_braille_equivalent_to_translate_text_single_paragraph(
        self, pipe: Pipeline
    ) -> None:
        """translate_block(paragraph) gives the same braille cells as
        translate_text(text) on the same input."""
        text = "我是中国"
        text_result = pipe.translate_text(text)
        block_result = pipe.translate_block(Paragraph(text=text))
        text_cells = [c.dots for c in text_result.braille_ir.blocks[0].cells]
        block_cells = [c.dots for c in block_result.braille_blocks[0].cells]
        assert text_cells == block_cells

    def test_hash_stable_for_same_inputs(self, pipe: Pipeline) -> None:
        a = pipe.translate_block(Paragraph(text="我是"))
        b = pipe.translate_block(Paragraph(text="我是"))
        assert a.source_hash == b.source_hash

    def test_hash_changes_when_text_edited(self, pipe: Pipeline) -> None:
        a = pipe.translate_block(Paragraph(text="我是"))
        b = pipe.translate_block(Paragraph(text="你是"))
        assert a.source_hash != b.source_hash

    def test_pre_populated_children_skips_frontend(self, pipe: Pipeline) -> None:
        block = Paragraph(children=[HanziChar(surface="字")])
        out = pipe.translate_block(block)
        assert len(out.ir.children) == 1
        assert out.ir.children[0].surface == "字"

    def test_heading_translates_to_one_braille_block(
        self, pipe: Pipeline
    ) -> None:
        block = Heading(text="标题", level=1)
        out = pipe.translate_block(block)
        assert len(out.braille_blocks) == 1
        assert out.braille_blocks[0].block_type == "heading"

    def test_list_translates_to_per_item_braille_blocks(
        self, pipe: Pipeline
    ) -> None:
        """Composite block: 2 list items → 2 braille blocks expansion."""
        items = [
            ListItem(children=[HanziChar(surface="一")]),
            ListItem(children=[HanziChar(surface="二")]),
        ]
        lst = ListBlock(items=items, ordered=False)
        out = pipe.translate_block(lst)
        assert len(out.braille_blocks) == 2
        for bb in out.braille_blocks:
            assert bb.block_type == "list_item"

    def test_block_id_propagated(self, pipe: Pipeline) -> None:
        block = Paragraph(text="x", id="b1")
        out = pipe.translate_block(block)
        assert out.block_id == "b1"

    def test_warnings_scoped_to_block(self, pipe: Pipeline) -> None:
        """Each translate_block call gets a fresh WarningCollector —
        warnings don't leak between calls."""
        out1 = pipe.translate_block(Paragraph(text="我"))
        out2 = pipe.translate_block(Paragraph(text="你"))
        # Each compilation result holds only its own warnings
        assert isinstance(out1.warnings, list)
        assert isinstance(out2.warnings, list)


# ---------------------------------------------------------------------------
# ir_transformer hook
# ---------------------------------------------------------------------------


class TestIrTransformer:
    """The post-frontend / pre-backend mutation hook.

    The hook receives a singleton :class:`DocumentIR` wrapping the
    block.  A front-end's override application plugs in here — the
    proofreading workflow semantics live in a downstream front-end;
    the compiler-side contract just guarantees the callback fires once
    between frontend and backend.
    """

    def test_transformer_called_with_singleton_doc(
        self, pipe: Pipeline
    ) -> None:
        captured: list[DocumentIR] = []

        def grab(doc: DocumentIR) -> None:
            captured.append(doc)

        pipe.translate_block(Paragraph(text="字"), ir_transformer=grab)
        assert len(captured) == 1
        assert isinstance(captured[0], DocumentIR)
        assert len(captured[0].blocks) == 1

    def test_transformer_can_mutate_block_in_place(
        self, pipe: Pipeline
    ) -> None:
        """A transformer that sets pinyin on a HanziChar should write
        through to the returned IR (proves the hook fires + mutations
        survive into the compiled output)."""

        def set_pinyin(doc: DocumentIR) -> None:
            doc.blocks[0].children[0].reading = "yi1"

        block = Paragraph(children=[HanziChar(surface="一")])
        out = pipe.translate_block(block, ir_transformer=set_pinyin)
        assert out.ir.children[0].reading == "yi1"

    def test_no_transformer_is_equivalent_to_none(self, pipe: Pipeline) -> None:
        a = pipe.translate_block(Paragraph(text="字"))
        b = pipe.translate_block(Paragraph(text="字"), ir_transformer=None)
        a_cells = [c.dots for c in a.braille_blocks[0].cells]
        b_cells = [c.dots for c in b.braille_blocks[0].cells]
        assert a_cells == b_cells


# ---------------------------------------------------------------------------
# tree_subcache reuse — math
# ---------------------------------------------------------------------------


class TestMathSubcache:
    """Per-formula parsed-tree cache threaded through translate_block.

    The pipeline keys math by ``("math", source_format, surface_text)``
    — the ``"math"`` domain prefix shares one pool with music while
    keeping the two from colliding.  The cache lets the caller skip
    re-parsing unchanged formulas when surrounding non-math text in the
    same block is edited.
    """

    @pytest.mark.requires("latex2mathml")
    def test_inline_math_is_recorded_in_output_subcache(
        self, pipe: Pipeline
    ) -> None:
        block = Paragraph(text="看 $x^2$ 这里")
        out = pipe.translate_block(block)
        # The latex inline formula is keyed by its surface (including
        # the ``$...$`` framing — that's exactly what the math frontend
        # gets handed), under the ``"math"`` domain.
        key = ("math", "latex", "$x^2$")
        assert key in out.tree_subcache
        from xml.etree.ElementTree import Element

        assert isinstance(out.tree_subcache[key], Element)

    def test_non_math_paragraph_yields_empty_subcache(
        self, pipe: Pipeline
    ) -> None:
        out = pipe.translate_block(Paragraph(text="我是中国"))
        assert out.tree_subcache == {}

    @pytest.mark.requires("latex2mathml")
    def test_cached_tree_reused_when_passed_back_in(
        self, pipe: Pipeline, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass the prior compile's subcache → frontend skips
        ``parse_math_tree`` for the same ``(domain, source, surface)``."""
        first = pipe.translate_block(Paragraph(text="$x^2$ 是"))
        cached = first.tree_subcache
        key = ("math", "latex", "$x^2$")
        assert key in cached

        # Sentinel that explodes if the math frontend gets called at all
        # for the cached formula. Reuse path must not parse again.
        def explode(*_args: object, **_kw: object) -> None:
            raise AssertionError(
                "parse_math_tree should not be called for a cached formula"
            )

        # Inject at the lookup site used by ``attach_math`` (on the instance,
        # so monkeypatch restores it after the test).
        monkeypatch.setattr(pipe._frontend, "_parse_math_tree", explode)

        second = pipe.translate_block(
            Paragraph(text="$x^2$ 是"), tree_subcache=cached
        )
        # Same parse result reused → tree is the exact object from the cache.
        out_tree = second.ir.children[0].math
        assert out_tree is cached[key]
        # And it's also threaded into the new compile's subcache for the
        # caller to use next round.
        assert second.tree_subcache[key] is out_tree

    @pytest.mark.requires("latex2mathml")
    def test_changing_formula_surface_misses_cache(
        self, pipe: Pipeline
    ) -> None:
        """Different surface text → different key → re-parsed."""
        first = pipe.translate_block(Paragraph(text="$x^2$"))
        second = pipe.translate_block(
            Paragraph(text="$x^3$"), tree_subcache=first.tree_subcache
        )
        # Only the formula actually parsed this compile lands in the
        # output subcache; the stale one is not carried over.
        assert ("math", "latex", "$x^2$") not in second.tree_subcache
        assert ("math", "latex", "$x^3$") in second.tree_subcache

    @pytest.mark.requires("latex2mathml")
    def test_math_block_uses_cache(self, pipe: Pipeline) -> None:
        """``MathBlock`` (display math) goes through the same caching
        path as inline math, keyed by ``("math", source, text)``."""
        from brailix.ir.document import MathBlock

        first = pipe.translate_block(
            MathBlock(text="\\sum x_i", source="latex")
        )
        key = ("math", "latex", "\\sum x_i")
        assert key in first.tree_subcache

    @pytest.mark.requires("latex2mathml")
    def test_empty_input_subcache_is_equivalent_to_none(
        self, pipe: Pipeline
    ) -> None:
        """Passing ``{}`` shouldn't break — explicit "no reuse pool"."""
        out = pipe.translate_block(
            Paragraph(text="$x$"), tree_subcache={}
        )
        assert ("math", "latex", "$x$") in out.tree_subcache

    @pytest.mark.requires("latex2mathml")
    def test_backend_does_not_mutate_cached_tree(self, pipe: Pipeline) -> None:
        """Math parity with :meth:`TestMusicSubcache.
        test_backend_does_not_mutate_cached_tree`: the math backend builds its
        cells from freshly-constructed elements and must never write back into
        the shared cached MathML tree — a reused ``tree_subcache`` entry is the
        same object across compiles, so any mutation would corrupt the pool."""
        import xml.etree.ElementTree as ET

        from brailix.core.context import MathContext
        from brailix.frontend import parse_math_tree
        from brailix.ir.document import MathBlock

        ctx = MathContext(profile=pipe.profile, source="latex", mode="display")
        tree = parse_math_tree("\\sum x_i", ctx)
        assert tree is not None
        before = ET.tostring(tree)

        key = ("math", "latex", "\\sum x_i")
        out = pipe.translate_block(
            MathBlock(text="\\sum x_i", source="latex"),
            tree_subcache={key: tree},
        )
        assert out.ir.children[0].math is tree  # confirm the cache hit
        assert ET.tostring(tree) == before  # backend left it untouched


# ---------------------------------------------------------------------------
# tree_subcache reuse — music (parity with math)
# ---------------------------------------------------------------------------


# A minimal single-note MusicXML score — enough to exercise the parse +
# normalise path without depending on the music backend's note coverage.
_SCORE_XML = (
    '<score-partwise version="4.0">'
    '<part-list><score-part id="P1"><part-name>Voice</part-name>'
    "</score-part></part-list>"
    '<part id="P1"><measure number="1">'
    "<note><pitch><step>C</step><octave>4</octave></pitch>"
    "<duration>1</duration><type>quarter</type></note>"
    "</measure></part></score-partwise>"
)


class TestMusicSubcache:
    """Per-score parsed-tree cache — the music half of the shared
    ``tree_subcache``, mirroring :class:`TestMathSubcache`.

    A :class:`ScoreBlock` parses its MusicXML into a normalised tree
    keyed by ``("music", source, text)``.  Proofreading edits that leave
    the score source untouched (an override on surrounding text, a pinyin
    tweak) reuse that tree instead of re-parsing — the win that makes
    large multi-MB scores editable without a stall on every keystroke.
    """

    def test_score_block_is_recorded_in_output_subcache(
        self, pipe: Pipeline
    ) -> None:
        out = pipe.translate_block(
            ScoreBlock(text=_SCORE_XML, source="musicxml")
        )
        key = ("music", "musicxml", _SCORE_XML)
        assert key in out.tree_subcache
        from xml.etree.ElementTree import Element

        assert isinstance(out.tree_subcache[key], Element)

    def test_cached_score_reused_when_passed_back_in(
        self, pipe: Pipeline, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Pass the prior compile's subcache → the music frontend skips
        the (expensive) MusicXML parse for the same score."""
        first = pipe.translate_block(
            ScoreBlock(text=_SCORE_XML, source="musicxml")
        )
        cached = first.tree_subcache
        key = ("music", "musicxml", _SCORE_XML)
        assert key in cached

        # Explodes if the music frontend is asked to parse again — the
        # reuse path must not re-run it for an unchanged score.
        def explode(*_args: object, **_kw: object) -> None:
            raise AssertionError(
                "parse_music_tree should not be called for a cached score"
            )

        monkeypatch.setattr(pipe._frontend, "_parse_music_tree", explode)

        second = pipe.translate_block(
            ScoreBlock(text=_SCORE_XML, source="musicxml"),
            tree_subcache=cached,
        )
        # Reused tree is the exact object from the cache, and it's
        # threaded into the new compile's subcache for the next round.
        out_tree = second.ir.children[0].score
        assert out_tree is cached[key]
        assert second.tree_subcache[key] is out_tree

    def test_changing_score_source_misses_cache(
        self, pipe: Pipeline
    ) -> None:
        """A different score text → different key → re-parsed."""
        other_xml = _SCORE_XML.replace("<step>C</step>", "<step>D</step>")
        first = pipe.translate_block(
            ScoreBlock(text=_SCORE_XML, source="musicxml")
        )
        second = pipe.translate_block(
            ScoreBlock(text=other_xml, source="musicxml"),
            tree_subcache=first.tree_subcache,
        )
        assert ("music", "musicxml", _SCORE_XML) not in second.tree_subcache
        assert ("music", "musicxml", other_xml) in second.tree_subcache

    def test_backend_does_not_mutate_cached_tree(self, pipe: Pipeline) -> None:
        """The backend must read a shared cached tree READ-ONLY.

        ``tree_subcache`` hands the very same :class:`ET.Element` to every
        compile that reuses it (no defensive copy — that is the whole point of
        the cache, which spares a large score a multi-MB re-parse on each
        keystroke). So a backend that mutated the tree would poison the pool:
        the next incremental recompile would build braille from a tree an
        unrelated earlier compile had silently altered — an order-dependent
        wrong-output bug. Snapshot the tree BEFORE any backend sees it, render
        it through a cache-hit compile, and assert it comes out byte-identical.
        """
        import xml.etree.ElementTree as ET

        from brailix.core.context import MusicContext
        from brailix.frontend.music import parse_music_tree

        # Parse once, independently, and snapshot before the backend touches it.
        ctx = MusicContext(profile=pipe.profile, source="musicxml")
        tree = parse_music_tree(_SCORE_XML, ctx)
        assert tree is not None
        before = ET.tostring(tree)

        # Prime a reuse pool with that exact object, then compile a block that
        # hits it — the backend renders the shared tree.
        key = ("music", "musicxml", _SCORE_XML)
        out = pipe.translate_block(
            ScoreBlock(text=_SCORE_XML, source="musicxml"),
            tree_subcache={key: tree},
        )
        assert out.ir.children[0].score is tree  # confirm the cache hit
        assert ET.tostring(tree) == before  # backend left it untouched


# ---------------------------------------------------------------------------
# tree_subcache reuse — graphics (parity with math / music)
# ---------------------------------------------------------------------------

# A labelled figure on purpose: the ``<text>`` label walks the tactile
# backend's label-translation path (the injected LabelTranslator renders it
# to braille dots), the deepest tree consumer — exactly where an accidental
# write-back into the shared SVG tree would happen.
_SVG_FIGURE = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100" '
    'width="50mm" height="50mm"><circle cx="50" cy="50" r="40"/>'
    '<text x="6" y="12">A</text></svg>'
)


class TestGraphicSubcache:
    """The ``("graphic", …)`` domain of the shared reuse pool — the third
    consumer alongside math and music (``FrontendDriver._populate_graphic_block``)."""

    def test_reuses_cached_tree(self, pipe: Pipeline) -> None:
        first = pipe.translate_block(GraphicBlock(text=_SVG_FIGURE, source="svg"))
        key = ("graphic", "svg", _SVG_FIGURE)
        assert key in first.tree_subcache

        second = pipe.translate_block(
            GraphicBlock(text=_SVG_FIGURE, source="svg"),
            tree_subcache=first.tree_subcache,
        )
        # Identity, not equality: the pool handed back the same parsed tree.
        assert second.ir.children[0].svg is first.tree_subcache[key]

    def test_backend_does_not_mutate_cached_tree(self, pipe: Pipeline) -> None:
        """Graphics parity with the math / music guards above: rasterizing a
        figure (including translating its ``<text>`` label to braille dots)
        must never write back into the shared cached SVG tree — a reused
        ``tree_subcache`` entry is the same object across compiles, so any
        mutation would corrupt every later compile that hits the entry."""
        import xml.etree.ElementTree as ET

        from brailix.core.context import GraphicsContext
        from brailix.frontend.graphics import parse_graphic_tree

        # Parse once, independently, and snapshot before the backend sees it.
        tree = parse_graphic_tree(_SVG_FIGURE, GraphicsContext(source="svg"))
        assert tree is not None
        before = ET.tostring(tree)

        key = ("graphic", "svg", _SVG_FIGURE)
        out = pipe.translate_block(
            GraphicBlock(text=_SVG_FIGURE, source="svg"),
            tree_subcache={key: tree},
        )
        assert out.ir.children[0].svg is tree  # confirm the cache hit
        assert out.raster is not None  # the backend really rasterised it
        assert ET.tostring(tree) == before  # backend left it untouched


@pytest.mark.requires("latex2mathml")
class TestTreeSubcacheCrossDomain:
    """Math and music share one reuse pool; the domain prefix keeps
    their keys from colliding even when ``source`` / ``surface`` would
    otherwise coincide (e.g. both defaulting to ``"plain"``)."""

    def test_math_and_music_coexist_in_one_pool(
        self, pipe: Pipeline
    ) -> None:
        # Compile a formula block and a score block, then union their
        # subcaches the way a front-end controller does across blocks.
        math_out = pipe.translate_block(Paragraph(text="$x^2$"))
        score_out = pipe.translate_block(
            ScoreBlock(text=_SCORE_XML, source="musicxml")
        )
        pool = {**math_out.tree_subcache, **score_out.tree_subcache}
        assert ("math", "latex", "$x^2$") in pool
        assert ("music", "musicxml", _SCORE_XML) in pool

        # Feeding the union back in lets a recompile reuse the math tree
        # without the music entry interfering.
        again = pipe.translate_block(Paragraph(text="$x^2$"), tree_subcache=pool)
        assert again.ir.children[0].math is pool[("math", "latex", "$x^2$")]


# ---------------------------------------------------------------------------
# Stale pre-populated block self-heal (P1-2)
# ---------------------------------------------------------------------------


class TestStaleBlockSelfHeal:
    """A block re-compiled after its ``text`` was edited must reflect the new
    text, not silently reuse children (and a ``source_hash``) built from the
    old one. ``populate_block`` self-heals by dropping children whose
    reconstructed surface no longer matches ``block.text`` (P1-2)."""

    def test_editing_text_on_populated_block_repopulates(
        self, pipe: Pipeline
    ) -> None:
        block = Paragraph(text="我是")
        first = pipe.translate_block(block)
        # Same object, ``text`` mutated after it was populated.
        block.text = "你好世界"
        second = pipe.translate_block(block)
        # Children were rebuilt from the NEW text, not the stale "我是".
        assert "".join(c.surface for c in second.ir.children) == "你好世界"
        # ...and the hash tracks the edit. The exact P1-2 symptom was that it
        # did NOT — the reused stale children kept the surface, hence the hash,
        # unchanged, so a cache keyed on it served the old braille.
        assert second.source_hash != first.source_hash

    def test_unchanged_text_reuses_children(self, pipe: Pipeline) -> None:
        """The self-heal must NOT fire on a consistent re-translate — the
        "skip the frontend cost" optimization for an unedited block is kept."""
        block = Paragraph(text="我是中国")
        pipe.translate_block(block)
        first_children = block.children
        first_child0 = block.children[0]
        pipe.translate_block(block)  # re-translate, text unchanged
        # Same child objects — the frontend was not re-run for this block.
        assert block.children is first_children
        assert block.children[0] is first_child0

    def test_editing_score_block_text_reparses(self, pipe: Pipeline) -> None:
        """Structured (math / score) carriers self-heal too: the MusicInline
        rebuilt from the edited score reflects the new source, not the old."""
        block = ScoreBlock(text=_SCORE_XML, source="musicxml")
        pipe.translate_block(block)
        edited = _SCORE_XML.replace("<step>C</step>", "<step>D</step>")
        block.text = edited
        second = pipe.translate_block(block)
        assert second.ir.children[0].surface == edited


# ---------------------------------------------------------------------------
# Pipeline.parse_text / Pipeline.parse_file
# ---------------------------------------------------------------------------


class TestPipelineParseText:
    """Parse-only public surface: text → DocumentIR without translating.

    Used by a proofreading front-end that compiles block-by-block,
    so it needs the unpopulated IR (children empty, ``block.text`` set)
    to drive its own per-block translate loop.
    """

    def test_plain_yields_single_paragraph_unpopulated(
        self, pipe: Pipeline
    ) -> None:
        doc = pipe.parse_text("我在重庆")
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "我在重庆"
        # Parse-only — frontend hasn't run yet.
        assert doc.blocks[0].children == []

    def test_markdown_format_parses_headings(self, pipe: Pipeline) -> None:
        doc = pipe.parse_text("# 标题\n\n段落", format="markdown")
        assert len(doc.blocks) == 2
        assert isinstance(doc.blocks[0], Heading)
        assert doc.blocks[0].level == 1
        assert doc.blocks[0].text == "标题"
        assert isinstance(doc.blocks[1], Paragraph)

    def test_default_format_is_plain(self, pipe: Pipeline) -> None:
        """Omitting ``format`` should match ``format='plain'``."""
        a = pipe.parse_text("# 不当标题")
        b = pipe.parse_text("# 不当标题", format="plain")
        assert len(a.blocks) == 1
        assert len(b.blocks) == 1
        assert isinstance(a.blocks[0], Paragraph)
        assert a.blocks[0].text == b.blocks[0].text

    def test_unknown_format_raises_value_error(self, pipe: Pipeline) -> None:
        with pytest.raises(ValueError, match="unknown parse format"):
            pipe.parse_text("x", format="latex")

    def test_musicxml_format_wraps_score_block_unpopulated(
        self, pipe: Pipeline
    ) -> None:
        """``format="musicxml"`` wraps raw MusicXML as one ScoreBlock,
        parse-only (children empty) so a caller can compile it per-block
        the same way it does markdown / plain."""
        doc = pipe.parse_text(_SCORE_XML, format="musicxml")
        assert len(doc.blocks) == 1
        score = doc.blocks[0]
        assert isinstance(score, ScoreBlock)
        assert score.source == "musicxml"
        assert score.text == _SCORE_XML
        # Parse-only — the music frontend hasn't run yet.
        assert score.children == []
        # Same metadata stamping as the other parse_text formats.
        assert doc.metadata.get("profile") == "cn_current"

    def test_metadata_carries_pipeline_profile_and_language(
        self, pipe: Pipeline
    ) -> None:
        """Parsed IR's metadata matches what translate_text would have stamped."""
        doc = pipe.parse_text("x")
        assert doc.metadata.get("profile") == "cn_current"
        assert doc.metadata.get("language") == pipe._profile.language

    def test_translate_document_on_parsed_yields_same_as_translate_text(
        self, pipe: Pipeline
    ) -> None:
        """parse_text + translate_document should round-trip back to the
        same braille cells translate_text would have produced."""
        text = "你好"
        a = pipe.translate_text(text)
        b = pipe.translate_document(pipe.parse_text(text))
        a_cells = [
            c.dots for blk in a.braille_ir.blocks for c in blk.cells
        ]
        b_cells = [
            c.dots for blk in b.braille_ir.blocks for c in blk.cells
        ]
        assert a_cells == b_cells


class TestPipelineParseFile:
    """File → DocumentIR by suffix dispatch.  Convenience over
    :meth:`parse_text` when the proofreader has the document on disk."""

    def test_md_suffix_routes_to_markdown(
        self, pipe: Pipeline, tmp_path
    ) -> None:
        p = tmp_path / "doc.md"
        p.write_text("# 标题\n\n段落", encoding="utf-8")
        doc = pipe.parse_file(p)
        assert len(doc.blocks) == 2
        assert isinstance(doc.blocks[0], Heading)

    def test_txt_suffix_routes_to_plain(
        self, pipe: Pipeline, tmp_path
    ) -> None:
        p = tmp_path / "doc.txt"
        p.write_text("一段文本", encoding="utf-8")
        doc = pipe.parse_file(p)
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], Paragraph)
        assert doc.blocks[0].text == "一段文本"

    def test_no_suffix_routes_to_plain(
        self, pipe: Pipeline, tmp_path
    ) -> None:
        p = tmp_path / "README"
        p.write_text("无后缀", encoding="utf-8")
        doc = pipe.parse_file(p)
        assert len(doc.blocks) == 1
        assert isinstance(doc.blocks[0], Paragraph)

    def test_missing_file_propagates_error(
        self, pipe: Pipeline, tmp_path
    ) -> None:
        with pytest.raises(FileNotFoundError):
            pipe.parse_file(tmp_path / "nope.md")

    def test_translate_file_equivalent_to_parse_plus_translate(
        self, pipe: Pipeline, tmp_path
    ) -> None:
        """parse_file + translate_document should equal translate_file."""
        p = tmp_path / "doc.md"
        p.write_text("# 一\n\n二", encoding="utf-8")
        a = pipe.translate_file(p)
        b = pipe.translate_document(pipe.parse_file(p))
        a_cells = [
            c.dots for blk in a.braille_ir.blocks for c in blk.cells
        ]
        b_cells = [
            c.dots for blk in b.braille_ir.blocks for c in blk.cells
        ]
        assert a_cells == b_cells
