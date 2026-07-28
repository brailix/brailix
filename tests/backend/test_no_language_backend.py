"""The backend's ``NO_LANGUAGE_BACKEND`` escape hatch.

Reached when a profile's language has no registered ``LanguageBackend`` (a
third-party / future-language profile, e.g. ``fr-FR`` with no French backend).
The frontend twin ``NO_LANGUAGE_FRONTEND`` returns ``[]`` so a prose node never
reaches the backend, making this branch structurally unreachable through the
pipeline — so it is exercised directly here. See ARCHITECTURE#arch-language-slots.
"""

from __future__ import annotations

from brailix.backend.dispatch import translate_node
from brailix.backend.number import translate_date
from brailix.core.config import load_profile
from brailix.core.context import BackendContext
from brailix.core.errors import WarningCollector
from brailix.core.span import Span
from brailix.ir.inline import Date, HanziChar, HanziMarker, Number, Word


def _profile_without_fr_backend(monkeypatch):
    prof = load_profile("cn_current")
    # No "fr" LanguageBackend is registered, so the escape hatch fires.
    monkeypatch.setattr(prof, "language", "fr-FR")
    return prof


def _ctx(prof) -> BackendContext:
    return BackendContext(profile=prof.name, warnings=WarningCollector())


class TestNoLanguageBackend:
    def test_word_falls_back_with_one_warning(self, monkeypatch):
        prof = _profile_without_fr_backend(monkeypatch)
        ctx = _ctx(prof)
        cells = translate_node(
            Word(surface="bonjour", span=Span(0, 7)), ctx, prof
        )
        assert cells == []
        assert [w.code for w in ctx.warnings.warnings] == ["NO_LANGUAGE_BACKEND"]

    def test_hanzi_char_falls_back_with_one_warning(self, monkeypatch):
        prof = _profile_without_fr_backend(monkeypatch)
        ctx = _ctx(prof)
        cells = translate_node(
            HanziChar(surface="字", span=Span(0, 1)), ctx, prof
        )
        assert cells == []
        assert [w.code for w in ctx.warnings.warnings] == ["NO_LANGUAGE_BACKEND"]

    def test_date_marker_falls_back_with_warning(self, monkeypatch):
        # The Date's HanziMarker components route to the language backend's
        # marker translator; with none registered they degrade to an unknown
        # cell + NO_LANGUAGE_BACKEND, while the Number digits still translate.
        prof = _profile_without_fr_backend(monkeypatch)
        ctx = _ctx(prof)
        node = Date(
            surface="5月",
            span=Span(0, 2),
            parts=[
                Number(surface="5", role="month", span=Span(0, 1)),
                HanziMarker(surface="月", span=Span(1, 2)),
            ],
        )
        cells = translate_date(node, ctx, prof)
        assert "NO_LANGUAGE_BACKEND" in [w.code for w in ctx.warnings.warnings]
        # The digit still translates — only the marker degrades.
        assert any(c.role == "digit" for c in cells)
