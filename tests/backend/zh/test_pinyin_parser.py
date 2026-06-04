import pytest

from brailix.backend.zh.pinyin_parser import ParsedPinyin, parse_pinyin


class TestBasicSplit:
    @pytest.mark.parametrize("syl,initial,final,tone", [
        ("ba1",  "b",  "a",   "1"),
        ("pao3", "p",  "ao",  "3"),
        ("mei2", "m",  "ei",  "2"),
        ("fan4", "f",  "an",  "4"),
        ("ding3","d",  "ing", "3"),
        ("tian1","t",  "ian", "1"),
        ("nong2","n",  "ong", "2"),
        ("lang2","l",  "ang", "2"),
        ("gao1", "g",  "ao",  "1"),
        ("kao3", "k",  "ao",  "3"),
        ("hen3", "h",  "en",  "3"),
        ("ren2", "r",  "en",  "2"),
        ("zai4", "z",  "ai",  "4"),
        ("cong2","c",  "ong", "2"),
        ("san1", "s",  "an",  "1"),
    ])
    def test_basic(self, syl, initial, final, tone):
        p = parse_pinyin(syl)
        assert p == ParsedPinyin(initial=initial, final=final, tone=tone)


class TestRetroflex:
    @pytest.mark.parametrize("syl,initial,final,tone", [
        # zhi/chi/shi/ri carry no real vowel — final is suppressed.
        ("zhi1",  "zh", "",    "1"),
        ("chong2","ch", "ong", "2"),
        ("shi4",  "sh", "",    "4"),
    ])
    def test_zh_ch_sh(self, syl, initial, final, tone):
        p = parse_pinyin(syl)
        assert p.initial == initial
        assert p.final == final
        assert p.tone == tone

    def test_zh_matched_before_z(self):
        # "zhe1" must not be parsed as z + he1.
        assert parse_pinyin("zhe1").initial == "zh"

    def test_sh_matched_before_s(self):
        assert parse_pinyin("shu1").initial == "sh"

    @pytest.mark.parametrize("syl,initial", [
        ("zi3", "z"), ("ci2", "c"), ("si4", "s"),
        ("zhi1", "zh"), ("chi1", "ch"), ("shi2", "sh"), ("ri4", "r"),
    ])
    def test_syllabic_i_drops_final(self, syl, initial):
        # zi/ci/si and zhi/chi/shi/ri have no vowel — the parser must
        # set the final to empty so the backend emits only initial+tone.
        p = parse_pinyin(syl)
        assert p.initial == initial
        assert p.final == ""


class TestZeroInitialW:
    @pytest.mark.parametrize("syl,final", [
        ("wu2",   "u"),
        ("wa1",   "ua"),
        ("wo3",   "uo"),
        ("wai4",  "uai"),
        ("wei3",  "uei"),
        ("wan1",  "uan"),
        ("wen2",  "uen"),
        ("wang2", "uang"),
        ("weng1", "ueng"),
    ])
    def test_w_normalizes(self, syl, final):
        p = parse_pinyin(syl)
        assert p.initial == ""
        assert p.final == final


class TestZeroInitialY:
    def test_bare_y_normalizes_to_i(self):
        # Defensive: a lone "y" (no following letter) is a degenerate
        # input but should still produce a recoverable result rather
        # than crash. The y→i fallback maps it to final "i".
        p = parse_pinyin("y")
        assert p.initial == ""
        assert p.final == "i"
        assert p.tone == ""

    @pytest.mark.parametrize("syl,final", [
        ("yi1",   "i"),
        ("ya1",   "ia"),
        ("ye4",   "ie"),
        ("yao3",  "iao"),
        ("you4",  "iou"),
        ("yan2",  "ian"),
        ("yin1",  "in"),
        ("yang2", "iang"),
        ("ying1", "ing"),
        ("yong3", "iong"),
        ("yu2",   "ü"),
        ("yue4",  "üe"),
        ("yuan2", "üan"),
        ("yun2",  "ün"),
    ])
    def test_y_normalizes(self, syl, final):
        p = parse_pinyin(syl)
        assert p.initial == ""
        assert p.final == final


class TestContractions:
    def test_iu_to_iou(self):
        # liu → l + iou
        assert parse_pinyin("liu2").final == "iou"

    def test_ui_to_uei(self):
        # gui → g + uei
        assert parse_pinyin("gui1").final == "uei"

    def test_un_to_uen(self):
        # dun → d + uen
        assert parse_pinyin("dun4").final == "uen"

    def test_contractions_only_with_initial(self):
        # "wen" already handled by w-normalization to "uen"; not by un→uen.
        # "you" → iou via y-normalization, not via iu→iou.
        assert parse_pinyin("wen2").final == "uen"

    @pytest.mark.parametrize(
        "syl,initial,final",
        [
            ("jiu3", "j", "iou"),
            ("qiu2", "q", "iou"),
            ("xiu1", "x", "iou"),
            ("niu2", "n", "iou"),
        ],
    )
    def test_jqx_n_iu_contracts_to_iou(self, syl, initial, final):
        # 求/旧/秀/牛 — the iu → iou expansion must fire for every
        # consonant initial, including jqx (jqx-rule only rewrites the
        # bare "u"-leading finals, not "iu").
        p = parse_pinyin(syl)
        assert p.initial == initial
        assert p.final == final


class TestJqxUmlaut:
    @pytest.mark.parametrize("syl,initial,final", [
        ("ju1",   "j", "ü"),
        ("qu4",   "q", "ü"),
        ("xue2",  "x", "üe"),
        ("juan1", "j", "üan"),
        ("xun4",  "x", "ün"),
    ])
    def test_jqx_u_means_umlaut(self, syl, initial, final):
        p = parse_pinyin(syl)
        assert p.initial == initial
        assert p.final == final


class TestUmlautInput:
    def test_v_treated_as_umlaut(self):
        # "lv4" is a common ASCII-only spelling of "lü4".
        p = parse_pinyin("lv4")
        assert p.initial == "l"
        assert p.final == "ü"

    def test_explicit_umlaut(self):
        p = parse_pinyin("lü4")
        assert p.initial == "l"
        assert p.final == "ü"


class TestTone:
    def test_no_tone(self):
        p = parse_pinyin("ma")
        assert p.tone == ""
        assert p.initial == "m"
        assert p.final == "a"

    def test_neutral_tone_5(self):
        p = parse_pinyin("ma5")
        assert p.tone == "5"

    @pytest.mark.parametrize("tone", ["1", "2", "3", "4", "5"])
    def test_all_tones(self, tone):
        assert parse_pinyin(f"ma{tone}").tone == tone


class TestErrors:
    def test_empty_raises(self):
        with pytest.raises(ValueError):
            parse_pinyin("")

    def test_only_tone_raises(self):
        with pytest.raises(ValueError):
            parse_pinyin("3")

    def test_invalid_tone_digit(self):
        # 6+ is not a valid Chinese tone
        with pytest.raises(ValueError):
            parse_pinyin("ma6")


class TestCaseAndWhitespace:
    def test_uppercase(self):
        assert parse_pinyin("BA1") == parse_pinyin("ba1")

    def test_strips_whitespace(self):
        assert parse_pinyin("  ba1  ") == parse_pinyin("ba1")


class TestErPlace:
    def test_er(self):
        # er is its own final with no initial.
        p = parse_pinyin("er2")
        assert p.initial == ""
        assert p.final == "er"
        assert p.tone == "2"
