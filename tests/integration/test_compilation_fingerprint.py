"""Compilation-configuration fingerprint: cache keys and populated-IR reuse.

Two silent-wrong-output holes closed by :mod:`brailix.pipeline._fingerprint`:

* ``block_hash`` / ``CompiledBlock.source_hash`` keyed only on
  ``(surface, profile name, structure)`` — the same text compiled under a
  different resolver / user dictionary / edited same-named profile hashed
  identically, so a cache served the other configuration's braille.
* ``populate_block`` skipped the frontend whenever ``children`` were present
  and matched ``block.text`` — a :class:`DocumentIR` populated by pipeline A
  kept A's semantic IR when translated through a differently-configured
  pipeline B, so B's translation silently used A's tokenization / readings.
* ``translate_text`` returned IR wearing neither ``text`` nor a stamp, which
  is the hand-built shape the stale-heal deliberately leaves alone — so the
  hole above stayed open for anything that started from ``translate_text``
  rather than ``parse_text``, which is the shorter of the two entry points and
  the one an example reaches for.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from brailix.core.span import Span
from brailix.input import parse_markdown
from brailix.ir.document import DocumentIR, Paragraph, Table
from brailix.ir.inline import LatinWord
from brailix.pipeline import Pipeline, block_hash

PROFILES_DIR = Path(__file__).resolve().parents[2] / "brailix" / "profiles"

# A reading that no resolver would produce for 重庆, so the braille output
# provably reflects WHICH configuration ran (dict format: space-separated
# tone-numbered syllables, the same shape the zh frontend consumes).
ALT_DICT = {"重庆": "zhong4 qing4"}

TEXT = "我在重庆。"


@pytest.fixture(scope="module")
def base() -> Pipeline:
    return Pipeline(profile="cn_current")


@pytest.fixture(scope="module")
def with_dict() -> Pipeline:
    return Pipeline(profile="cn_current", user_pinyin_dict=dict(ALT_DICT))


@pytest.fixture()
def shadow_profile_dir(tmp_path: Path) -> Path:
    """A user profile drop shadowing builtin ``cn_current`` by name, with
    different content (one extra feature key)."""
    src = PROFILES_DIR / "cn_current.json"
    dest = tmp_path / "cn_current.json"
    shutil.copy(src, dest)
    payload = json.loads(dest.read_text(encoding="utf-8"))
    payload.setdefault("features", {})["test.fingerprint_probe"] = True
    dest.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    return tmp_path


# ---------------------------------------------------------------------------
# Pipeline.fingerprint identity
# ---------------------------------------------------------------------------


class TestFingerprintIdentity:
    def test_equal_configuration_equal_fingerprint(self, base: Pipeline) -> None:
        assert Pipeline(profile="cn_current").fingerprint == base.fingerprint

    def test_resolver_changes_fingerprint(self, base: Pipeline) -> None:
        other = Pipeline(profile="cn_current", resolver="null")
        assert other.fingerprint != base.fingerprint

    def test_user_pinyin_dict_changes_fingerprint(
        self, base: Pipeline, with_dict: Pipeline
    ) -> None:
        assert with_dict.fingerprint != base.fingerprint

    def test_user_pinyin_dict_is_order_insensitive(self) -> None:
        d1 = {"重庆": "chong2 qing4", "银行": "yin2 hang2"}
        d2 = dict(reversed(list(d1.items())))
        p1 = Pipeline(profile="cn_current", user_pinyin_dict=d1)
        p2 = Pipeline(profile="cn_current", user_pinyin_dict=d2)
        assert p1.fingerprint == p2.fingerprint

    def test_mode_changes_fingerprint(self, base: Pipeline) -> None:
        # Braille output is mode-independent, but a cached compile also
        # replays its recorded warnings, whose levels pivot on the mode.
        strict = Pipeline(profile="cn_current", mode="strict")
        assert strict.fingerprint != base.fingerprint

    def test_same_profile_name_different_content_changes_fingerprint(
        self, base: Pipeline, shadow_profile_dir: Path
    ) -> None:
        shadowed = Pipeline(
            profile="cn_current",
            extra_profile_paths=(str(shadow_profile_dir),),
        )
        assert shadowed.fingerprint != base.fingerprint


# ---------------------------------------------------------------------------
# source_hash covers the configuration
# ---------------------------------------------------------------------------


class TestSourceHashConfigCoverage:
    def _hash(self, pipe: Pipeline) -> str:
        return pipe.translate_block(pipe.parse_text(TEXT).blocks[0]).source_hash

    def test_stable_across_equal_pipelines(self, base: Pipeline) -> None:
        assert self._hash(base) == self._hash(Pipeline(profile="cn_current"))

    def test_resolver_changes_source_hash(self, base: Pipeline) -> None:
        assert self._hash(base) != self._hash(
            Pipeline(profile="cn_current", resolver="null")
        )

    def test_user_pinyin_dict_changes_source_hash(
        self, base: Pipeline, with_dict: Pipeline
    ) -> None:
        assert self._hash(base) != self._hash(with_dict)

    def test_same_profile_name_different_content_changes_source_hash(
        self, base: Pipeline, shadow_profile_dir: Path
    ) -> None:
        shadowed = Pipeline(
            profile="cn_current",
            extra_profile_paths=(str(shadow_profile_dir),),
        )
        assert self._hash(base) != self._hash(shadowed)

    def test_module_block_hash_fingerprint_salt_flips_digest(
        self, base: Pipeline
    ) -> None:
        blk = base.parse_text(TEXT).blocks[0]
        unsalted = block_hash(blk, "cn_current")
        salted = block_hash(blk, "cn_current", fingerprint=base.fingerprint)
        assert unsalted != salted
        assert len(unsalted) == len(salted) == 64
        # Same salt → same digest (the salted form is still deterministic).
        assert salted == block_hash(
            blk, "cn_current", fingerprint=base.fingerprint
        )


# ---------------------------------------------------------------------------
# Populated DocumentIR does not stick to the first pipeline's configuration
# ---------------------------------------------------------------------------


class TestPopulatedDocConfigInvalidation:
    def test_second_pipeline_rebuilds_children_and_braille(
        self, base: Pipeline, with_dict: Pipeline
    ) -> None:
        doc = base.parse_text(TEXT)
        out_a = base.translate_document(doc).render("unicode")
        children_a = doc.blocks[0].children
        assert children_a  # populated by A

        out_b = with_dict.translate_document(doc).render("unicode")
        assert doc.blocks[0].children is not children_a  # frontend re-ran
        assert out_b != out_a  # ...and B's user dictionary actually applied

        # B's own re-translation now reuses B's children (stamp matches).
        children_b = doc.blocks[0].children
        with_dict.translate_document(doc)
        assert doc.blocks[0].children is children_b

    def test_translate_text_ir_is_invalidated_by_different_pipeline(
        self, base: Pipeline, with_dict: Pipeline
    ) -> None:
        # ``translate_text`` used to assemble its own Paragraph carrying only
        # ``children`` — no ``text``, no stamp — which is exactly the shape the
        # population contract calls hand-built and reuses verbatim. So this
        # result, handed to any other pipeline, kept A's tokenization and
        # readings and produced braille B never compiled. It stamps now, like
        # every other populate path.
        result_a = base.translate_text(TEXT)
        blk = result_a.ir.blocks[0]
        assert blk.text == TEXT
        assert blk.frontend_fingerprint == base.fingerprint

        out_b = with_dict.translate_document(result_a.ir).render("unicode")
        assert out_b == with_dict.translate_text(TEXT).render("unicode")
        assert out_b != result_a.render("unicode")

    def test_translate_text_ir_is_reused_by_equal_pipeline(
        self, base: Pipeline
    ) -> None:
        # The other half of the contract: stamping must not cost the documented
        # "re-translation skips the frontend cost" reuse when the configuration
        # is the same one.
        result = base.translate_text(TEXT)
        children = result.ir.blocks[0].children
        Pipeline(profile="cn_current").translate_document(result.ir)
        assert result.ir.blocks[0].children is children

    def test_equal_configuration_still_reuses_children(
        self, base: Pipeline
    ) -> None:
        doc = base.parse_text(TEXT)
        base.translate_document(doc)
        children = doc.blocks[0].children
        Pipeline(profile="cn_current").translate_document(doc)
        assert doc.blocks[0].children is children

    def test_hand_built_children_are_used_as_is(
        self, base: Pipeline, with_dict: Pipeline
    ) -> None:
        # Never stamped by a pipeline → the documented hand-built contract:
        # children are consumed verbatim by every pipeline.
        para = Paragraph(
            text="AB",
            children=[LatinWord(surface="AB", span=Span(0, 2))],
        )
        from brailix.ir.document import DocumentIR

        doc = DocumentIR(blocks=[para])
        hand_children = para.children
        base.translate_document(doc)
        assert para.children is hand_children
        with_dict.translate_document(doc)
        assert para.children is hand_children

    def test_config_invalidation_clears_the_stamp_with_the_children(
        self, base: Pipeline
    ) -> None:
        """A stamp describes the children currently on the block, so dropping
        them for a configuration mismatch must drop it too.

        Normally the rebuild immediately re-stamps and nothing shows. When the
        rebuild *aborts* — strict mode raising on the first diagnostic, an
        adapter blowing up — the old path left the block at ``children == []``
        while still advertising the configuration that built the children it
        no longer has: the "no stamped-but-empty block" invariant that populate
        maintains on the success path, broken on the failure path.
        """
        from brailix.core.errors import StrictModeError
        from brailix.ir.document import DocumentIR, MathBlock

        # Populated by A: an unknown math source soft-fails to a carrier with
        # no tree (a MATH_ADAPTER_MISSING warning), which is still a populate.
        block = MathBlock(text="x", source="no_such_source")
        doc = DocumentIR(blocks=[block])
        base.translate_document(doc)
        assert block.children
        assert block.frontend_fingerprint == base.fingerprint

        # B differs in configuration (mode is fingerprinted) AND raises on the
        # first warning, so its rebuild aborts right after the invalidation.
        strict = Pipeline(profile="cn_current", mode="strict")
        assert strict.fingerprint != base.fingerprint
        with pytest.raises(StrictModeError):
            strict.translate_document(doc)
        assert block.children == []
        assert block.frontend_fingerprint is None

    def test_table_cells_rebased_after_config_invalidation(
        self, base: Pipeline, with_dict: Pipeline
    ) -> None:
        # The config-staleness drop happens INSIDE the table-cell loop; the
        # rebuilt second cell must get its spans rebased to row coordinates
        # exactly like a fresh populate (regression: reading the pre-heal
        # children count skipped the rebase and pointed at column 0).
        doc = parse_markdown(
            "| AB | CDE |\n| --- | --- |\n",
            profile="cn_current",
            language="zh-CN",
        )
        base.translate_document(doc)
        with_dict.translate_document(doc)
        table = next(b for b in doc.blocks if isinstance(b, Table))
        c1 = table.rows[0].cells[1].children[0]
        assert (c1.span.start, c1.span.end) == (4, 7)  # "CDE" row-local


# ---------------------------------------------------------------------------
# Runtime registry re-registration invalidates fingerprint-derived caching
# ---------------------------------------------------------------------------


class _ShoutSegmenter:
    """A replacement whose behaviour is observably different from the
    default: the whole text comes back as ONE upper-cased latin run."""

    name = "probe"

    def segment(self, block, ctx=None):  # noqa: ANN001, ANN201 — protocol shape
        from brailix.ir.inline import Segment

        text = block.text or ""
        return [
            Segment(
                type="latin_text",
                surface=text.upper(),
                span=Span(0, len(text)),
            )
        ]


class TestRegistryReRegisterInvalidation:
    """The registries allow re-registering an implementation under a live
    name, and the frontend re-resolves names on every run — so replacing
    an adapter mid-process changes what a pipeline compiles WITHOUT any
    Pipeline field changing. ``Pipeline.fingerprint`` folds every
    compilation-relevant registry's ``generation`` in, so the swap
    advances the fingerprint, flips ``source_hash``, and invalidates the
    ``frontend_fingerprint`` stamps on previously populated IR — no cache
    layer can keep serving the replaced implementation's braille."""

    def test_re_register_advances_fingerprint_and_source_hash(self) -> None:
        from brailix.frontend.segment import DefaultSegmenter, segmenter_registry

        with segmenter_registry.overriding("probe", DefaultSegmenter):
            pipe = Pipeline(profile="cn_current", segmenter="probe")
            fp1 = pipe.fingerprint
            h1 = pipe.translate_block(Paragraph(text=TEXT)).source_hash

            segmenter_registry.register("probe", DefaultSegmenter)

            assert pipe.fingerprint != fp1
            assert (
                pipe.translate_block(Paragraph(text=TEXT)).source_hash != h1
            )

    def test_registration_during_a_compile_cannot_collide_cache_keys(
        self,
    ) -> None:
        """The window the test above steps over: a registration landing *inside*
        a compile, not between two.

        ``CompilationSession.begin`` snapshots the fingerprint for the frontend,
        but ``compile_block`` used to re-read ``pipeline.fingerprint`` at the end
        to build ``source_hash``. A registration between those two reads made
        run 1's key describe the *new* epoch while its cells came from the old
        one — so run 1 and run 2 hashed identically and rendered differently,
        which is the one thing a cache key must never allow.

        Swapping the pinyin resolver is what makes this observable: the readings
        change, so the braille changes, while surface and structure — the other
        inputs to ``block_hash`` — stay byte-identical. (A segmenter swap would
        also move the surface, and the key would flip for the wrong reason.)
        """
        from dataclasses import replace as dc_replace

        from brailix.frontend.zh.pinyin.registry import resolver_registry

        class _ResolverB:
            name = "probe"

            def resolve(self, tokens, ctx=None):  # noqa: ANN001, ANN201
                return [dc_replace(t, pinyin="zhong4 qing4") for t in tokens]

        class _ResolverA:
            """Re-registers itself as ``_ResolverB`` the first time it runs, so
            the generation moves mid-compile — after the session snapshot and
            before the cache key is built."""

            name = "probe"

            def __init__(self) -> None:
                self.fired = False

            def resolve(self, tokens, ctx=None):  # noqa: ANN001, ANN201
                if not self.fired:
                    self.fired = True
                    resolver_registry.register("probe", _ResolverB)
                return [dc_replace(t, pinyin="chong2 qing4") for t in tokens]

        with resolver_registry.overriding("probe", _ResolverA):
            pipe = Pipeline(profile="cn_current", resolver="probe")
            first = pipe.translate_block(Paragraph(text="重庆"))
            second = pipe.translate_block(Paragraph(text="重庆"))

        dots = lambda cb: [  # noqa: E731 — tiny local shorthand
            c.dots for bb in cb.braille_blocks for c in bb.cells
        ]
        # The readings really did differ — otherwise the guard proves nothing.
        assert dots(first) != dots(second)
        assert first.source_hash != second.source_hash

    def test_a_registration_mid_compile_is_reported(self) -> None:
        """Pinning the key keeps the cache honest, but the run itself still
        straddled two epochs: adapter names resolve on every use, so part of the
        block met the outgoing implementation and part met its replacement. No
        fingerprint describes a blend, so the run says so."""
        from brailix.frontend.segment import DefaultSegmenter, segmenter_registry

        class _SelfRegistering:
            name = "probe"

            def __init__(self) -> None:
                self.fired = False

            def segment(self, block, ctx=None):  # noqa: ANN001, ANN201
                if not self.fired:
                    self.fired = True
                    segmenter_registry.register("probe", DefaultSegmenter)
                return DefaultSegmenter().segment(block, ctx)

        with segmenter_registry.overriding("probe", _SelfRegistering):
            pipe = Pipeline(profile="cn_current", resolver="null", segmenter="probe")
            drifted = pipe.translate_block(Paragraph(text=TEXT))
            settled = pipe.translate_block(Paragraph(text=TEXT))

        assert "COMPILE_EPOCH_CHANGED" in [w.code for w in drifted.warnings]
        # And it is not a permanent state: once registration settles, a compile
        # that ran entirely inside one epoch reports nothing.
        assert "COMPILE_EPOCH_CHANGED" not in [w.code for w in settled.warnings]

    def test_a_drifted_block_carries_no_reusable_cache_key(self) -> None:
        """Reporting the blend was not enough on its own.

        A caller that stores ``result.source_hash -> result`` — the documented
        use of the field — does not have to read diagnostics to do it. So the
        drifted compile went into the cache under the *ordinary* key, and the
        next clean compile of the same block looked that key up and got the
        blend: one key, two braillings, which is precisely what folding the
        registry generation into the fingerprint exists to prevent.

        Two things now stop it. ``cacheable`` says don't store this, and the
        key itself is retired to a one-off value, so a caller that ignores the
        flag records a dead entry instead of poisoning a live one.
        """
        from brailix.frontend.segment import DefaultSegmenter, segmenter_registry

        class _SelfRegistering:
            name = "probe"

            def __init__(self) -> None:
                self.fired = False

            def segment(self, block, ctx=None):  # noqa: ANN001, ANN201
                if not self.fired:
                    self.fired = True
                    segmenter_registry.register("probe", DefaultSegmenter)
                return DefaultSegmenter().segment(block, ctx)

        with segmenter_registry.overriding("probe", _SelfRegistering):
            pipe = Pipeline(
                profile="cn_current", resolver="null", segmenter="probe"
            )
            drifted = pipe.translate_block(Paragraph(text=TEXT))
            settled = pipe.translate_block(Paragraph(text=TEXT))
            settled_again = pipe.translate_block(Paragraph(text=TEXT))

        assert drifted.cacheable is False
        assert settled.cacheable is True
        # The clean pair agree, so the key really is stable across compiles...
        assert settled.source_hash == settled_again.source_hash
        # ...and the drifted one cannot collide with it, nor with itself.
        assert drifted.source_hash != settled.source_hash
        assert drifted.source_hash.startswith("uncacheable-")

    def test_two_drifted_compiles_never_share_a_key(self) -> None:
        """The retired key must be one-off, not merely "different from the
        clean one": two blends of the same block are not interchangeable
        either, and a persisted cache outlives the process that made them."""
        from brailix.pipeline._incremental import _uncacheable_hash

        digest = "0" * 64
        assert _uncacheable_hash(digest) != _uncacheable_hash(digest)
        assert digest in _uncacheable_hash(digest)  # still recognisable

    @pytest.mark.parametrize(
        "translate",
        [
            pytest.param(
                lambda pipe: pipe.translate_text(TEXT), id="translate_text"
            ),
            pytest.param(
                lambda pipe: pipe.translate_document(
                    DocumentIR(blocks=[Paragraph(text=TEXT)])
                ),
                id="translate_document",
            ),
        ],
    )
    def test_whole_document_paths_report_drift_too(self, translate) -> None:
        """The blend is a property of the run, not of the result shape.

        The check lived only in the block-level compile, on the reasoning that
        only it returns a cache key. But ``translate_text`` and
        ``translate_document`` resolve adapter names on every node exactly the
        same way, so a registration landing mid-run leaves those results just
        as mixed — and they said nothing at all about it.
        """
        from brailix.frontend.segment import DefaultSegmenter, segmenter_registry

        class _SelfRegistering:
            name = "probe"

            def __init__(self) -> None:
                self.fired = False

            def segment(self, block, ctx=None):  # noqa: ANN001, ANN201
                if not self.fired:
                    self.fired = True
                    segmenter_registry.register("probe", DefaultSegmenter)
                return DefaultSegmenter().segment(block, ctx)

        with segmenter_registry.overriding("probe", _SelfRegistering):
            pipe = Pipeline(
                profile="cn_current", resolver="null", segmenter="probe"
            )
            drifted = translate(pipe)
            settled = translate(pipe)

        assert "COMPILE_EPOCH_CHANGED" in [w.code for w in drifted.warnings]
        assert "COMPILE_EPOCH_CHANGED" not in [w.code for w in settled.warnings]

    @pytest.mark.parametrize(
        "install",
        [
            pytest.param(
                lambda registry, handler: registry.__setitem__("zh", handler),
                id="setitem",
            ),
            pytest.param(
                lambda registry, handler: registry.__ior__({"zh": handler}),
                id="ior",
            ),
        ],
    )
    def test_swapping_a_boundary_handler_moves_the_fingerprint(
        self, install
    ) -> None:
        """``boundary_registry`` is a documented extension point that changes
        the braille — it inserts the space between a hanzi run and a Latin
        word, the connector before a number — so it belongs in the fingerprint.

        It was left out on the strength of its type: the fold walked a list of
        ``Registry`` instances and this one is a dict. Nothing else covered the
        gap either, because the nodes a handler inserts carry ``surface=""``:
        the stale-children check compares a reconstructed surface against
        ``block.text`` and sees no difference. Measured before the fix,
        ``Paragraph("x轴")`` compiled to ``⠰⠭⠤⠀`` and then to ``⠰⠭⠀`` under one
        ``source_hash``.

        Both spellings, because the first fix only closed the first one:
        ``registry |= {...}`` is inherited straight from ``dict`` and changed
        the handler without passing through the counted ``__setitem__``, so
        the very same "one hash, two braillings" came back through the
        operator form. ``tests/frontend/test_boundary_registry.py`` covers the
        whole mutation surface; this pins the two ends of the chain that
        matter to a cache.
        """
        from brailix.frontend import boundary_registry

        pipe = Pipeline(profile="cn_current", resolver="null")
        fp_before = pipe.fingerprint
        first = pipe.translate_block(Paragraph(text="x轴"))

        original = boundary_registry.get("zh")
        try:
            install(boundary_registry, lambda nodes, profile: list(nodes))
            assert pipe.fingerprint != fp_before
            second = pipe.translate_block(Paragraph(text="x轴"))
        finally:
            if original is not None:
                boundary_registry["zh"] = original

        dots = lambda cb: [  # noqa: E731 — tiny local shorthand
            c.dots for bb in cb.braille_blocks for c in bb.cells
        ]
        # The handler really did change the output — otherwise this proves
        # nothing about the key.
        assert dots(first) != dots(second)
        assert first.source_hash != second.source_hash

    def test_swapping_a_boundary_handler_rebuilds_populated_children(
        self,
    ) -> None:
        """The second half: an already-populated block must not keep spacing
        produced by a handler that has since been replaced. The zero-width
        surfaces mean text comparison can't detect it; only the fingerprint
        stamp can."""
        from brailix.frontend import boundary_registry

        pipe = Pipeline(profile="cn_current", resolver="null")
        block = Paragraph(text="x轴")
        first = pipe.translate_block(block)

        original = boundary_registry.get("zh")
        try:
            boundary_registry["zh"] = lambda nodes, profile: list(nodes)
            second = pipe.translate_block(block)  # same, already-populated
        finally:
            if original is not None:
                boundary_registry["zh"] = original

        dots = lambda cb: [  # noqa: E731
            c.dots for bb in cb.braille_blocks for c in bb.cells
        ]
        assert dots(first) != dots(second), (
            "the populated block kept children built by the replaced handler"
        )

    def test_every_documented_extension_registry_is_fingerprinted(self) -> None:
        """Derived from the extension surface, not from a hand-list.

        ``boundary_registry`` was absent because the fold enumerated
        ``Registry`` instances and it is a dict — a membership rule based on
        type rather than on "does replacing an entry change the braille". This
        checks the rule the fold actually needs: everything the extension
        manifest publishes as a compile-time registry carries a ``generation``
        and is folded in.
        """
        from brailix.pipeline._fingerprint import _compilation_registries

        folded = {id(r) for r in _compilation_registries()}

        # The renderer registry is deliberately outside: rendering happens
        # after the braille a cache stores, so swapping one cannot stale it.
        from brailix.frontend import boundary_registry, language_frontend_registry
        from brailix.frontend.graphics.registry import graphic_source_registry
        from brailix.frontend.math.registry import math_source_registry
        from brailix.frontend.music.registry import music_source_registry
        from brailix.frontend.normalize import normalizer_registry
        from brailix.frontend.segment import segmenter_registry

        compile_time = {
            "segmenter_registry": segmenter_registry,
            "normalizer_registry": normalizer_registry,
            "language_frontend_registry": language_frontend_registry,
            "boundary_registry": boundary_registry,
            "math_source_registry": math_source_registry,
            "music_source_registry": music_source_registry,
            "graphic_source_registry": graphic_source_registry,
        }
        missing = [
            name for name, reg in compile_time.items() if id(reg) not in folded
        ]
        assert not missing, (
            f"compile-time registries missing from the fingerprint: {missing} "
            f"— replacing an entry in one changes the braille while the cache "
            f"key stays put"
        )
        for name, reg in compile_time.items():
            assert isinstance(getattr(reg, "generation", None), int), (
                f"{name} has no generation counter to fold"
            )

    def test_steady_state_fingerprint_is_stable(self) -> None:
        # No registration churn between the reads → the cached fold is
        # returned as-is, and an equal-configuration pipeline built in the
        # same registry state agrees.
        pipe = Pipeline(profile="cn_current")
        fp = pipe.fingerprint
        assert pipe.fingerprint == fp
        assert Pipeline(profile="cn_current").fingerprint == fp

    def test_replaced_implementation_reruns_on_populated_block(self) -> None:
        # The sharp edge: the SAME block object was populated before the
        # swap. Its stamp no longer matches, so the re-translate drops the
        # stale children and the NEW implementation observably runs.
        from brailix.frontend.segment import DefaultSegmenter, segmenter_registry

        with segmenter_registry.overriding("probe", DefaultSegmenter):
            pipe = Pipeline(
                profile="cn_current", segmenter="probe", resolver="null"
            )
            block = Paragraph(text="abc")
            first = pipe.translate_block(block)
            children1 = block.children
            assert children1

            segmenter_registry.register("probe", _ShoutSegmenter)

            second = pipe.translate_block(block)
            assert block.children is not children1  # stamp invalidated
            surfaces = "".join(c.surface for c in block.children)
            assert surfaces == "ABC"  # the replacement actually ran
            dots = lambda cb: [  # noqa: E731 — tiny local shorthand
                c.dots for bb in cb.braille_blocks for c in bb.cells
            ]
            assert dots(second) != dots(first)


# ---------------------------------------------------------------------------
# Graphic asset-resolver identity
# ---------------------------------------------------------------------------

_ASSET_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 10 10" '
    b'width="10mm" height="10mm"><rect width="10" height="10"/></svg>'
)


class TestAssetResolverIdentity:
    """What a graphic's asset reference resolves to is part of the compiled
    output (resolved bytes are inlined into the tree), so the resolver's
    identity folds into ``Pipeline.fingerprint``: same-name references
    through different resolvers must not share fingerprints — while the
    no-resolver steady state and a shared resolver instance still agree."""

    def test_different_resolver_instances_fingerprint_apart(self) -> None:
        a = Pipeline(profile="cn_current", asset_resolver=lambda n: b"a")
        b = Pipeline(profile="cn_current", asset_resolver=lambda n: b"b")
        assert a.fingerprint != b.fingerprint

    def test_no_resolver_and_shared_instance_agree(self, base: Pipeline) -> None:
        assert Pipeline(profile="cn_current").fingerprint == base.fingerprint

        def shared(name: str) -> bytes | None:
            return _ASSET_SVG

        p1 = Pipeline(profile="cn_current", asset_resolver=shared)
        p2 = Pipeline(profile="cn_current", asset_resolver=shared)
        assert p1.fingerprint == p2.fingerprint
        assert p1.fingerprint != base.fingerprint  # resolver ≠ no resolver

    def test_declared_cache_identity_shares_across_instances(self) -> None:
        class ContentAddressed:
            cache_identity = "assets:sha256:abc123"

            def __call__(self, name: str) -> bytes | None:
                return _ASSET_SVG

        p1 = Pipeline(profile="cn_current", asset_resolver=ContentAddressed())
        p2 = Pipeline(profile="cn_current", asset_resolver=ContentAddressed())
        assert p1.fingerprint == p2.fingerprint

    def test_late_bound_resolver_advances_fingerprint_and_reaches_driver(
        self,
    ) -> None:
        # The front-end wiring: bind a resolver onto an ALREADY-BUILT
        # pipeline (``pipe.asset_resolver = ...``). The fingerprint must
        # advance, and — the regression half — the next run must sync the
        # late-bound resolver onto the frontend driver instead of keeping
        # the constructor-time snapshot (which was None, silently
        # soft-failing every image). Dependency-free: the driver sync is
        # asserted directly, so this guard holds even where the ``image``
        # adapter's Pillow extra is absent; the end-to-end consultation is
        # pinned separately below.
        from brailix.ir.document import GraphicBlock

        pipe = Pipeline(profile="cn_current", resolver="null")
        fp0 = pipe.fingerprint

        def resolver(name: str) -> bytes | None:
            return _ASSET_SVG

        pipe.asset_resolver = resolver
        assert pipe.fingerprint != fp0

        pipe.translate_block(GraphicBlock(text="media/image1.png", source="image"))
        assert pipe._frontend.asset_resolver is resolver

    def test_late_bound_resolver_is_actually_consulted(self) -> None:
        # End-to-end half of the wiring regression: the ``image`` source
        # adapter really consults the late-bound resolver. The adapter
        # registers under the ``graphics`` extra (Pillow), so this leg
        # skips where that extra isn't installed — the driver-sync guard
        # above still covers the mechanism there.
        pytest.importorskip("PIL")
        from brailix.ir.document import GraphicBlock

        pipe = Pipeline(profile="cn_current", resolver="null")

        calls: list[str] = []

        def resolver(name: str) -> bytes | None:
            calls.append(name)
            return _ASSET_SVG

        pipe.asset_resolver = resolver
        block = GraphicBlock(text="media/image1.png", source="image")
        pipe.translate_block(block)
        assert calls == ["media/image1.png"]


# ---------------------------------------------------------------------------
# Configuration is read-only once constructed
# ---------------------------------------------------------------------------


class TestCompileConfigIsImmutable:
    """Every compile-relevant field is consumed ONCE, in ``__post_init__``, to
    build the frontend driver and hash ``_fingerprint_base``. Nothing re-reads
    them, so assigning one on a live pipeline used to land in one of two silent
    failure modes:

    * the write was ignored outright (``resolver`` / ``analyzer`` / a rebound
      ``user_pinyin_dict`` — the driver kept its own copy), or
    * it half-applied: mutating the user dictionary *in place* really did
      change the braille, while ``fingerprint`` and every ``source_hash``
      folded from it stayed byte-identical. That is precisely the "same cache
      key, two different compiles" hole the fingerprint exists to close —
      measured before the fix, ``Paragraph("重庆")`` compiled to two different
      cell strings under one ``source_hash``.

    So the fields are read-only after construction and reconfiguring means
    deriving a new pipeline. ``asset_resolver`` and ``default_renderer`` stay
    assignable — see ``_FROZEN_CONFIG_FIELDS`` for why neither can go stale.
    """

    @pytest.mark.parametrize(
        ("field", "value"),
        [
            ("profile", "cn_ncb"),
            ("mode", "strict"),
            ("segmenter", "regex"),
            ("normalizer", "default"),
            ("analyzer", "char"),
            ("resolver", "pypinyin"),
            ("user_pinyin_dict", dict(ALT_DICT)),
            ("extra_profile_paths", ("/nonexistent",)),
        ],
    )
    def test_assigning_a_compile_field_raises(
        self, field: str, value: object
    ) -> None:
        pipe = Pipeline(profile="cn_current", resolver="null")
        with pytest.raises(AttributeError, match="read-only after construction"):
            setattr(pipe, field, value)

    def test_the_frozen_set_covers_every_fingerprinted_field(self) -> None:
        """A new compile-relevant constructor argument must join the frozen
        set, or it reopens the hole for itself. Pinned as an exact set: the
        two exempt fields are exempt for stated reasons (see
        ``_FROZEN_CONFIG_FIELDS``), and a third one appearing silently is the
        regression."""
        from dataclasses import fields as dc_fields

        from brailix.pipeline import _FROZEN_CONFIG_FIELDS

        public = {
            f.name for f in dc_fields(Pipeline) if f.init and not f.name.startswith("_")
        }
        assert public - _FROZEN_CONFIG_FIELDS == {
            "default_renderer",
            "asset_resolver",
        }

    def test_user_dict_cannot_be_mutated_in_place(self) -> None:
        # The in-place path is the dangerous one: it used to change the
        # braille while leaving the digest untouched.
        pipe = Pipeline(profile="cn_current", user_pinyin_dict={})
        with pytest.raises(TypeError):
            pipe.user_pinyin_dict["重庆"] = "zhong4 qing4"  # type: ignore[index]

    def test_caller_dict_is_snapshotted_at_construction(self) -> None:
        """The caller keeps their own dictionary and may go on editing it; the
        pipeline's digest was computed from the contents at construction, so it
        must hold a copy nobody else can reach."""
        caller = {"我": "wo3"}
        pipe = Pipeline(profile="cn_current", user_pinyin_dict=caller)
        caller["重庆"] = "zhong4 qing4"
        assert dict(pipe.user_pinyin_dict) == {"我": "wo3"}

    def test_replace_is_the_reconfigure_path(self) -> None:
        """``dataclasses.replace`` rebuilds the driver and the digest, so the
        new dictionary both moves the cache key AND reaches the frontend —
        the two halves that came apart when the field was assignable."""
        from dataclasses import replace

        base_pipe = Pipeline(profile="cn_current", resolver="null")
        derived = replace(base_pipe, user_pinyin_dict=dict(ALT_DICT))

        assert derived.fingerprint != base_pipe.fingerprint
        b1 = base_pipe.translate_block(Paragraph(text="重庆"))
        b2 = derived.translate_block(Paragraph(text="重庆"))
        assert b1.source_hash != b2.source_hash
        # ... and the dictionary actually ran: the null resolver leaves 重庆
        # unread (blank cells), the dictionary entry gives it a reading.
        dots = lambda cb: [  # noqa: E731 — tiny local shorthand
            c.dots for bb in cb.braille_blocks for c in bb.cells
        ]
        assert dots(b1) != dots(b2)

    def test_replace_carries_the_whole_configuration(self) -> None:
        """Deriving must not silently reset the knobs the caller didn't name —
        otherwise "reconfigure by replace" would trade one silent-wrong-output
        bug for another."""
        from dataclasses import replace

        full = Pipeline(
            profile="cn_current",
            resolver="null",
            analyzer="char",
            user_pinyin_dict=dict(ALT_DICT),
        )
        flipped = replace(full, profile="cn_ncb")
        assert flipped.analyzer == "char"
        assert flipped.resolver == "null"
        assert dict(flipped.user_pinyin_dict) == ALT_DICT
        assert flipped.profile_name == "cn_ncb"

    def test_an_empty_path_list_is_snapshotted_too(self) -> None:
        """The normalisation used to be guarded on truthiness, so an EMPTY list
        stayed the caller's own mutable object — reachable from both sides,
        contradicting the declared ``tuple[str, ...]``, and carried into any
        ``dataclasses.replace`` derivative as if configured."""
        caller: list[str] = []
        pipe = Pipeline(profile="cn_current", extra_profile_paths=caller)

        assert isinstance(pipe.extra_profile_paths, tuple)
        caller.append("/added/afterwards")
        assert pipe.extra_profile_paths == ()

    def test_a_derived_pipeline_does_not_inherit_appended_paths(self) -> None:
        """The consequence that actually bites: a path appended to the caller's
        list after construction must not appear in a pipeline derived later."""
        from dataclasses import replace

        caller: list[str] = []
        pipe = Pipeline(profile="cn_current", extra_profile_paths=caller)
        caller.append("/added/afterwards")

        derived = replace(pipe, resolver="null")
        assert derived.extra_profile_paths == ()

    def test_late_bound_asset_resolver_still_allowed(self) -> None:
        """The one documented late-binding seam stays open (a front-end
        attaches a document's assets to an already-built pipeline)."""
        pipe = Pipeline(profile="cn_current", resolver="null")
        fp0 = pipe.fingerprint
        pipe.asset_resolver = lambda name: _ASSET_SVG
        assert pipe.fingerprint != fp0
        pipe.default_renderer = "brf"
        assert pipe.default_renderer == "brf"
