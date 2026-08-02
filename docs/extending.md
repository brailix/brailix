# Extending brailix

brailix is built around one pattern: each pluggable subsystem defines a **normalized mediator format** and plugs external tools in through **adapters** chosen by name from a registry. Adding a capability almost never means editing core code — you write an adapter (or a profile, or a set of resources) and register it. See [Architecture](../ARCHITECTURE.md) for the full rationale; this page is the practical how-to.

Every extension point shares the same three pieces:

1. A **protocol** (a structural interface in `brailix.core.protocols`) your implementation satisfies.
2. A **registry** you register a loader with, under a name.
3. An optional **extra** (a `pip` dependency group) so a missing third-party package surfaces as a clear `MissingExtraError` instead of an `ImportError`.

The loader is a zero-argument callable that imports any heavy dependency and returns your implementation. Registering a loader (rather than an instance) is what keeps imports lazy: a user who never selects your adapter never imports its dependency.

## Add a Chinese segmentation engine

The protocol is `ChineseAnalyzer`: an object with a `name` and an `analyze(text, ctx)` method returning a list of `ChineseToken`.

Import the IR and core types from the shallow facades (`brailix.ir`, `brailix.core`) the way any other caller does — those are the names the [API reference](https://brailix.github.io/brailix/) pins. The protocols and the registries sit deeper, at `brailix.core.protocols` and each subsystem's own path, since a registry belongs with the pluggable family it serves. One core type sits deeper too: `BrailleProfile`, which every `LanguageBackend` method takes, is imported from `brailix.core.config`, the sub-package that owns profile loading. That extension surface carries the same compatibility promise as the facades and is pinned by its own manifest in the test suite, so neither a registry path nor that type can be renamed out from under your adapter — everything else under those subsystems (the built-in adapters, the normalizers, the dispatch tables) stays internal.

```python
# mypkg/lac_adapter.py
from brailix.core import Span
from brailix.frontend.zh.tokens import ChineseToken
from brailix.frontend.zh.analyzer.registry import analyzer_registry


class LacAnalyzer:
    name = "lac"

    def analyze(self, text, ctx=None):
        words = _run_lac(text)           # your tokenizer
        out, cursor = [], 0
        for w in words:
            start = text.find(w, cursor)
            out.append(ChineseToken(surface=w, pos=None, span=Span(start, start + len(w))))
            cursor = start + len(w)
        return out


def _load():
    return LacAnalyzer()


analyzer_registry.register("lac", _load, extra="lac")
```

Once registered, select it by name — a `Pipeline` always names its braille standard as well, since there is no default profile:

```python
from brailix import Pipeline

pipe = Pipeline(profile="cn_current", analyzer="lac")
```

Add a matching `lac = ["lac"]` extra in `pyproject.toml` so the `extra="lac"` hint points users at the right install.

## Add a pinyin engine

The protocol is `PinyinResolver`: `name` plus `resolve(tokens, ctx)`. The resolver fills each token's `pinyin` field (numeric-tone form) and must not change token boundaries or types; low-confidence readings should be reported through `ctx.warnings`. Register with `resolver_registry` from `brailix.frontend.zh.pinyin.registry`, then select it with `Pipeline(profile="cn_current", resolver="your-name")`.

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

1. **Segmenter** (`Segmenter` protocol) — recognize the writing system and cut prose into typed segments; register in `frontend.segment.segmenter_registry` under the language subtag.
2. **Frontend** (`LanguageFrontend` protocol) — turn a prose run into inline IR (segment, annotate the reading, build nodes); declare the `prose_types` it consumes; register in `frontend.language_frontend_registry`. Two optional declarations make the language visible where a user picks one: `display_name` (the English name a listing shows) and `adapters`, a `{family: () -> list[str]}` mapping naming what can be chosen for this language in each family — `"analyzer"` for the segmentation or morphological engine, `"resolver"` for a reading engine where the language has one. `brailix --list-analyzers` and any engine picker read them through `frontend.list_language_adapters`, so a language that declares them appears there with no change to the front-end:

    ```python
    class KoFrontend:
        prose_types = frozenset({"hangul_text"})
        display_name = "Korean"
        # Any zero-argument callable returning the registered names.
        adapters = {"analyzer": ko_analyzer_registry.names}

        def process(self, surface, base, ctx): ...
    ```

    Reading either declaration resolves the frontend, so if your language ships behind an optional package of its own, listing it needs that package installed. Nothing breaks without it: `brailix --list-analyzers` reports your language on standard error with the extra to install, prints the rest of the listing, and still exits 0. Keep the registration itself light (register a loader, not an eager import) and the engines behind their own registry, and the weight is paid only when a document is actually translated.

3. **Backend** (`LanguageBackend` protocol) — translate prose nodes into cells by the language's braille rules; register in `backend.dispatch.language_backend_registry`. Two methods, both required: `translate_word` (`Word` — a prose word of any length, single characters included) and `translate_date_marker` (`HanziMarker`). The registry runs a runtime protocol check the first time it resolves your adapter, so one missing method means rejection at `get()` rather than at registration. `translate_date_marker` owns both the marker's reading and whether a joiner cell follows a number — a language with no special date rules still writes an explicit implementation, since there is no inherited default:

    ```python
    from brailix.core import BackendContext
    from brailix.core.config import BrailleProfile
    from brailix.backend.dispatch import language_backend_registry


    class KoBackend:
        def translate_word(self, node, ctx, profile):
            return _ko_word_to_cells(node, profile)

        def translate_date_marker(self, marker, follows_number, ctx, profile):
            # No special rule: write the marker the way ordinary prose is written.
            return _ko_word_to_cells(marker, profile)


    language_backend_registry.register("ko", lambda: KoBackend())
    ```

    Language-neutral nodes (numbers, punctuation, Latin, math, music) keep going through the shared dispatch.
4. **Normalizer** (`Normalizer` protocol, as needed) — if the language has its own structural conventions; otherwise reuse the default.
5. **Resources and profile** — put the rule tables under `resources/<language>/` and write a profile whose `language` points at the new language.
6. **Boundary pass** (optional) — for cross-kind or word-boundary separators on the assembled inline stream (Chinese spaces hanzi↔Latin; Japanese inserts a number joiner), register a handler in `frontend.boundary_registry` under the language subtag.

The existing IR node set is enough: `Word`, `HanziMarker`, and the language-neutral `reading` field carry an ideographic or a phonetic language without new node types (a single character is a one-character `Word`, not a type of its own). **Japanese is a shipped worked example**: `frontend.ja` (a kana/kanji segmenter, a morphological-analysis subsystem with janome / fugashi / sudachi adapters, and 文節 word-spacing) plus `backend.ja` (kana → cells) plus `resources/ja/` and `profiles/ja_current.json`. The Architecture document's "Adding a language" section walks through each seam in detail.

## Packaging an adapter as a separate distribution

Your adapter's `register(...)` call runs when its module is imported. To make a separately-installed adapter available by name without the user importing it explicitly, expose it through your application's discovery mechanism (an entry point group, a plugin loader, or an explicit import at startup). Within this repository, the built-in adapters simply call `register(...)` at import time.
