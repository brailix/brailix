"""brailix: a pluggable braille compiler.

Turn a document into braille — or a drawing into an embossable tactile
page — and keep every step of it inspectable. :class:`Pipeline` is the
entry point for text: give it a braille standard (a *profile*) and hand it
text, a parsed document, or a file. :func:`translate_graphic` is the entry
point for a figure, and takes no braille standard at all: its product is a
raised-dot raster rather than cells.

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

Beyond plain prose, the same pipeline handles mathematics, music scores,
chemistry, tactile graphics, and whole documents. Much of that is in the bare
install, which is stdlib-only: Markdown, MathML, ``\\ce{...}`` chemistry,
MusicXML and ``.mxl`` scores, and tactile figures from SVG or geometry
primitives. What needs an optional extra is a source format whose parser is a
third-party package: LaTeX (``brailix[latex]``), Word ``.docx`` and the OMML
or MathType formulae inside it (``brailix[docx]``), MIDI (``brailix[midi]``)
and ABC (``brailix[abc]``) scores, a raster image or a complex external SVG as
a tactile figure (``brailix[graphics]``, ``brailix[graphics-svg-raster]``).
The heavier Chinese and Japanese engines — segmentation, pinyin, kanji
readings — are extras of their own; without them the ``auto`` chains fall back
to the dependency-free ``char`` and ``kana`` analyzers rather than dropping the
language. Every pluggable part — tokenizer, pinyin engine, math and music
source formats, renderers, whole languages — is selected by name through a
registry, and adding one is registration rather than a change to this package.
``ARCHITECTURE.md`` explains the design; the *Extending brailix* guide is the
how-to.

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
between releases. That deliberately includes :mod:`brailix.pipeline`, the
package :class:`Pipeline` is assembled in — the names worth depending on are
re-exported here, and here is where to import them from.

One qualification, because that package's own docstring could be read as
promising more than this one does: what ``brailix.pipeline`` resolves to *is*
pinned by the test suite, exactly and in order. That is not a second supported
address; it is a guard on the namespace a third party can reach, which keeps
private helpers from accumulating in it and the list from growing by
accident. The compatibility promise is this package's ``__all__`` and the
facades named above — one list, one place.

Each facade's ``__all__`` **is** the promise, and it is pinned by an exact
manifest in the test suite: a name cannot go missing without failing a
test, and cannot quietly become public without a deliberate edit — the
manifest is checked for exact equality, and a facade's namespace is checked
too, so a brailix name cannot merely *resolve* there without being
published. Names from *outside* brailix are held to that everywhere, not
only at the facades: no module in the package binds one under its plain
spelling, so ``from brailix.<anything> import Path`` does not quietly work
at an address that was never ours to promise. That manifest is also what
this reference is generated from, so what you read here and what the
library supports cannot drift apart.

Importing this package costs nothing but this module. Every name above is
resolved **lazily**, on first attribute access (PEP 562): ``import brailix``
loads no layer at all, and ``from brailix import Pipeline`` is what pulls in
the orchestrator and everything under it. That is not a startup micro-
optimisation — it is what keeps the layering real at runtime. Python runs a
package's ``__init__`` before any of its submodules, so an eager
``import brailix.pipeline`` here would mean that ``import brailix.ir`` — the
neutral mediator layer that promises to load carrying core primitives alone —
executes *this* file first, and with it the frontend, the backend, the
renderers and the input layer. The dependency matrix would be one-directional
in the source and reconnected into the whole compiler at import time, with the
AST guard none the wiser: it walks the layer directories, and the edge is one
a facade adds. ``tests/test_core_layering.py`` also asserts the real
``sys.modules`` set from a fresh interpreter, which is the only place that
question has an honest answer.

The extension surface
---------------------

Writing an adapter is a second kind of use, with a second, narrower surface —
and it is supported too. It comprises exactly:

* :mod:`brailix.core.protocols`, the structural interface your implementation
  satisfies (``MathSourceAdapter``, ``Renderer``, ``LanguageBackend``, ...);
* ``brailix.core.config.BrailleProfile`` — the one core type those interfaces
  name that no facade above carries. Every ``LanguageBackend`` method takes a
  profile, so an implementer has to be able to write the annotation; the
  profile loader keeps its own surface (like :mod:`brailix.core.models`)
  rather than being re-exported into :mod:`brailix.core`, which would put the
  whole table loader behind every ``import brailix.core``;
* the **registry** you register a loader with, which lives at its own
  subsystem's path because each belongs to one pluggable family:
  ``brailix.frontend.math.registry.math_source_registry``,
  ``brailix.frontend.zh.analyzer.registry.analyzer_registry``, and so on. The
  two keyed on the language rather than on a source format
  (``language_frontend_registry``, ``boundary_registry``) sit on the
  :mod:`brailix.frontend` facade, and ``renderer_registry`` on
  :mod:`brailix.renderer`;
* one **shared helper**, because one contract cannot be implemented from
  scratch responsibly: a ``LanguageFrontend`` has to cut its language's prose
  out of raw text, and the language-neutral half of that (``$...$`` math
  islands, IPA regions, digit runs, Latin, Greek, punctuation) is not a thing
  to re-derive per language. ``brailix.frontend.segmentation`` publishes
  ``segment_text`` — the chunker, parameterised by a character classifier —
  and ``char_category``, the built-in classifier a language's own composes
  with.

Those paths are deeper than the facades above — deliberately, since a registry
belongs with its subsystem — but they carry the same promise, kept by their own
exact manifest in the test suite, separate from the end-user one. The two are
not the same audience and should not move together. The *Extending brailix*
guide is the how-to; everything else under those subsystems (the concrete
adapters, the normalization pass, the dispatch tables) remains internal.
"""

from __future__ import annotations

# Bound under private names on purpose: this is the namespace the whole
# library is read through, and every plain binding here tab-completes beside
# ``Pipeline`` as if it were part of the surface. ``TYPE_CHECKING`` was the
# one that got away — ``from brailix import TYPE_CHECKING`` resolved, and
# ``__dir__`` below lists this module's globals, so it was offered by
# completion right next to the published names.
from importlib import import_module as _import_module
from typing import TYPE_CHECKING as _TYPE_CHECKING

__version__ = "0.1.0"

if _TYPE_CHECKING:
    # For type checkers and IDEs only. At runtime these names arrive through
    # ``__getattr__`` below, from the very same modules — the table under
    # ``_EXPORTS`` is what makes the two spellings one fact.
    from typing import Any

    from brailix.backend.tactile.profile import (
        TactileProfile,
        list_tactile_profiles,
        load_tactile_profile,
    )
    from brailix.input import (
        DEFAULT_INPUT_LIMITS,
        InputLimits,
        InputTooLargeError,
    )
    from brailix.pipeline import (
        CompiledBlock,
        GraphicResult,
        Pipeline,
        TactilePageResult,
        TranslationResult,
        TreeSubcache,
        block_hash,
        translate_graphic,
    )

# Every type a public entry point hands back — or takes — is nameable from
# here. ``translate_graphic`` returns a GraphicResult and
# ``Pipeline.translate_document_to_pages`` a TactilePageResult, so a caller
# annotating those had to reach into ``brailix.pipeline`` for the type while
# the function itself was top-level. The same held on the way in:
# ``translate_graphic`` documents an already-loaded ``TactileProfile`` as a
# legal argument, and the only path to that type — and to the loader that
# builds one — ran through ``brailix.backend.tactile.profile``, which the
# policy above calls internal and free to move, so the offer could not be
# taken from inside the supported surface. The graphics entry point lives
# here; its configuration belongs beside it. The manifest in the public-API
# test pins this list, and the generated reference documents it.
__all__ = [
    "Pipeline",
    "translate_graphic",
    "TranslationResult",
    "GraphicResult",
    "TactilePageResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
    "TactileProfile",
    "load_tactile_profile",
    "list_tactile_profiles",
    "InputLimits",
    "InputTooLargeError",
    "DEFAULT_INPUT_LIMITS",
]

# Where each published name really lives. The one table both halves above read
# from: the ``TYPE_CHECKING`` block spells it for a checker, ``__getattr__``
# resolves it at runtime, and ``tests/test_public_api.py`` checks it covers
# ``__all__`` exactly — so a name cannot be published here and left
# unresolvable, or resolvable and unpublished.
_EXPORTS: dict[str, str] = {
    "Pipeline": "brailix.pipeline",
    "translate_graphic": "brailix.pipeline",
    "TranslationResult": "brailix.pipeline",
    "GraphicResult": "brailix.pipeline",
    "TactilePageResult": "brailix.pipeline",
    "CompiledBlock": "brailix.pipeline",
    "TreeSubcache": "brailix.pipeline",
    "block_hash": "brailix.pipeline",
    "TactileProfile": "brailix.backend.tactile.profile",
    "load_tactile_profile": "brailix.backend.tactile.profile",
    "list_tactile_profiles": "brailix.backend.tactile.profile",
    "InputLimits": "brailix.input",
    "InputTooLargeError": "brailix.input",
    "DEFAULT_INPUT_LIMITS": "brailix.input",
}


def __getattr__(name: str) -> Any:
    """Resolve a published name on first access (PEP 562).

    This is what makes the facade free to import: the module it names is
    imported here, at the moment somebody asks for the name, instead of when
    this file runs. See "The public surface" above for why that matters — a
    package's ``__init__`` runs ahead of every submodule, so an eager import
    here put the whole compiler behind ``import brailix.ir``.

    The resolved object is written into this module's globals, so the lookup
    happens once per name: ``__getattr__`` is consulted only for attributes
    the module does not already have.
    """
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(_import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """``dir(brailix)`` lists the published names whether or not they have
    been touched yet — otherwise a fresh interpreter's tab-completion would
    show a shorter surface than a warm one's."""
    return sorted(set(globals()) | set(_EXPORTS))
