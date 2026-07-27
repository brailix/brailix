"""brailix: a pluggable braille compiler.

Turn a document into braille, and keep every step of it inspectable.
:class:`Pipeline` is the entry point: give it a braille standard (a
*profile*) and hand it text, a parsed document, or a file.

    from brailix import Pipeline

    pipe = Pipeline(profile="cn_current")
    result = pipe.translate_text("我在重庆。")
    print(result.render())        # Unicode braille
    result.render("brf")          # NABCC bytes for an embosser

What you get back is not a string but a :class:`TranslationResult`:
rendering is deferred, so one compile can be written out as Unicode
braille, BRF, a cell array, or a laid-out page, and you pay only for the
formats you ask for. Along the way it carries the parsed document IR, the
braille IR, and the diagnostics — every braille cell knows the source
characters it came from, which is what makes proofreading tools, click-to-
source navigation, and incremental recompilation possible.

Beyond plain prose, the same pipeline handles mathematics (LaTeX, MathML,
Word's OMML and MathType), music scores (MusicXML, ``.mxl``, MIDI, ABC),
chemistry, tactile graphics, and Word / Markdown documents — each behind an
optional extra, so a bare install stays small. Every pluggable part —
tokenizer, pinyin engine, math and music source formats, renderers, whole
languages — is selected by name through a registry, and adding one is
registration rather than a change to this package. ``ARCHITECTURE.md``
explains the design; the *Extending brailix* guide is the how-to.

The public surface
------------------

The supported API is this package plus :mod:`brailix.ir`,
:mod:`brailix.core`, :mod:`brailix.core.models`, :mod:`brailix.renderer`,
:mod:`brailix.input` and :mod:`brailix.frontend` — the modules documented
in this reference. Import from those facades rather than from the concrete
modules behind them (``from brailix.core import Span``, not ``from
brailix.core.span import Span``), so the library can reorganise its
internals without breaking you.

Everything else is internal: reachable, unsupported, and free to move
between releases. That deliberately includes :mod:`brailix.pipeline`, which
is where :class:`Pipeline` is implemented — the names worth depending on
are re-exported here.

Each facade's ``__all__`` **is** the promise, and it is pinned by an exact
manifest in the test suite: a name cannot go missing without failing a
test, and cannot quietly become public without a deliberate edit. That is
also what this reference is generated from, so what you read here and what
the library supports cannot drift apart.
"""

__version__ = "0.1.0"

from brailix.input import (  # noqa: E402
    DEFAULT_INPUT_LIMITS,
    InputLimits,
    InputTooLargeError,
)
from brailix.pipeline import (  # noqa: E402
    CompiledBlock,
    GraphicResult,
    Pipeline,
    TactilePageResult,
    TranslationResult,
    TreeSubcache,
    block_hash,
    translate_graphic,
)

# Every result type a public entry point can hand back is nameable from
# here: ``translate_graphic`` returns a GraphicResult and
# ``Pipeline.translate_document_to_pages`` a TactilePageResult, so a caller
# annotating those had to reach into ``brailix.pipeline`` for the type while
# the function itself was top-level. The manifest in the public-API test
# pins this list, and the generated reference documents it.
__all__ = [
    "Pipeline",
    "translate_graphic",
    "TranslationResult",
    "GraphicResult",
    "TactilePageResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
    "InputLimits",
    "InputTooLargeError",
    "DEFAULT_INPUT_LIMITS",
]
