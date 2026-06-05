# Extending brailix

brailix is built around one pattern: each pluggable subsystem defines a **normalized mediator format** and plugs external tools in through **adapters** chosen by name from a registry. Adding a capability almost never means editing core code — you write an adapter (or a profile, or a set of resources) and register it. See [Architecture](../ARCHITECTURE.md) for the full rationale; this page is the practical how-to.

Every extension point shares the same three pieces:

1. A **protocol** (a structural interface in `brailix.core.protocols`) your implementation satisfies.
2. A **registry** you register a loader with, under a name.
3. An optional **extra** (a `pip` dependency group) so a missing third-party package surfaces as a clear `MissingExtraError` instead of an `ImportError`.

The loader is a zero-argument callable that imports any heavy dependency and returns your implementation. Registering a loader (rather than an instance) is what keeps imports lazy: a user who never selects your adapter never imports its dependency.

## Add a Chinese segmentation engine

The protocol is `ChineseAnalyzer`: an object with a `name` and an `analyze(text, ctx)` method returning a list of `ChineseToken` (from `brailix.ir.inline`).

```python
# mypkg/lac_adapter.py
from brailix.core.span import Span
from brailix.ir.inline import ChineseToken
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

Once registered, select it with `Pipeline(analyzer="lac")`. Add a matching `lac = ["lac"]` extra in `pyproject.toml` so the `extra="lac"` hint points users at the right install.

## Add a pinyin engine

The protocol is `PinyinResolver`: `name` plus `resolve(tokens, ctx)`. The resolver fills each token's `pinyin` field (numeric-tone form) and must not change token boundaries or types; low-confidence readings should be reported through `ctx.warnings`. Register with `resolver_registry` from `brailix.frontend.zh.pinyin.registry`, then select with `Pipeline(resolver="...")`.

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

## Add an input format

An input adapter reads one document format and returns a `DocumentIR` with block structure populated (inline content stays as raw `Block.text` until the frontend runs). The input layer keeps no registry — the choice is usually static (a file suffix or MIME type) — so you call your parser directly, or add a branch to your own dispatch. `brailix.input.parse_file` is the suffix dispatch the library ships; mirror its shape for a new format.

## Add a renderer

A renderer encodes a braille IR into a concrete output and understands no source language. The protocol is `Renderer`: a `name` and `render(bir) -> Any` (the return type is deliberately open — a string, bytes, a cell list, HTML, or JSON). Register a loader with `renderer_registry` from `brailix.renderer`:

```python
from brailix.renderer import renderer_registry

class PefRenderer:
    name = "pef"
    def render(self, bir):
        return _to_pef_xml(bir)

renderer_registry.register("pef", lambda: PefRenderer())
```

Select it with `result.render("pef")`.

## Add a braille profile (a new standard)

A different braille standard is **data, not code**: a profile JSON plus its resource tables. There is deliberately no backend to subclass — the backend is a node-type dispatcher, and the rules it applies come from the profile. To add a standard:

1. Put the rule tables under `brailix/resources/<region>/<scheme>/` (initials, finals, tones, punctuation, math symbols, and so on). Shared tables (the named cell pool, numbers, Latin, Greek, music) already live at the top of `resources/` and are reused.
2. Write a profile JSON under `brailix/profiles/<name>.json` whose `language` and `tables` point at those resources, and whose `features` toggle the behaviour switches.
3. Select it with `Pipeline(profile="<name>")`. To load a profile from outside the package (a user folder), pass `extra_profile_paths=[...]` to the `Pipeline`.

## Add a language

Supporting a new language (Japanese, Korean, and so on) is additive — the orchestrator stays language-agnostic, and you register at a few seams plus add resources. In brief:

1. **Segmenter** (`Segmenter` protocol) — recognize the writing system and cut prose into typed segments; register in `frontend.segment.segmenter_registry` under the language subtag.
2. **Frontend** (`LanguageFrontend` protocol) — turn a prose run into inline IR (segment, annotate the reading, build nodes); declare the `prose_types` it consumes; register in `frontend.language_frontend_registry`.
3. **Backend** (`LanguageBackend` protocol) — translate prose nodes (`Word`, `HanziChar`) into cells by the language's braille rules; register in `backend.dispatch.language_backend_registry`. Language-neutral nodes (numbers, punctuation, Latin, math, music) keep going through the shared dispatch.
4. **Normalizer** (`Normalizer` protocol, as needed) — if the language has its own structural conventions; otherwise reuse the default.
5. **Resources and profile** — put the rule tables under `resources/<language>/` and write a profile whose `language` points at the new language.
6. **Boundary pass** (optional) — for cross-kind or word-boundary separators on the assembled inline stream (Chinese spaces hanzi↔Latin; Japanese inserts a number joiner), register a handler in `frontend.boundary_registry` under the language subtag.

The existing IR node set is enough: `Word`, `HanziChar`, and the language-neutral `reading` field carry an ideographic or a phonetic language without new node types. **Japanese is a shipped worked example**: `frontend.ja` (a kana/kanji segmenter, a morphological-analysis subsystem with janome / fugashi / sudachi adapters, and 文節 word-spacing) plus `backend.ja` (kana → cells) plus `resources/ja/` and `profiles/ja_current.json`. The Architecture document's "Adding a language" section walks through each seam in detail.

## Packaging an adapter as a separate distribution

Your adapter's `register(...)` call runs when its module is imported. To make a separately-installed adapter available by name without the user importing it explicitly, expose it through your application's discovery mechanism (an entry point group, a plugin loader, or an explicit import at startup). Within this repository, the built-in adapters simply call `register(...)` at import time.
