"""Plain-text input adapter: wrap a string as a single-paragraph
:class:`DocumentIR`.

Useful when callers have already pre-processed their source and just
need the standard DocumentIR shell that the Pipeline expects.
Whitespace is preserved verbatim — the frontend's segmenter handles
trimming as part of its normal categorisation.
"""

from __future__ import annotations

from brailix.core.defaults import DEFAULT_LANGUAGE, DEFAULT_PROFILE
from brailix.core.span import Span
from brailix.ir.document import DocumentIR, Paragraph


def parse_plain(
    text: str,
    *,
    language: str = DEFAULT_LANGUAGE,
    profile: str = DEFAULT_PROFILE,
) -> DocumentIR:
    """Wrap ``text`` as a one-block :class:`DocumentIR`.

    ``language`` and ``profile`` are stuffed into ``metadata`` so
    downstream renderers / proofread tools can see what the document
    was parsed for. They don't gate translation — that's
    :class:`Pipeline`'s job.
    """
    paragraph = Paragraph(text=text, span=Span(0, len(text)) if text else None)
    return DocumentIR(
        metadata={"language": language, "profile": profile},
        blocks=[paragraph],
    )
