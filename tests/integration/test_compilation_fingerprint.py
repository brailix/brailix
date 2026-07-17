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
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from brailix.core.span import Span
from brailix.input import parse_markdown
from brailix.ir.document import Paragraph, Table
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
