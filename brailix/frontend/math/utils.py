"""Shared MathML string utilities for the math source adapters + normalizer.

``merror_wrap`` (a soft-failure ``<merror>`` document), ``_strip_math_delimiters``
(peel the ``$...$`` / ``\\(...\\)`` wrappers the segmenter leaves attached), and
the MathML namespace constant are used by every math adapter *and* the
normalizer. They live here — a neutral util — rather than inside the
pass-through ``mathml`` adapter, so the normalizer and the sibling adapters
don't reverse-import into one specific adapter module.
"""

from __future__ import annotations

from xml.sax.saxutils import escape, quoteattr

from brailix.frontend._xml import strip_xml_invalid_chars

# The MathML 3 namespace. Some emitters (latex2mathml) include it, others
# don't; both forms are accepted by the normalizer.
_MATHML_NS: str = "http://www.w3.org/1998/Math/MathML"


def _strip_math_delimiters(text: str) -> str:
    """Peel the inline-math delimiters the segmenter leaves attached.

    The segmenter captures the whole ``$...$`` / ``\\(...\\)`` / ``\\[...\\]``
    span as the math surface, so each adapter strips its own wrapping before
    parsing. Shared so the LaTeX and MathML adapters stay in sync.
    """
    if text.startswith("$") and text.endswith("$") and len(text) >= 2:
        return text[1:-1]
    if text.startswith("\\(") and text.endswith("\\)"):
        return text[2:-2]
    if text.startswith("\\[") and text.endswith("\\]"):
        return text[2:-2]
    return text


def merror_wrap(surface: str, *, reason: str) -> str:
    """Build a minimal MathML document carrying a single ``<merror>``.

    Shared by every adapter — and the normalizer's parse-error path — that
    needs to report a soft failure.
    """
    escaped = escape(strip_xml_invalid_chars(surface))
    escaped_reason = quoteattr(strip_xml_invalid_chars(reason))
    return (
        f'<math xmlns="{_MATHML_NS}">'
        f"<merror data-reason={escaped_reason}><mtext>{escaped}</mtext></merror>"
        f"</math>"
    )
