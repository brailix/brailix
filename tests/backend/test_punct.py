import pytest

from brailix.backend.punct import (
    translate_code_inline,
    translate_connector,
    translate_punct,
    translate_space,
    translate_unknown,
)
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.span import Span
from brailix.ir.inline import (
    CodeInline,
    Connector,
    Punct,
    Space,
    Unknown,
)


@pytest.fixture(scope="module")
def profile():
    return load_profile("cn_current")


@pytest.fixture
def ctx():
    return BackendContext(profile="cn_current")


class TestPunct:
    def test_known_comma(self, ctx, profile):
        # Chinese comma ⠐ is single-cell with space_after=true →
        # comma cell + trailing blank.
        cells = translate_punct(Punct(surface="，", span=Span(0, 1)), ctx, profile)
        assert len(cells) == 2
        # punctuation table now stores cell *sequences* — index [0]
        # picks the single cell for ，
        assert cells[0].dots == profile.punctuation["，"][0]
        assert cells[0].role == "punct"
        assert cells[0].source_text == "，"
        assert cells[1].is_blank

    def test_known_period(self, ctx, profile):
        # Chinese 。 is *two cells* (⠐⠆) with no space on either side.
        cells = translate_punct(Punct(surface="。"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (5,)
        assert cells[1].dots == (2, 3)
        assert all(c.role == "punct" for c in cells)
        # No trailing blank.
        assert not any(c.is_blank for c in cells)

    def test_english_period_trailing_space(self, ctx, profile):
        # English ``.`` is single-cell ⠲ with space_after=true so the
        # next sentence is separated by one blank, mirroring the
        # English ``,`` / ``?`` / ``!`` spacing rule.
        cells = translate_punct(Punct(surface="."), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (2, 5, 6)
        assert cells[0].role == "punct"
        assert cells[1].is_blank

    def test_english_exclamation_trailing_space(self, ctx, profile):
        # English ``!`` is single-cell ⠖ with space_after=true — same
        # rule as ``.`` / ``?``. Distinct from Chinese ！ which is the
        # two-cell ⠰⠂ form with no surrounding space.
        cells = translate_punct(Punct(surface="!"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (2, 3, 5)
        assert cells[0].role == "punct"
        assert cells[1].is_blank

    def test_chinese_question_mark_is_two_cells_no_space(self, ctx, profile):
        # Regression: Chinese ？ must be
        # two cells ⠐⠄ with no trailing blank — the English ⠦ glyph
        # (c_236) is wrong here. Mirrors the 。 / ！ double-cell rule.
        cells = translate_punct(Punct(surface="？"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (5,)
        assert cells[1].dots == (3,)
        assert all(c.role == "punct" for c in cells)
        assert not any(c.is_blank for c in cells)

    def test_english_question_mark_unchanged(self, ctx, profile):
        # The fix to Chinese ？ must not touch ASCII ?: still ⠦
        # (c_236) with a trailing blank.
        cells = translate_punct(Punct(surface="?"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (2, 3, 6)
        assert cells[1].is_blank

    def test_english_comma_dots_and_trailing_space(self, ctx, profile):
        # English ``,`` is ⠂ (dot 2) with space_after=true —
        # distinct from Chinese ， which is dot 5.
        cells = translate_punct(Punct(surface=","), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (2,)
        assert cells[1].is_blank

    def test_chinese_semicolon_is_dots_56(self, ctx, profile):
        # Chinese ； is ⠰ (c_56) with a trailing blank. ⠆ (c_23) is the
        # ASCII semicolon (and the open-fraction sign) — a different mark,
        # not another spelling of this one. ⠰ is the same head ！ (⠰⠂)
        # carries.
        cells = translate_punct(Punct(surface="；"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (5, 6)
        assert cells[0].role == "punct"
        assert cells[1].is_blank

    def test_english_semicolon_stays_dots_23(self, ctx, profile):
        # The Chinese ； fix must not touch ASCII ``;``: still ⠆ (c_23)
        # with a trailing blank.
        cells = translate_punct(Punct(surface=";"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (2, 3)
        assert cells[1].is_blank

    def test_single_em_dash_is_english_dash_one_cell(self, ctx, profile):
        # A single 「—」 = English dash = single cell ⠤ (36), no space on
        # either side. The Chinese dash is two consecutive 「——」 (merged by
        # the normalizer); see the next test case.
        cells = translate_punct(Punct(surface="—", span=Span(0, 1)), ctx, profile)
        assert len(cells) == 1
        assert cells[0].dots == (3, 6)
        assert cells[0].role == "punct"
        assert not cells[0].is_blank

    def test_chinese_dash_pair_is_two_cells_no_space(self, ctx, profile):
        # The Chinese dash 「——」 (two consecutive em-dashes, merged by the
        # normalizer into surface="——") = ⠠⠤ (6 | 36), two cells, no space
        # on either side.
        cells = translate_punct(Punct(surface="——", span=Span(0, 2)), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (6,)
        assert cells[1].dots == (3, 6)
        assert all(c.role == "punct" for c in cells)
        assert not any(c.is_blank for c in cells)

    def test_underscore_uses_36_dots(self, ctx, profile):
        # ``_`` shares the same glyph as the em-dash: single cell ⠤
        # (dots 3-6), no space on either side.
        cells = translate_punct(Punct(surface="_", span=Span(0, 1)), ctx, profile)
        assert len(cells) == 1
        assert cells[0].dots == (3, 6)
        assert cells[0].role == "punct"
        assert not cells[0].is_blank

    def test_middle_dot_trailing_space(self, ctx, profile):
        # ``·`` carries space_after=true so it formats the same way
        # whether it appears in body text or as the synthesized marker
        # of an unordered list (see :func:`backend.block._list_marker_cells`).
        cells = translate_punct(Punct(surface="·", span=Span(0, 1)), ctx, profile)
        assert len(cells) == 2
        assert cells[0].role == "punct"
        assert cells[0].dots == (3,)
        assert cells[1].is_blank

    def test_unknown_punct_emits_warning(self, ctx, profile):
        cells = translate_punct(Punct(surface="§", span=Span(0, 1)), ctx, profile)
        warnings = list(ctx.warnings)
        assert any(w.code == "UNKNOWN_PUNCT" for w in warnings)
        assert cells[0].role == "unknown"
        assert cells[0].source_text == "§"

    def test_punct_with_space_before_inserts_leading_blank(self, ctx, profile):
        # Chinese left single quote ‘ carries space_before=true in
        # cn_current. The handler must insert a leading blank cell
        # before the quote's own cells.
        cells = translate_punct(
            Punct(surface="‘", span=Span(0, 1)), ctx, profile
        )
        assert cells[0].is_blank
        # The actual quote glyph follows the blank.
        assert any(c.role == "punct" for c in cells[1:])

    def test_chinese_round_brackets(self, ctx, profile):
        # Round brackets （）— left ⠰⠄ (56-3)
        # with space_before, right ⠠⠆ (6-23) with space_after. Each side is
        # two cells; the brackets are not interchangeable.
        cells_l = translate_punct(Punct(surface="（"), ctx, profile)
        assert len(cells_l) == 3
        assert cells_l[0].is_blank
        assert cells_l[1].dots == (5, 6)
        assert cells_l[2].dots == (3,)
        cells_r = translate_punct(Punct(surface="）"), ctx, profile)
        assert len(cells_r) == 3
        assert cells_r[0].dots == (6,)
        assert cells_r[1].dots == (2, 3)
        assert cells_r[2].is_blank

    def test_chinese_square_brackets(self, ctx, profile):
        # Square brackets 【】— both sides are ⠰⠆ (56-23),
        # the left/right asymmetry comes purely from spacing flags.
        cells_l = translate_punct(Punct(surface="【"), ctx, profile)
        assert len(cells_l) == 3
        assert cells_l[0].is_blank
        assert cells_l[1].dots == (5, 6)
        assert cells_l[2].dots == (2, 3)
        cells_r = translate_punct(Punct(surface="】"), ctx, profile)
        assert len(cells_r) == 3
        assert cells_r[0].dots == (5, 6)
        assert cells_r[1].dots == (2, 3)
        assert cells_r[2].is_blank

    def test_double_book_title_marks(self, ctx, profile):
        # Double book title marks 《》— left ⠐⠤ (5-36)
        # space_before, right ⠤⠂ (36-2) space_after.
        cells_l = translate_punct(Punct(surface="《"), ctx, profile)
        assert len(cells_l) == 3
        assert cells_l[0].is_blank
        assert cells_l[1].dots == (5,)
        assert cells_l[2].dots == (3, 6)
        cells_r = translate_punct(Punct(surface="》"), ctx, profile)
        assert len(cells_r) == 3
        assert cells_r[0].dots == (3, 6)
        assert cells_r[1].dots == (2,)
        assert cells_r[2].is_blank

    def test_single_book_title_marks(self, ctx, profile):
        # Single book title marks 〈〉— left ⠐⠄ (5-3)
        # space_before, right ⠠⠂ (6-2) space_after. The left cells (5-3)
        # collide glyphwise with ？; readers disambiguate by context.
        cells_l = translate_punct(Punct(surface="〈"), ctx, profile)
        assert len(cells_l) == 3
        assert cells_l[0].is_blank
        assert cells_l[1].dots == (5,)
        assert cells_l[2].dots == (3,)
        cells_r = translate_punct(Punct(surface="〉"), ctx, profile)
        assert len(cells_r) == 3
        assert cells_r[0].dots == (6,)
        assert cells_r[1].dots == (2,)
        assert cells_r[2].is_blank

    def test_annotation_asterisk(self, ctx, profile):
        # Annotation mark * = ⠶⠔ (2356-35), no spacing on
        # either side — attaches tightly to the annotated word.
        cells = translate_punct(Punct(surface="*"), ctx, profile)
        assert len(cells) == 2
        assert cells[0].dots == (2, 3, 5, 6)
        assert cells[1].dots == (3, 5)
        assert all(c.role == "punct" for c in cells)
        assert not any(c.is_blank for c in cells)


class TestSpace:
    def test_single_space(self, ctx, profile):
        cells = translate_space(Space(surface=" ", span=Span(0, 1)), ctx, profile)
        assert len(cells) == 1
        assert cells[0].is_blank
        assert cells[0].role == "space"

    def test_multiple_spaces(self, ctx, profile):
        cells = translate_space(Space(surface="   ", span=Span(0, 3)), ctx, profile)
        assert len(cells) == 3
        assert all(c.is_blank for c in cells)

    def test_empty_surface_synthetic_separator(self, ctx, profile):
        # A Space with empty surface is the synthetic separator the
        # frontend inserts between Chinese words; we still emit one
        # blank cell so the word boundary survives into braille.
        cells = translate_space(Space(surface="", span=Span(5, 5)), ctx, profile)
        assert len(cells) == 1
        assert cells[0].is_blank
        assert cells[0].source_text == ""


class TestConnector:
    def test_emits_profile_connector_cell(self, ctx, profile):
        # Connector → the profile's connector cell ⠤ (dots 3-6), role
        # "connector", empty source_text (a synthetic joiner, no surface
        # char behind it) — the within-compound-word counterpart of the
        # word-boundary Space.
        cells = translate_connector(
            Connector(surface="", span=Span(1, 1)), ctx, profile
        )
        assert len(cells) == 1
        assert cells[0].dots == (3, 6)
        assert cells[0].dots == profile.connector
        assert cells[0].role == "connector"
        assert cells[0].source_text == ""
        assert not cells[0].is_blank

    def test_preserves_boundary_span(self, ctx, profile):
        cells = translate_connector(
            Connector(surface="", span=Span(4, 4)), ctx, profile
        )
        assert cells[0].source_span == Span(4, 4)


class TestUnknown:
    def test_emits_warning(self, ctx, profile):
        cells = translate_unknown(Unknown(surface="??", span=Span(0, 2)), ctx, profile)
        warnings = list(ctx.warnings)
        assert any(w.code == "UNKNOWN_NODE" for w in warnings)
        assert cells[0].role == "unknown"


class TestProvenance:
    def test_source_spans_preserved(self, ctx, profile):
        cells = translate_punct(
            Punct(surface="，", span=Span(5, 6)), ctx, profile
        )
        assert cells[0].source_span == Span(5, 6)


class TestCodeInline:
    def test_chars_translated(self, ctx, profile):
        cells = translate_code_inline(
            CodeInline(surface=":;", span=Span(0, 2)), ctx, profile
        )
        # Both ":" and ";" are in the punctuation table.
        assert len(cells) == 2
        assert all(c.role == "punct" for c in cells)

    def test_empty_surface_yields_no_cells(self, ctx, profile):
        # Hits the early `not text` fast path in _char_by_char.
        assert translate_code_inline(CodeInline(surface=""), ctx, profile) == []


class TestFullwidthLatinFold:
    """Prose renders full-width Latin letters through the letter table via
    their half-width twins (F7: ``ＡＢＣ`` used to be three blank unknown
    cells in every shipped profile). The fold mirrors the digit run's
    ``fold_nonascii`` prose policy — routine CJK typography folds, math
    warns — and keeps the letter's case sign, because ``profile.letter``
    composes prefix + bare cell."""

    def test_fullwidth_upper_folds_to_letter_with_case_sign(self, ctx, profile):
        cells = translate_punct(Punct(surface="Ａ", span=Span(0, 1)), ctx, profile)
        assert [c.dots for c in cells] == list(profile.letter("A"))
        assert all(c.role == "latin_letter" for c in cells)
        # Traceability maps the cells to the character the source actually
        # carries — the full-width one, not the folded twin nobody typed.
        assert all(c.source_text == "Ａ" for c in cells)
        assert all(c.source_span == Span(0, 1) for c in cells)

    def test_fullwidth_lower_folds_to_letter_with_case_sign(self, ctx, profile):
        cells = translate_punct(Punct(surface="ａ", span=Span(0, 1)), ctx, profile)
        assert [c.dots for c in cells] == list(profile.letter("a"))

    def test_case_survives_the_fold(self, ctx, profile):
        # The whole point of routing through profile.letter: Ａ and ａ must
        # not collapse the way bare cells alone would.
        upper = translate_punct(Punct(surface="Ａ"), ctx, profile)
        lower = translate_punct(Punct(surface="ａ"), ctx, profile)
        assert [c.dots for c in upper] != [c.dots for c in lower]

    def test_fold_is_silent_when_the_profile_has_the_sign(self, ctx, profile):
        # cn_current defines letter_prefix.*: the fold needs no diagnostic.
        translate_punct(Punct(surface="Ａ"), ctx, profile)
        assert [w.code for w in ctx.warnings] == []

    def test_fold_warns_when_profile_lacks_the_sign(self):
        # ja_current ships the letter table but no letter_prefix.* (the
        # registered romaji debt): the folded letter renders bare — case
        # lost — and that rides the Latin path's own warning.
        from brailix.core.config import load_profile

        ja = load_profile("ja_current")
        ja_ctx = BackendContext(profile="ja_current")
        cells = translate_punct(Punct(surface="Ａ"), ja_ctx, ja)
        assert [c.dots for c in cells] == [ja.bare_letter("A")]
        assert "LATIN_CASE_SIGN_MISSING" in [w.code for w in ja_ctx.warnings]

    def test_fullwidth_non_letter_is_not_folded(self, ctx, profile):
        # The fold is scoped to LETTERS (the latin table's domain). A
        # full-width ＆ has no letter twin and keeps its ordinary
        # unknown-punct diagnostic with the half-width hint.
        cells = translate_punct(Punct(surface="＆", span=Span(0, 1)), ctx, profile)
        assert [w.code for w in ctx.warnings] == ["UNKNOWN_PUNCT"]
        assert cells[0].role == "unknown"

    @pytest.mark.parametrize(
        ("profile_name", "kwargs"),
        [
            ("cn_current", {"analyzer": "char", "resolver": "null"}),
            ("ja_current", {"analyzer": "kana"}),
        ],
    )
    def test_prose_fullwidth_latin_renders_nonblank(self, profile_name, kwargs):
        # End-to-end (frontend segmentation + backend): ＡＢＣａｂｃ in
        # running text must produce letter cells, never blank placeholders.
        from brailix import Pipeline

        result = Pipeline(profile=profile_name, **kwargs).translate_text(
            "ＡＢＣａｂｃ"
        )
        cells = [c for b in result.braille_ir.blocks for c in b.cells]
        assert cells, f"{profile_name}: full-width latin probe produced no cells"
        placeholders = [c for c in cells if not c.dots and c.role != "space"]
        assert not placeholders, (
            f"{profile_name}: full-width latin rendered blank placeholder cells"
        )
        assert "UNKNOWN_PUNCT" not in [w.code for w in result.warnings]
        # Five source letters → at least five letter cells (nothing vanished).
        assert sum(1 for c in cells if c.role == "latin_letter") >= 5
