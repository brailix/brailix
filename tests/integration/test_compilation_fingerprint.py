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
