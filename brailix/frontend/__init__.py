"""Frontend layer: text → structured IR.

The frontend never emits braille. Its job is to identify *what* each
region of input is (hanzi run, number, date, latin word, math
fragment, ...) and produce a typed
:class:`~brailix.ir.inline` representation. The Backend then
decides how to write each type as braille.

The math / music source adapters here double as the shared conversion
service the **input** layer defers to: a text-dialect fragment Word
stored as OMML / EQ arrives as a deferred source-tagged island
(:mod:`brailix.core.inline_math`) and is converted by
:func:`~brailix.frontend.math.parse_math_tree`, exactly as a user-typed
``$...$`` fragment is. The boundary rule (text defers, binary is decoded
at input) is stated in ARCHITECTURE#arch-layers; the dependency is one-way (input
imports this; this never imports input).

## One entry point per subsystem

Each subsystem under ``frontend/`` exposes **a single high-level
entry point**. Where the subsystem has competing implementations it also
keeps a registry of them, and the entry point picks one from
``ctx.options[...]`` (or from an ``"auto"`` default that probes what's
installed). Where it does not, the entry point *is* the implementation: a
fixed compiler pass with nothing to select.

The table lists each **subsystem's own** entry point, at the module that
defines it — not the contents of this facade.

These are **orchestration entry points, not published API**. What this facade
re-exports (its ``__all__``, below) is the supported surface; a subsystem
entry point deeper than that is internal, exactly as the top-level
:mod:`brailix` docstring says of every path outside the facades and the
extension surface — reachable, unsupported, free to move between releases.
The compiler is who calls them: :class:`~brailix.Pipeline` drives the
document ones, and the music / graphics / Japanese verticals are driven from
their own orchestration. They are listed here so the shape of the layer is
legible, not to invite a third party to import them; widening ``__all__`` to
match the table would publish four more compatibility promises for the sake
of symmetry.

=========================  ===========================================
Module                     Its subsystem entry point
-------------------------  -------------------------------------------
``frontend.segmentation``  :func:`segment` (the language-neutral chunking)
``frontend.normalization`` :func:`normalize` (a fixed pass, nothing to select)
``frontend.zh``            :func:`tokenize` (selected by ``zh_analyzer``)
``frontend.zh.pinyin``     :func:`annotate` (selected by ``pinyin_resolver``)
``frontend.ja``            :func:`analyze` (selected by ``ja_analyzer``)
``frontend.math``          :func:`parse_math_tree` (source via :class:`MathContext`)
``frontend.music``         :func:`parse_music_tree` (source via :class:`MusicContext`)
``frontend.graphics``      :func:`parse_graphic_tree` (source via :class:`GraphicsContext`)
=========================  ===========================================

The first two are the pair with no adapter family: which characters group into
a region is the active language's own policy and reaches the compiler through
:meth:`LanguageFrontend.segment
<brailix.core.protocols.LanguageFrontend.segment>` (``frontend.segmentation``
holds the built-in classification every language builds on, and runs as-is
when the language has no frontend), while normalization is the canonical
Segment → inline-IR lowering. Both used to be plugin families with a
registry, an ``auto`` adapter and a ``ctx.options`` key of their own —
language-keyed seams parallel to ``language_frontend_registry``, which is
where the difference they expressed already lived.

Those two modules are named for the *process* rather than the verb because
the verb is taken: this facade binds :func:`segment` and :func:`normalize` as
functions, and a package attribute and a submodule cannot both own one name.
Naming them ``frontend.segment`` / ``frontend.normalize`` instead puts a
submodule under a name this facade binds as a function, and the function wins:
``import brailix.frontend.segment as m`` hands back the function, so
``m.segment_text`` — the path the extension guide names — raises
``AttributeError``, while ``from brailix.frontend.segment import segment_text``
works, because that form resolves through ``sys.modules`` rather than through
the package. One documented path, two spellings, one of them broken.

Published *here*, as this facade's ``__all__`` — the names that do carry a
compatibility promise: :func:`segment`, :func:`normalize`, ``tokenize_zh``,
``annotate_pinyin``, :func:`parse_math_tree`, plus the two language-keyed
registries below.

Custom adapters register themselves with the corresponding registry
(``analyzer_registry`` in :mod:`frontend.zh.analyzer.registry`,
``resolver_registry`` in :mod:`frontend.zh.pinyin.registry`, etc.) and
then become available by name. End users never touch the registries
directly — they set the name via ``ctx.options`` (or the equivalent
:class:`~brailix.Pipeline` constructor argument) and call the
public function; *extenders* do, and those registry paths are part of the
supported extension surface (see the top-level :mod:`brailix` docstring).

Two of them live here rather than at a subsystem path, because what they key
on is the language rather than one subsystem's source format:
:data:`language_frontend_registry` (which frontend handles a language's prose)
and :data:`boundary_registry` (the post-frontend pass that inserts cross-kind
or word-boundary separators on the assembled stream).
"""

from __future__ import annotations

from typing import TYPE_CHECKING as _TYPE_CHECKING

from brailix.core.errors import MissingExtraError as _MissingExtraError
from brailix.core.protocols import LanguageFrontend as _LanguageFrontend
from brailix.core.registry import Registry as _Registry
from brailix.frontend.ja import analyze as _ja_analyze
from brailix.frontend.ja import ja_boundary as _ja_boundary
from brailix.frontend.ja import ja_segment as _ja_segment
from brailix.frontend.ja import tokens_to_inline as _ja_tokens_to_inline
from brailix.frontend.ja.analyzer import list_analyzers as _ja_list_analyzers
from brailix.frontend.math import parse_math_tree
from brailix.frontend.normalization import normalize
from brailix.frontend.segmentation import segment
from brailix.frontend.zh import (
    insert_cross_kind_boundary_spaces as _zh_boundary_spaces,
)
from brailix.frontend.zh import (
    shift_token_spans as _shift_zh_spans,
)
from brailix.frontend.zh import (
    tokenize as tokenize_zh,
)
from brailix.frontend.zh import (
    tokens_to_inline as _zh_to_inline,
)
from brailix.frontend.zh.analyzer import list_analyzers as _zh_list_analyzers
from brailix.frontend.zh.pinyin import annotate as annotate_pinyin
from brailix.frontend.zh.pinyin import list_resolvers as _zh_list_resolvers

if _TYPE_CHECKING:
    from collections.abc import Callable

    from brailix.core.config import BrailleProfile
    from brailix.core.context import FrontendContext
    from brailix.core.segment import Segment
    from brailix.ir.document import Block
    from brailix.ir.inline import InlineNode

    BoundaryHandler = Callable[
        [list[InlineNode], BrailleProfile], list[InlineNode]
    ]


class _ZhFrontend(_LanguageFrontend):
    """Chinese :class:`~brailix.core.protocols.LanguageFrontend`:
    Han-aware segmentation, then tokenize → pinyin → inline IR.

    Lives here (frontend orchestration level), not inside
    ``frontend.zh.analyzer``, because it chains the analyzer with the
    pinyin resolver — and the analyzer must not import
    ``frontend.zh.pinyin`` (subsystem independence, ARCHITECTURE#arch-mediators).
    """

    # Chinese prose reaches the frontend as ``hanzi_text`` segments (Han
    # ideograph runs, emitted by this language's own ``segment`` below). The
    # Pipeline routes those to ``process`` via this declaration rather than a
    # hard-coded literal.
    prose_types = frozenset({"hanzi_text"})
    display_name = "Chinese"
    # What a caller can choose between for this language, per family. Reading
    # is a Chinese concern here: pinyin is resolved after tokenization, by an
    # engine of its own.
    adapters = {
        "analyzer": _zh_list_analyzers,
        "resolver": _zh_list_resolvers,
    }

    def segment(
        self, block: Block, ctx: FrontendContext | None = None
    ) -> list[Segment]:
        # Chinese adds no script the built-in classifier lacks — Han runs are
        # what it already recognises — so the language's lexical policy IS the
        # built-in chunking, delegated to rather than restated.
        return segment(block, ctx)

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]:
        tokens = tokenize_zh(surface, ctx)
        tokens = _shift_zh_spans(tokens, base)
        tokens = annotate_pinyin(tokens, ctx)
        return _zh_to_inline(tokens)


class _JaFrontend(_LanguageFrontend):
    """Japanese :class:`~brailix.core.protocols.LanguageFrontend`.

    Segments kana **and** kanji into one ``ja_text`` run (:func:`ja_segment
    <brailix.frontend.ja.ja_segment>` — they have to stay together for
    readings and particles to resolve across the boundary), then chains the
    morphological analyzer (selected by ``ctx.options["ja_analyzer"]``,
    default ``auto``) with ``tokens_to_inline``: the run is analyzed
    into tokens carrying katakana pronunciation-form readings, then turned
    into :class:`~brailix.ir.inline.Word` nodes. Pure kana works with no
    analyzer installed (the ``kana`` fallback); kanji readings need
    janome / fugashi / sudachi. Word-boundary spacing (文節分かち書き) is
    inserted by ``tokens_to_inline`` from the analyzer's POS — only when a
    real analyzer is present; the ``kana`` fallback (no POS) keeps the
    source's own spaces.
    """

    prose_types = frozenset({"ja_text"})
    display_name = "Japanese"
    # No ``resolver`` family: a Japanese reading comes out of the analyzer
    # itself (katakana pronunciation forms on the tokens), so there is nothing
    # to choose between after it.
    adapters = {"analyzer": _ja_list_analyzers}

    def segment(
        self, block: Block, ctx: FrontendContext | None = None
    ) -> list[Segment]:
        return _ja_segment(block, ctx)

    def process(
        self, surface: str, base: int, ctx: FrontendContext
    ) -> list[InlineNode]:
        return _ja_tokens_to_inline(_ja_analyze(surface, ctx), base)


# Per-language frontend registry — the Pipeline routes each prose
# segment to the implementation matching the profile's language. Adding
# a language = register a LanguageFrontend here (or via entry points).
language_frontend_registry: _Registry[_LanguageFrontend] = _Registry(
    "language_frontend", _LanguageFrontend
)
language_frontend_registry.register("zh", _ZhFrontend)
language_frontend_registry.register("ja", _JaFrontend)


# Per-language boundary pass — the post-frontend step that inserts
# cross-kind / word-boundary separators on the assembled inline stream
# (the orchestrator runs it once after concatenating per-segment outputs).
# Chinese inserts spaces / connectors at hanzi↔latin / number / math
# boundaries; a language with no handler passes through unchanged — its
# within-segment spacing already ran in its frontend (e.g. Japanese
# wakachigaki in ``tokens_to_inline``). Keyed by the language subtag,
# mirroring the ARCHITECTURE#arch-language-slots registries, so the
# orchestrator stays language-blind.
class _BoundaryRegistry(dict):
    """The boundary-handler table, with a generation counter.

    A plain ``dict`` in every respect a caller sees — ``boundary_registry[lang]
    = handler`` is what the extension guide documents and what the builtins
    below use — but every mutation advances :attr:`generation`, which
    :attr:`brailix.pipeline.Pipeline.fingerprint` folds in like any other
    compilation-relevant registry.

    It needs that because a boundary handler **changes the braille**: it is
    what inserts the space between a hanzi run and a Latin word, the connector
    before a number. Left out of the fingerprint, replacing one produces two
    compiles with identical ``source_hash`` and different cells — measured,
    ``Paragraph("x轴")`` compiles to ``⠰⠭⠤⠀`` and then ``⠰⠭⠀`` under one key.
    Nothing else catches it: the nodes a handler inserts carry
    ``surface=""``, so the stale-content check (which compares reconstructed
    surface against ``block.text``) sees no difference, and an
    already-populated block keeps its old spacing on recompile.

    A ``dict`` subclass rather than a :class:`~brailix.core.registry.Registry`
    on purpose: a handler is a bare callable with no protocol to validate, and
    ``Registry``'s name-based lazy-loading buys nothing here — while changing
    the public shape would break the documented ``boundary_registry[lang] = …``
    idiom for no gain.

    The cost of that choice is that ``dict``'s mutators are C-level and do
    **not** route through one another: overriding ``__setitem__`` does not
    make ``update`` or ``|=`` go through it. So every one of them is
    overridden below; missing a single one — ``|=``, say — leaves a documented
    way to swap a handler (``boundary_registry |= {"zh": h}``) without moving
    the generation, the fingerprint, or any ``source_hash``.
    ``tests/frontend/test_boundary_registry.py`` covers each mutator
    individually and fails on any inherited ``dict`` method that is not on its
    reviewed read-only list, so a mutator cannot be missed by omission.

    The counter tracks *changes*, not calls (:meth:`_bump_if_changed`): a
    mutator that leaves the table exactly as it was leaves the generation
    alone, because a bump nobody needs still costs the caller a full
    recompile.
    """

    __slots__ = ("_generation",)

    def __init__(self) -> None:
        super().__init__()
        self._generation = 0

    @property
    def generation(self) -> int:
        """Bumped by every mutation that changes the table; folded into the
        compilation fingerprint."""
        return self._generation

    def _bump_if_changed(self, before: dict[str, BoundaryHandler]) -> None:
        """Advance the generation only if the contents actually moved.

        The generation exists to invalidate: it changes the fingerprint, which
        changes every ``source_hash``, which drops every cached block. So a
        bump with no cause is not free — it is a full recompile of the open
        document for nothing. Several spellings genuinely change nothing:
        ``clear()`` on an empty table, ``update({})``, ``pop(key, default)``
        for a key that was never there, ``registry["zh"] = <the handler already
        registered>``. Each of those used to invalidate. (``setdefault`` was
        already exempted for this exact reason, with a test; this is the same
        rule applied to the rest instead of to one method.)

        Compared by **identity**, not equality: two handlers that merely
        compare equal can still behave differently, so anything short of "the
        same object is still there" counts as a change. Erring that way costs a
        recompile; erring the other way serves stale braille under an unchanged
        key, which is what folding the generation in was meant to stop.
        """
        if before.keys() != self.keys() or any(
            before[key] is not self[key] for key in self
        ):
            self._generation += 1

    def __setitem__(self, key: str, value: BoundaryHandler) -> None:
        changed = key not in self or self[key] is not value
        super().__setitem__(key, value)
        if changed:
            self._generation += 1

    def __delitem__(self, key: str) -> None:
        # A missing key raises before the bump, so a failed delete is not a
        # change either.
        super().__delitem__(key)
        self._generation += 1

    def pop(self, *args: object, **kwargs: object) -> object:
        before = dict(self)
        result = super().pop(*args, **kwargs)
        self._bump_if_changed(before)
        return result

    def popitem(self) -> tuple[str, BoundaryHandler]:
        # Raises on an empty table, so reaching the bump means one went.
        result = super().popitem()
        self._generation += 1
        return result

    def clear(self) -> None:
        had_entries = bool(self)
        super().clear()
        if had_entries:
            self._generation += 1

    def update(self, *args: object, **kwargs: object) -> None:
        before = dict(self)
        super().update(*args, **kwargs)
        self._bump_if_changed(before)

    def __ior__(self, other: object) -> _BoundaryRegistry:  # type: ignore[misc]
        # ``registry |= {...}`` is a mutation like any other; inherited from
        # ``dict`` it updates the contents in C without passing through
        # ``update`` or ``__setitem__``, so the generation — and with it the
        # Pipeline fingerprint and every ``source_hash`` derived from it —
        # stayed put while the braille changed.
        self.update(other)
        return self

    def setdefault(
        self, key: str, default: BoundaryHandler | None = None
    ) -> BoundaryHandler | None:
        had = key in self
        result = super().setdefault(key, default)
        if not had:
            self._generation += 1
        return result


boundary_registry: _BoundaryRegistry = _BoundaryRegistry()


def _zh_boundary(
    nodes: list[InlineNode], profile: BrailleProfile
) -> list[InlineNode]:
    return _zh_boundary_spaces(nodes, profile.zh_compounds)


boundary_registry["zh"] = _zh_boundary
boundary_registry["ja"] = _ja_boundary


def _apply_boundary(
    nodes: list[InlineNode], lang: str, profile: BrailleProfile
) -> list[InlineNode]:
    """Run the boundary pass registered for ``lang`` on the assembled
    inline stream; a language with no registered handler passes through
    unchanged.

    Orchestration, not an extension point: the compiler calls this once per
    run after concatenating the per-segment outputs, and what an extender
    supplies is a *handler* in :data:`boundary_registry`, which is published.
    Underscore-named because this is a **facade**, and a name that resolves
    here is API to everyone who meets it — it imports, it tab-completes, and
    nothing distinguishes it from :func:`segment` beside it. It spent a while
    as the one documented exception to that rule, recorded in an allowlist in
    the public-API test; a rule with an exception in it is a rule a reader has
    to check the test suite to know. Doing the pass by hand needs nothing
    private: ``boundary_registry.get(lang)`` and call what comes back.
    """
    handler = boundary_registry.get(lang)
    return handler(nodes, profile) if handler else nodes


def list_language_adapters(language: str, family: str = "analyzer") -> list[str]:
    """The registered adapter names a language offers in one ``family``.

    ``family`` is the kind of pluggable part: ``"analyzer"`` (the word
    segmentation / morphological engine behind ``Pipeline(analyzer=...)``) or
    ``"resolver"`` (the reading engine behind ``Pipeline(resolver=...)``, which
    Chinese uses for pinyin). A language that offers nothing in a family — a
    Japanese reading comes from its analyzer, so ``ja`` has no resolver — and a
    language with no registered frontend at all both return ``[]``.

    This is how a front-end fills an engine picker. Reading
    :func:`brailix.frontend.zh.analyzer.list_analyzers` and its Japanese twin
    instead works only for the languages the caller already knows the names of:
    the CLI listed exactly two, hard-coded, and a third language could register
    its frontend and its backend and still be invisible to it. Here
    the language declares what it offers and the caller stays language-blind
    (see ARCHITECTURE#arch-language-slots).

    Within a language the names are sorted and independent of installed extras:
    each family is its own registry of lazy loaders, so an engine's name is
    listed before its wheel is present and selecting it is what raises
    :class:`~brailix.core.errors.MissingExtraError`.

    The language's *own* frontend is a different matter — asking it what it
    offers resolves it, which runs its loader. The built-in languages are
    dependency-free at that level (their engines are what carry the weight), but
    a language that ships behind an optional package of its own raises
    ``MissingExtraError`` from here, naming the extra to install. That is
    deliberately not swallowed: an empty list would say "this language offers
    nothing to choose from", which is a different and false answer. A caller
    listing every language (``brailix --list-analyzers``, an engine picker)
    should isolate the failure per language so the rest still list.
    """
    if not language_frontend_registry.has(language):
        return []
    families = getattr(language_frontend_registry.get(language), "adapters", {})
    lister = families.get(family)
    return list(lister()) if lister is not None else []


def language_display_name(language: str) -> str:
    """A human-readable English name for ``language``, or the subtag itself.

    Declared by the language's own frontend (``display_name``), so a listing
    can group by language without any table on the reading side — which is the
    whole point: the label ships with the language, like its adapters do. An
    implementation that declares none is named by its subtag rather than by a
    guess.

    Never raises. Reading the declaration resolves the frontend, and one that
    ships behind an uninstalled extra is named by its subtag too — a label is
    what a caller needs precisely when it is about to *report* that language as
    unavailable, and a listing that sorts by this must not be the thing that
    fails (see :func:`list_language_adapters`, which does report the failure).
    """
    if not language_frontend_registry.has(language):
        return language
    try:
        frontend = language_frontend_registry.get(language)
    except _MissingExtraError:
        return language
    return getattr(frontend, "display_name", language)


__all__ = (
    "segment",
    "normalize",
    "tokenize_zh",
    "annotate_pinyin",
    "parse_math_tree",
    "language_frontend_registry",
    "language_display_name",
    "list_language_adapters",
    "boundary_registry",
)
