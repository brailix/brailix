# Extending brailix

brailix is built around one pattern: each pluggable subsystem defines a **normalized mediator format** and plugs external tools in through **adapters** chosen by name from a registry. Adding a capability almost never means editing core code — you write an adapter (or a profile, or a set of resources) and register it. See [Architecture](../ARCHITECTURE.md) for the full rationale; this page is the practical how-to.

Every **registry-selected** adapter family shares the same three pieces:

1. A **protocol** (a structural interface in `brailix.core.protocols`) your implementation satisfies.
2. A **registry** you register a loader with, under a name.
3. An optional **extra** (a `pip` dependency group) so a missing third-party package surfaces as a clear `MissingExtraError` instead of an `ImportError`.

The loader is a zero-argument callable that imports any heavy dependency and returns your implementation. Registering a loader (rather than an instance) is what keeps imports lazy: a user who never selects your adapter never imports its dependency.

One extension point is deliberately not registry-selected: an **input format** is chosen by file suffix or MIME type rather than by a name a user configures, so the input layer keeps no registry and you call your parser directly. It still has a protocol-shaped contract and an optional extra; see [Add an input format](#add-an-input-format) below.

## Add a Chinese segmentation engine

The protocol is `ChineseAnalyzer`: an object with a `name` and an `analyze(text, ctx)` method returning a list of `ChineseToken`.

Import the IR and core types from the shallow facades (`brailix.ir`, `brailix.core`) the way any other caller does — those are the names the [API reference](https://brailix.github.io/brailix/) pins. The protocols and the registries sit deeper, at `brailix.core.protocols` and each subsystem's own path, since a registry belongs with the pluggable family it serves. One core type sits deeper too: `BrailleProfile`, which every `LanguageBackend` method takes, is imported from `brailix.core.config`, the sub-package that owns profile loading. So do two helpers a language frontend reuses rather than reimplements — `segment_text` and `char_category`, from `brailix.frontend.segmentation` (see *Add a language*). That extension surface carries the same compatibility promise as the facades and is pinned by its own manifest in the test suite, so neither a registry path nor those names can be renamed out from under your adapter — everything else under those subsystems (the built-in adapters, the normalization pass, the dispatch tables) stays internal.

Each token's `span` is what makes the result traceable: it says which characters of `text` the token was read from, and every braille cell produced from that token inherits those coordinates as the source a proofreader jumps back to. brailix therefore checks the tokens as they come back from your `analyze`, and refuses four things outright with a `FrontendContractError` naming your adapter and the offending token.

1. A result that is not a list of `ChineseToken`.
2. A token whose `surface` is not a string, or whose `pos` is neither a string nor `None`.
3. A span reaching past the end of the text you were given.
4. Spans that overlap each other or run backwards. Consecutive tokens must be ordered, so each token starts at or after the point where the previous one ended.

Rules 3 and 4 are applied to the span each token *ends up with*, which matters if you omit some. You may omit spans — brailix lays the missing ones out from a running cursor — but the check runs that cursor too, so a spanless token followed by a span pointing back into it is refused even though each token would look correct on its own. If you supply spans at all, supplying them for every token is the way to be sure of what you are claiming.

One thing is deliberately allowed: a token's surface need not be the text its span points at. A tokenizer that normalises its input (full-width digits to half-width, say) legitimately reports a word that is not in the source as written, and brailix records that as a `TOKEN_SPAN_MISMATCH` warning rather than failing the document, because the braille is still correct even though the coordinates for that one word are approximate.

The worked example below keeps a cursor so its spans stay ordered, and handles the case that produces most of these violations in practice: `str.find` returning `-1` for a word the tokenizer changed.

```python
# mypkg/lac_adapter.py
from brailix.core import Span
from brailix.frontend.zh.tokens import ChineseToken
from brailix.frontend.zh.analyzer.registry import analyzer_registry


class LacAnalyzer:
    name = "lac"

    def analyze(self, text, ctx=None):
        out, cursor = [], 0
        for w in _run_lac(text):                 # your tokenizer
            start = text.find(w, cursor)
            if start < 0:
                # Your tokenizer changed this word, so it is not in the source
                # as written. Anchor it at the cursor and say so — building
                # Span(-1, ...) raises, and guessing silently would send a
                # proofreader to the wrong character.
                start = cursor
                if ctx is not None:
                    ctx.warnings.warn(
                        code="LAC_WORD_NOT_IN_TEXT",
                        message=f"lac returned {w!r}, absent from the source at {cursor}",
                        surface=w,
                        span=Span(cursor, cursor),
                        source="lac",
                    )
            end = min(start + len(w), len(text))
            out.append(ChineseToken(surface=w, pos=None, span=Span(start, end)))
            cursor = end                          # keeps the spans ordered
        return out


def _load():
    return LacAnalyzer()


analyzer_registry.register("lac", _load, extra="lac")
```

Searching from `cursor` rather than from the start of the text is what makes a repeated word resolve to the right occurrence: in `很好，很好` the second `很好` is found at position three, not at position zero again.

Once registered, select it by name — a `Pipeline` always names its braille standard as well, since there is no default profile:

```python
from brailix import Pipeline

pipe = Pipeline(profile="cn_current", analyzer="lac")
```

Add a matching `lac = ["lac"]` extra in `pyproject.toml` so the `extra="lac"` hint points users at the right install.

## Add a pinyin engine

The protocol is `PinyinResolver`: `name` plus `resolve(tokens, ctx)`. The resolver fills each token's `pinyin` field (numeric-tone form) and must not change token boundaries or types; low-confidence readings should be reported through `ctx.warnings`. Register with `resolver_registry` from `brailix.frontend.zh.pinyin.registry`, then select it with `Pipeline(profile="cn_current", resolver="your-name")`.

The two fields you may write are checked on the way back, with the same `FrontendContractError` naming your adapter and the token: `pinyin` is a string or `None`, and `confidence` is a probability — a finite number in `[0, 1]`, since brailix compares it against a threshold to decide whether to warn about the reading. An integer is accepted and stored as the `float` the field declares. Everything else about a token — how many there are, their order, surface, span and `pos` — is compared against what you were handed.

"Must not change boundaries or types" is checked, not merely asked for. brailix compares the tokens you return against the ones it handed you, and raises `FrontendContractError` if the number of tokens, their order, or any surface, span or part-of-speech tag has moved. The two fields a resolver owns are `pinyin` and `confidence`; return the same tokens with those filled in, either by building fresh ones with `dataclasses.replace` or by handing back the list you were given. Segmentation belongs to the analyzer, so a word you would rather see divided differently is a matter for a `ChineseAnalyzer`, not for a resolver that splits it on the way past.

## Add a math source format

The math subsystem's mediator is **MathML**: every source format is converted to a MathML string, which the backend walks as the IR. The protocol is `MathSourceAdapter`: a `source` attribute and `to_mathml(formula, ctx) -> str`. An adapter only ever produces valid MathML; on error it returns a `<merror>` element and the pipeline recovers.

```python
from brailix.frontend.math.registry import math_source_registry

class AsciiMathAdapter:
    source = "asciimath"
    def to_mathml(self, formula, ctx=None):
        return _asciimath_to_mathml(formula)   # returns a MathML string

math_source_registry.register("asciimath", lambda: AsciiMathAdapter(), extra="asciimath")
```

## Add a music source format

Symmetric to math, with **MusicXML** as the mediator. The protocol is `MusicSourceAdapter`: a `source` attribute and `to_musicxml(src, ctx) -> str`. Register with `music_source_registry` from `brailix.frontend.music.registry`.

## Add a tactile-graphic source format

The third vertical follows the same shape, with **SVG** as the mediator: every graphic source is converted to an SVG string, and the tactile backend rasterizes that tree into dots. The protocol is `GraphicSourceAdapter`: a `source` attribute and `to_svg(src, ctx) -> str`, where `ctx` is a `GraphicsContext`. Register with `graphic_source_registry` from `brailix.frontend.graphics.registry`.

```python
from brailix.core import GraphicsContext
from brailix.frontend.graphics.registry import graphic_source_registry


class DotPlotAdapter:
    source = "dotplot"

    def to_svg(self, src: str | bytes, ctx: GraphicsContext | None = None) -> str:
        return _plot_to_svg(src)          # returns an SVG string


graphic_source_registry.register("dotplot", lambda: DotPlotAdapter(), extra="dotplot")
```

Unlike math and music, the graphics entry point soft-fails the two failures an adapter is expected to have: a missing adapter and a failed conversion both degrade to an SVG carrying a `data-bk-error` marker, which the backend surfaces as a `GRAPHICS_SOFT_FAIL` warning and a blank figure, so a document with an unreadable figure still compiles. Two things still propagate, by design: a `StrictModeError` — strict mode means the first diagnostic stops the run, whichever subsystem reported it — and a genuine programming error, which is a bug to fix rather than a figure to blank out. Your adapter should follow the same rule and return an error-marked SVG rather than raise.

Once registered, the name is selectable two ways: as the source format of a standalone compile, `translate_graphic(src, source_format="dotplot")`, and as the suffix of an embedded figure's fence in a document, ```` ```graphic-dotplot ````.

## Add an input format

An input adapter reads one document format and returns a `DocumentIR` with block structure populated (inline content stays as raw `Block.text` until the frontend runs). The input layer keeps no registry — the choice is usually static (a file suffix or MIME type) — so you call your parser directly, or add a branch to your own dispatch. `brailix.input.parse_file` is the suffix dispatch the library ships; mirror its shape for a new format.

## Add a renderer

A renderer is the dumb encoder at the end of the pipeline: it turns one *output-domain IR* into a concrete output and understands no source language. The protocol is `Renderer`: a `name` and `render(ir) -> Any` (the return type is deliberately open — a string, bytes, a cell list, HTML, or JSON). Register a loader with `renderer_registry` from `brailix.renderer`:

```python
from brailix.renderer import renderer_registry

class PefRenderer:
    name = "pef"
    def render(self, bir):
        return _to_pef_xml(bir)

renderer_registry.register("pef", lambda: PefRenderer())
```

Select it with `result.render("pef")`.

**Say which IR you consume.** One registry holds the renderers of both output domains, so a renderer declares the IR it reads through a `consumes` attribute:

| `consumes` | Input IR | Built-in renderers | Reached from |
|---|---|---|---|
| `"braille"` (the default) | `BrailleDocument` / `BrailleSequence` of `BrailleCell` | `unicode`, `brf`, `cells`, `layout` | `TranslationResult.render(...)` |
| `"tactile_raster"` | `TactileRaster` (a grid of raise levels) | `bmp`, `png`, `pdf`, `tactile_preview` | `GraphicResult.render(...)`, `TactilePageResult.render(...)` |

`PefRenderer` above omits the attribute, which means `"braille"` — that default is what keeps a renderer written before the tactile domain existed valid. A renderer for the other domain says so:

```python
class SwellRenderer:
    name = "swell"
    consumes = "tactile_raster"          # not a braille IR

    def render(self, raster):
        return _to_swell_bytes(raster)   # raster.width / .height / .data

renderer_registry.register("swell", lambda: SwellRenderer())
```

Each result object checks that declaration before handing over its IR, so a domain mismatch is an `IncompatibleRendererError` naming both sides rather than a crash inside your `render`. It is also what a braille-only front-end filters on: `brailix --list-renderers` and `--to` offer the `"braille"` renderers only, since a text translation has no raster to give a tactile one.

## Add a braille profile (a new standard)

A different braille standard is **data, not code**: a profile JSON plus its resource tables. There is deliberately no backend to subclass — the backend is a node-type dispatcher, and the rules it applies come from the profile. To add a standard:

1. Put the rule tables under `brailix/resources/<region>/<scheme>/` (initials, finals, tones, punctuation, math symbols, and so on). Shared tables (the named cell pool, numbers, Latin, Greek, music) already live at the top of `resources/` and are reused.
2. Write a profile JSON under `brailix/profiles/<name>.json` whose `language` and `tables` point at those resources, and whose `features` toggle the behaviour switches.
3. Select it with `Pipeline(profile="<name>")`. To load a profile from outside the package (a user folder), pass `extra_profile_paths=[...]` to the `Pipeline`.

## Add a language

Supporting a new language (Japanese, Korean, and so on) is additive — the orchestrator stays language-agnostic, and you register at a few seams plus add resources. In brief:

1. **Frontend** (`LanguageFrontend` protocol) — one registration carrying both halves of the language, under the language subtag in `frontend.language_frontend_registry`:

    - `segment(block, ctx)` — your language's lexical policy: cut the raw text into typed segments, tagging your own prose with your own type. Do not write a chunker from scratch. `frontend.segmentation.segment_text(text, base_offset, categorize=...)` already handles the language-neutral part — `$...$` math islands, IPA regions, digit runs (decimal point included), Latin, Greek, one segment per punctuation mark — and takes your character classifier as a parameter. A classifier is usually a few lines: claim your script, hand everything else to `frontend.segmentation.char_category`. If the built-in classification already covers your writing system, delegate straight to `frontend.segmentation.segment`.
    - `process(surface, base, ctx)` — turn one prose run into inline IR (tokenize, annotate the reading, build nodes).
    - `prose_types` — which segment types are this language's prose. The Pipeline routes a segment back to `process` by this declaration, so the type stays writing-system accurate while routing stays language-driven.

    Two optional declarations make the language visible where a user picks one: `display_name` (the English name a listing shows) and `adapters`, a `{family: () -> list[str]}` mapping naming what can be chosen for this language in each family — `"analyzer"` for the segmentation or morphological engine, `"resolver"` for a reading engine where the language has one. `brailix --list-analyzers` and any engine picker read them through `frontend.list_language_adapters`, so a language that declares them appears there with no change to the front-end:

    ```python
    from brailix.frontend import language_frontend_registry
    from brailix.frontend.segmentation import char_category, segment_text


    def _ko_category(ch):
        return "hangul_text" if _is_hangul(ch) else char_category(ch)


    class KoFrontend:
        prose_types = frozenset({"hangul_text"})
        display_name = "Korean"
        # Any zero-argument callable returning the registered names.
        adapters = {"analyzer": ko_analyzer_registry.names}

        def segment(self, block, ctx=None):
            text = block.text or ""
            base = block.span.start if block.span is not None else 0
            return segment_text(text, base_offset=base, categorize=_ko_category)

        def process(self, surface, base, ctx): ...


    language_frontend_registry.register("ko", lambda: KoFrontend())
    ```

    Both methods are required — the registry runs a runtime protocol check the first time it resolves your adapter, so half a language is rejected at `get()`. Reading either optional declaration also resolves the frontend, so if your language ships behind an optional package of its own, listing it needs that package installed. Nothing breaks without it: `brailix --list-analyzers` reports your language on standard error with the extra to install, prints the rest of the listing, and still exits 0. Keep the registration itself light (register a loader, not an eager import) and the engines behind their own registry, and the weight is paid only when a document is actually translated.

2. **Backend** (`LanguageBackend` protocol) — translate prose nodes into cells by the language's braille rules; register in `backend.dispatch.language_backend_registry`. Two methods, both required: `translate_word` (`Word` — a prose word of any length, single characters included) and `translate_date_marker` (`DateComponent` — one `<digits><marker>` unit of a date, such as `2026年`). The registry runs a runtime protocol check the first time it resolves your adapter, so one missing method means rejection at `get()` rather than at registration. `translate_date_marker` owns both the marker's reading and whether a joiner cell follows the digits — a language with no special date rules still writes an explicit implementation, since there is no inherited default:

    ```python
    from brailix.core import BackendContext
    from brailix.core.config import BrailleProfile
    from brailix.backend.dispatch import language_backend_registry


    class KoBackend:
        def translate_word(self, node, ctx, profile):
            return _ko_word_to_cells(node, profile)

        def translate_date_marker(self, component, ctx, profile):
            # No special rule: write the marker the way ordinary prose is
            # written. ``component.digits`` is what a connector rule would
            # look at; ``component.marker`` / ``.marker_span`` / ``.reading``
            # are the marker itself.
            return _ko_marker_to_cells(component, profile)


    language_backend_registry.register("ko", lambda: KoBackend())
    ```

    Language-neutral nodes (numbers, punctuation, Latin, math, music) keep going through the shared dispatch.
3. **Resources and profile** — put the rule tables under `resources/<language>/` and write a profile whose `language` points at the new language.
4. **Boundary pass** (optional) — for cross-kind or word-boundary separators on the assembled inline stream (Chinese spaces hanzi↔Latin; Japanese inserts a number joiner), register a handler in `frontend.boundary_registry` under the language subtag.

There is nothing to register for normalization: turning a digit run into a `Number`, a date pattern into a `Date` and a `$...$` island into a `MathInline` is a fixed pass, the same for every language.

The existing IR node set is enough: `Word`, `Date` and the language-neutral `reading` field carry an ideographic or a phonetic language without new node types (a single character is a one-character `Word`, not a type of its own). **Japanese is a shipped worked example**: `frontend.ja` (kana/kanji segmentation, a morphological-analysis subsystem with janome / fugashi / sudachi adapters, and 文節 word-spacing) plus `backend.ja` (kana → cells) plus `resources/ja/` and `profiles/ja_current.json`. The Architecture document's "Adding a language" section walks through each seam in detail.

## Packaging an adapter as a separate distribution

Your adapter's `register(...)` call runs when its module is imported. To make a separately-installed adapter available by name without the user importing it explicitly, expose it through your application's discovery mechanism (an entry point group, a plugin loader, or an explicit import at startup). Within this repository, the built-in adapters simply call `register(...)` at import time.
