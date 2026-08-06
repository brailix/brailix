"""One token-contract policy, three open registries — pinned in one place.

The Chinese analyzer, the Japanese analyzer and the pinyin resolver are all
selected by name from a registry a third party may register with. Each
declares a ``Protocol``, and a ``Protocol`` proves exactly one thing: the
object *has* the method. What comes back out of it is not typed, not checked
by the registry, and — until these checks existed — trusted completely.

That trust is not cheap here. Token spans are the source coordinates every
braille cell ends up inheriting, so a wrong one does not crash: it produces a
document that translates fine and whose proofreading jumps land on the wrong
characters. Worse, the spans are *read* as well as carried —
``tokens_to_inline`` places a word-boundary blank at each token's ``span.end``,
the Chinese cross-kind rules compare ``prev.span.end`` with ``cur.span.start``
to decide whether a space or a connector belongs between two runs, and the
Japanese 分かち書き pass decides whether two morphemes are one over-segmented
word the same way. Overlapping coordinates therefore change the braille, not
just its provenance.

So each subsystem entry point checks what it was handed, and the *policy* is
one policy: a structural impossibility (wrong type, span past the end of the
text, spans that overlap or run backwards, a resolver that re-segmented) is a
:class:`~brailix.core.errors.FrontendContractError` raised on the spot and
never downgraded, because it is a defect in the adapter's code rather than a
property of the user's document — the same line
:class:`~brailix.core.errors.BackendContractError` draws on the output side. A
surface that does not match the source text its span points at is a
``TOKEN_SPAN_MISMATCH`` *warning*, because an analyzer that normalises its
input produces one legitimately (the shipped THULAC and HanLP adapters do, and
say so).

One shared contract test, three separate implementations — the same shape
``test_soft_failure_policy.py`` uses, and for the same reason: zh and ja are
independently replaceable language components (ARCHITECTURE#arch-layers), so a
common validation helper would weld them together to save a few lines. A test
may span them; the production code may not.

Every case here goes through the real registry via ``Registry.overriding()``,
so what is exercised is the entry point's own boundary rather than any
particular built-in adapter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pytest

from brailix.core.context import FrontendContext
from brailix.core.errors import FrontendContractError
from brailix.core.span import Span
from brailix.frontend.ja.analyzer import JapaneseToken, analyze
from brailix.frontend.ja.analyzer.registry import analyzer_registry as ja_registry
from brailix.frontend.zh.analyzer import tokenize
from brailix.frontend.zh.analyzer.registry import analyzer_registry as zh_registry
from brailix.frontend.zh.pinyin import annotate
from brailix.frontend.zh.pinyin.registry import resolver_registry
from brailix.frontend.zh.tokens import ChineseToken

_PROBE = "contract_probe"

ZH_TEXT = "国家通用盲文"
JA_TEXT = "私はパンを買う"


@dataclass
class _FakeAnalyzer:
    """Returns whatever it was built with, for either language."""

    tokens: Any
    name: str = _PROBE

    def analyze(self, text: str, ctx: FrontendContext | None = None) -> Any:
        return self.tokens


@dataclass
class _FakeResolver:
    """Returns ``tokens_out``, or the input transformed by ``rewrite``."""

    tokens_out: Any = None
    rewrite: Any = None
    name: str = _PROBE

    def resolve(
        self, tokens: list[ChineseToken], ctx: FrontendContext | None = None
    ) -> Any:
        if self.rewrite is not None:
            return self.rewrite(tokens)
        return self.tokens_out


def _zh_ctx(**options: Any) -> FrontendContext:
    return FrontendContext("cn_current", options={"zh_analyzer": _PROBE, **options})


def _ja_ctx(**options: Any) -> FrontendContext:
    return FrontendContext("cn_current", options={"ja_analyzer": _PROBE, **options})


def _pinyin_ctx(**options: Any) -> FrontendContext:
    return FrontendContext(
        "cn_current", options={"pinyin_resolver": _PROBE, **options}
    )


def _zh_out(tokens: Any) -> Any:
    """Run ``tokenize`` with an adapter returning ``tokens``."""
    with zh_registry.overriding(_PROBE, lambda: _FakeAnalyzer(tokens)):
        ctx = _zh_ctx()
        return tokenize(ZH_TEXT, ctx), ctx


def _ja_out(tokens: Any) -> Any:
    with ja_registry.overriding(_PROBE, lambda: _FakeAnalyzer(tokens)):
        ctx = _ja_ctx()
        return analyze(JA_TEXT, ctx), ctx


class TestChineseAnalyzerBoundary:
    def test_a_non_list_result_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a list"):
            _zh_out(("国家", "通用"))

    def test_a_foreign_element_type_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a ChineseToken"):
            _zh_out([ChineseToken("国家", span=Span(0, 2)), "通用"])

    def test_a_non_span_span_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a Span"):
            _zh_out([ChineseToken("国家", span=(0, 2))])

    def test_a_non_str_surface_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="surface"):
            _zh_out([ChineseToken(123, span=Span(0, 2))])

    def test_a_non_str_pos_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="pos"):
            _zh_out([ChineseToken("国家", pos=7, span=Span(0, 2))])

    def test_a_span_past_the_end_of_the_text_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="past the end"):
            _zh_out([ChineseToken("国家通用盲文", span=Span(0, len(ZH_TEXT) + 1))])

    def test_overlapping_spans_are_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="ordered and non-overlapping"):
            _zh_out(
                [
                    ChineseToken("国家通", span=Span(0, 3)),
                    ChineseToken("家通用", span=Span(1, 4)),
                ]
            )

    def test_descending_spans_are_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="ordered and non-overlapping"):
            _zh_out(
                [
                    ChineseToken("盲文", span=Span(4, 6)),
                    ChineseToken("国家", span=Span(0, 2)),
                ]
            )

    def test_the_message_names_the_adapter_and_the_token(self) -> None:
        # A contract error is diagnosed by whoever ships the plugin, so it has
        # to say which plugin and which token — the failure surfaces in the
        # library, not in their code.
        with pytest.raises(FrontendContractError) as exc:
            _zh_out(
                [
                    ChineseToken("国家通", span=Span(0, 3)),
                    ChineseToken("家通用", span=Span(1, 4)),
                ]
            )
        assert _PROBE in str(exc.value)
        assert "家通用" in str(exc.value)

    def test_a_spanless_token_is_still_legal(self) -> None:
        # Documented: ``_local_spans`` synthesises coordinates from a running
        # cursor for adapters that omit them.
        tokens, _ = _zh_out([ChineseToken("国家"), ChineseToken("通用")])
        assert [t.surface for t in tokens] == ["国家", "通用"]

    def test_touching_spans_are_legal(self) -> None:
        tokens, _ = _zh_out(
            [ChineseToken("国家", span=Span(0, 2)), ChineseToken("通用", span=Span(2, 4))]
        )
        assert len(tokens) == 2

    def test_an_empty_result_is_legal(self) -> None:
        tokens, _ = _zh_out([])
        assert tokens == []

    def test_a_surface_that_is_not_at_its_span_only_warns(self) -> None:
        # The normalising-analyzer case: THULAC and HanLP both produce it and
        # say so, so the document still compiles and the proofreader is told
        # the coordinates for that word are unreliable.
        tokens, ctx = _zh_out([ChineseToken("國家", span=Span(0, 2))])
        assert [t.surface for t in tokens] == ["國家"]
        assert [w.code for w in ctx.warnings] == ["TOKEN_SPAN_MISMATCH"]

    def test_a_length_only_mismatch_warns_too(self) -> None:
        # The span is in range and ordered, but two characters short of the
        # surface it claims — the shape a hand-rolled ``text.find`` produces
        # when the tokenizer glued two words together.
        _, ctx = _zh_out([ChineseToken("国家通用", span=Span(0, 2))])
        assert [w.code for w in ctx.warnings] == ["TOKEN_SPAN_MISMATCH"]

    def test_an_accurate_span_warns_about_nothing(self) -> None:
        _, ctx = _zh_out([ChineseToken("国家", span=Span(0, 2))])
        assert list(ctx.warnings) == []


class TestJapaneseAnalyzerBoundary:
    def test_a_non_list_result_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a list"):
            _ja_out(iter([]))

    def test_a_foreign_element_type_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a JapaneseToken"):
            _ja_out([ChineseToken("私", span=Span(0, 1))])

    def test_a_non_span_span_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a Span"):
            _ja_out([JapaneseToken("私", reading="ワタシ", span=[0, 1])])

    def test_a_non_str_reading_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="reading"):
            _ja_out([JapaneseToken("私", reading=5, span=Span(0, 1))])

    def test_a_span_past_the_end_of_the_text_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="past the end"):
            _ja_out([JapaneseToken("私", reading="ワタシ", span=Span(0, 99))])

    def test_overlapping_spans_are_refused(self) -> None:
        # The case that changes the braille rather than only its provenance:
        # 分かち書き spacing is decided from ``prev.span.end ==
        # token.span.start``.
        with pytest.raises(FrontendContractError, match="ordered and non-overlapping"):
            _ja_out(
                [
                    JapaneseToken("私は", reading="ワタシワ", span=Span(0, 2)),
                    JapaneseToken("はパン", reading="ワパン", span=Span(1, 4)),
                ]
            )

    def test_a_spanless_token_is_still_legal(self) -> None:
        tokens, _ = _ja_out([JapaneseToken("私", reading="ワタシ")])
        assert [t.surface for t in tokens] == ["私"]

    def test_a_surface_that_is_not_at_its_span_only_warns(self) -> None:
        _, ctx = _ja_out([JapaneseToken("僕", reading="ボク", span=Span(0, 1))])
        assert [w.code for w in ctx.warnings] == ["TOKEN_SPAN_MISMATCH"]

    def test_an_accurate_span_warns_about_nothing(self) -> None:
        _, ctx = _ja_out([JapaneseToken("私", reading="ワタシ", span=Span(0, 1))])
        assert list(ctx.warnings) == []


class TestPinyinResolverBoundary:
    """A resolver fills in readings; it may not re-segment.

    ``PinyinResolver`` states that in as many words, and until now stated it
    in three docstrings and checked it nowhere: :func:`annotate` handed the
    adapter's return value straight back to the orchestrator.
    """

    @staticmethod
    def _given() -> list[ChineseToken]:
        # Fresh tokens per call, not a shared class attribute: one of the
        # cases below rewrites a surface **in place**, which is the whole
        # point of it, and a shared fixture would carry that into every test
        # that ran afterwards.
        return [
            ChineseToken("国家", span=Span(0, 2)),
            ChineseToken("通用", span=Span(2, 4)),
        ]

    def _resolve(self, **kwargs: Any) -> list[ChineseToken]:
        with resolver_registry.overriding(_PROBE, lambda: _FakeResolver(**kwargs)):
            return annotate(self._given(), _pinyin_ctx())

    def test_a_non_list_result_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="not a list"):
            self._resolve(tokens_out=None)

    def test_dropping_a_token_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="does not re-segment"):
            self._resolve(rewrite=lambda toks: toks[:1])

    def test_splitting_a_token_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="does not re-segment"):
            self._resolve(
                rewrite=lambda toks: [
                    ChineseToken(ch, span=Span(i, i + 1))
                    for i, ch in enumerate("国家通用")
                ]
            )

    def test_reordering_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="changed token 0"):
            self._resolve(rewrite=lambda toks: list(reversed(toks)))

    def test_rewriting_a_surface_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="changed token 0"):
            self._resolve(
                rewrite=lambda toks: [
                    ChineseToken("國家", span=toks[0].span),
                    toks[1],
                ]
            )

    def test_moving_a_span_is_refused(self) -> None:
        with pytest.raises(FrontendContractError, match="changed token 1"):
            self._resolve(
                rewrite=lambda toks: [
                    toks[0],
                    ChineseToken(toks[1].surface, span=Span(9, 11)),
                ]
            )

    def test_an_in_place_rewrite_is_refused_too(self) -> None:
        """The case an after-the-fact comparison cannot see.

        The ``null`` resolver returns the caller's own token objects, so
        comparing the returned list against ``tokens`` would compare each token
        with itself and pass whatever was done to it. The check snapshots the
        surfaces / spans / POS *before* the call for exactly this.
        """

        def _mutate(toks: list[ChineseToken]) -> list[ChineseToken]:
            toks[0].surface = "他家"
            return toks

        with pytest.raises(FrontendContractError, match="changed token 0"):
            self._resolve(rewrite=_mutate)

    def test_filling_in_readings_is_what_a_resolver_is_for(self) -> None:
        from dataclasses import replace

        out = self._resolve(
            rewrite=lambda toks: [
                replace(t, pinyin="x", confidence=0.5) for t in toks
            ]
        )
        assert [t.pinyin for t in out] == ["x", "x"]
        assert [t.surface for t in out] == ["国家", "通用"]

    def test_returning_the_caller_s_own_objects_is_legal(self) -> None:
        # What ``null`` does; identity is not what is being checked.
        out = self._resolve(rewrite=lambda toks: toks)
        assert [t.surface for t in out] == ["国家", "通用"]


class TestTheShippedAdaptersSatisfyTheirOwnContract:
    """The checks above install fakes, so on their own they would pass over a
    boundary no real adapter ever crosses. These run every *registered* engine
    that is installed here and assert it comes through its own gate clean —
    which is also what makes the policy a real constraint on the built-ins
    rather than a rule written for other people's code.
    """

    TEXTS = ["国家通用盲文方案", "很好，很好！", "abc123 混排English", ""]
    JA_TEXTS = ["私はパンを買う。", "これはペンです。", ""]

    @pytest.mark.parametrize("engine", sorted(zh_registry.names()))
    def test_every_installed_zh_analyzer(self, engine: str) -> None:
        if not zh_registry.available(engine):
            pytest.skip(f"{engine} not installed")
        for text in self.TEXTS:
            ctx = FrontendContext("cn_current", options={"zh_analyzer": engine})
            tokenize(text, ctx)  # raises FrontendContractError on a violation

    @pytest.mark.parametrize("engine", sorted(ja_registry.names()))
    def test_every_installed_ja_analyzer(self, engine: str) -> None:
        if not ja_registry.available(engine):
            pytest.skip(f"{engine} not installed")
        for text in self.JA_TEXTS:
            ctx = FrontendContext("cn_current", options={"ja_analyzer": engine})
            analyze(text, ctx)

    @pytest.mark.parametrize("engine", sorted(resolver_registry.names()))
    def test_every_installed_pinyin_resolver(self, engine: str) -> None:
        if not resolver_registry.available(engine):
            pytest.skip(f"{engine} not installed")
        ctx = FrontendContext("cn_current", options={"pinyin_resolver": engine})
        annotate(
            [
                ChineseToken("国家", span=Span(0, 2)),
                ChineseToken("通用", span=Span(2, 4)),
            ],
            ctx,
        )
