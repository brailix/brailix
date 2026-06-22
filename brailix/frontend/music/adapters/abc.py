"""ABC notation source adapter — converts ABC text to MusicXML.

Uses ``abc-xml-converter`` (a packaging of Wim Vree's classic
``abc2xml`` script). Pure Python, no external binaries. The adapter
is registered with ``extra="abc"``; missing dependency surfaces as
:class:`~brailix.core.errors.MissingExtraError` pointing at
``pip install brailix[abc]``.

ABC is a text format, so input is ``str`` — bytes get utf-8 decoded
first. Conversion errors fall through to a ``<music-error>`` MusicXML
placeholder per the music subsystem's soft-failure contract.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass

from brailix.core.context import MusicContext
from brailix.frontend.music.adapters.musicxml import music_error_wrap


@dataclass(slots=True)
class AbcSourceAdapter:
    """ABC text → MusicXML via ``abc-xml-converter``.

    Soft-failure: any conversion exception comes back as a
    ``<music-error>`` MusicXML doc.
    """

    source: str = "abc"

    def to_musicxml(
        self, src: str | bytes, ctx: MusicContext | None = None
    ) -> str:
        if isinstance(src, bytes):
            try:
                src = src.decode("utf-8")
            except UnicodeDecodeError:
                return music_error_wrap(repr(src), reason="non-utf8 bytes")
        text = src.strip()
        if not text:
            return music_error_wrap("", reason="empty abc payload")
        try:
            from abc_xml_converter import abc2xml  # noqa: WPS433 — lazy
        except ImportError:
            return music_error_wrap(
                text,
                reason=(
                    "abc-xml-converter not installed "
                    "(pip install brailix[abc])"
                ),
            )
        # Use the library-level ``getXmlScores(abc_text) -> list[str]``,
        # NOT the top-level ``convert_abc2xml``: that entry calls
        # ``optparse.parse_args()`` internally to fill options we don't
        # pass, which reads ``sys.argv`` — so under any real launch whose
        # argv carries flags (the ``brailix`` CLI, a test runner, an
        # embedding host application) it hits an unknown option and
        # ``SystemExit(2)``s.
        # ``getXmlScores`` takes the ABC text directly and is argv-free.
        # (The earlier ``abc2xml.convert`` we probed writes to a file and
        # returns None, and the original ``getattr(abc2xml, "convert")``
        # bound that internal 4-arg helper and TypeError'd on one arg, so
        # this adapter never actually converted anything.) getattr-probe
        # the name so a future rename degrades to a clear warning.
        get_scores = getattr(abc2xml, "getXmlScores", None)
        if get_scores is None:
            return music_error_wrap(
                text,
                reason="abc-xml-converter lacks the getXmlScores API",
            )
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                scores = get_scores(text)
        except Exception as e:  # noqa: BLE001 — third-party failures vary
            return music_error_wrap(text, reason=f"abc-xml-converter error: {e}")
        if not scores:
            return music_error_wrap(
                text, reason="abc-xml-converter produced no score"
            )
        # The music IR is one score per fragment, so only the first tune of a
        # multi-tune ABC file is translated. Warn (when a ctx is available) so
        # the dropped tunes 2..N aren't a silent content loss.
        if len(scores) > 1 and ctx is not None:
            ctx.warnings.warn(
                code="MUSIC_UNSUPPORTED_NOTATION",
                message=(
                    f"ABC file has {len(scores)} tunes; only the first is "
                    f"translated ({len(scores) - 1} dropped)"
                ),
                source="frontend.music.abc",
            )
        return scores[0]


def _load() -> AbcSourceAdapter:
    """Lazy-load the adapter. Imports the converter here so the
    registry's MissingExtraError fires at registration-touch time."""
    import abc_xml_converter  # noqa: F401, WPS433 — registration-time gate

    return AbcSourceAdapter()
