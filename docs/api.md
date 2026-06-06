# API reference

This page documents the **stable public surface** of brailix. That surface is the top-level `brailix` package plus the `brailix.ir`, `brailix.core`, `brailix.core.models`, and `brailix.renderer` sub-packages; the input and frontend entry points below are public too. A test (`tests/test_public_api.py`) pins these names so a refactor that drops or renames one fails loudly.

Import from these facade modules rather than from concrete internal modules (for example, import `Span` from `brailix.core`, not from `brailix.core.span`).

## `brailix` — the top level

### `Pipeline`

The end-to-end entry point. Construct one and call a `translate_*` method.

```python
from brailix import Pipeline

pipe = Pipeline(
    profile="cn_current",      # a profile name under brailix/profiles/
    mode="normal",             # "strict" / "normal" / "lenient"
    segmenter="auto",
    normalizer="auto",
    analyzer="auto",           # Chinese tokenizer: auto / char / jieba / thulac / hanlp
    resolver="auto",           # pinyin: auto / null / pypinyin / g2pm / g2pw
    user_pinyin_dict={},       # optional surface→reading overrides (multi-char keys)
    default_renderer="unicode",
    extra_profile_paths=(),    # extra dirs searched for profile JSON before the builtins
)
```

Every adapter family is selected by string and resolved through an internal registry; `"auto"` lets brailix pick the best installed implementation.

Methods and properties:

- `translate_text(text: str) -> TranslationResult` — wrap `text` in one paragraph and run the whole pipeline.
- `translate_document(doc: DocumentIR) -> TranslationResult` — translate a full document, block by block.
- `translate_file(path) -> TranslationResult` — read a file (suffix-dispatched) and translate it; convenience over `parse_file` plus `translate_document`.
- `translate_math_inline(surface: str, source: str) -> str` — translate a single inline formula (`source` is the math source format, for example `"latex"`) to a braille string.
- `parse_text(text: str, *, format: str = "plain") -> DocumentIR` — parse without translating; `format` is `"plain"`, `"markdown"`, or `"musicxml"`.
- `parse_file(path) -> DocumentIR` — read and parse a file to a `DocumentIR` without translating (suffix-dispatched).
- `translate_block(block, *, ir_transformer=None, tree_subcache=None) -> CompiledBlock` — the incremental-compilation primitive: compile one block in isolation. `ir_transformer` is an optional in-place mutation hook that runs between frontend and backend.
- `profile_name -> str` and `profile_language -> str` — the resolved profile's name and language tag, exposed so a caller can record a document's identity without reaching into private state.

### `TranslationResult`

Returned by the `translate_*` methods. Rendering is deferred — call `render` to produce a concrete format.

- `render(name: str | None = None)` — render through the named renderer (defaults to the Pipeline's `default_renderer`). Returns `str` for Unicode braille, `bytes` for BRF, and other types for the cells / layout renderers.
- `ir: DocumentIR` — the parsed document IR.
- `braille_ir: BrailleDocument` — the backend's cell sequence.
- `warnings: WarningCollector` — diagnostics gathered during the run.
- `proofread_json() -> dict` — a JSON-ready mapping of source text to braille IR plus warnings, for proofreading tools. It does not pre-render any output.
- `text: str` — the source text.

### `CompiledBlock`

The result of `Pipeline.translate_block` — a block-level compilation unit for incremental recompilation. It carries the populated `ir`, the `braille_blocks`, the `warnings`, a `tree_subcache` of parsed math/music trees for reuse, a stable `source_hash` for cache keying, and a `compiled_at` timestamp. The Pipeline produces these but does not keep a cache itself — caching is the caller's job.

### `TreeSubcache`

A type alias: `dict[(domain, source, surface), ET.Element]`, a reuse pool of parsed MathML / MusicXML trees keyed by `domain` (`"math"` or `"music"`), source format, and surface text. Pass a prior compile's subcache back into `translate_block` so an unchanged formula or score is not re-parsed.

### `block_hash`

`block_hash(block, profile_name) -> str` — the stable digest used as `CompiledBlock.source_hash`. A front-end that wants override-aware caching composes this with its own salt.

## `brailix.input` — document sources

Each function returns a `DocumentIR` with block structure populated; inline content stays as raw text until the frontend runs.

- `parse_file(path, *, language=..., profile=...)` — read a file and parse by suffix: `.md` / `.markdown` use the Markdown adapter; `.docx` / `.docm` use `parse_docx`; `.doc` uses `parse_doc`; `.musicxml` / `.mxl` use `parse_musicxml`; a `.xml` file uses `parse_musicxml` only when its head looks like a MusicXML score (`<score-partwise>` / `<score-timewise>`), otherwise it is treated as plain text, since `.xml` is a generic container; `.mid` / `.midi` / `.abc` use `parse_score_file`; and everything else (including `.txt`) uses `parse_plain`.
- `parse_plain(text, ...)` — one paragraph from a string.
- `parse_markdown(text, ...)` — a common Markdown subset: headings, paragraphs, ordered and unordered lists, block quotes, fenced code blocks, `$$...$$` math blocks, and `| col | col |` tables.
- `parse_docx(path, ...)` and `parse_doc(path, ...)` — Word documents. `parse_docx` reads modern OOXML (`.docx` / `.docm`, including MathType and OMML formulae) and needs the `docx` extra; `parse_doc` reads the legacy binary `.doc` and needs a LibreOffice `soffice` install on `PATH`.
- `parse_musicxml(path, ...)` — MusicXML and compressed `.mxl` scores (and a `.xml` file that is actually a score).
- `parse_score_file(path, ...)` — score formats that reach MusicXML through a source adapter: MIDI (`.mid` / `.midi`, needs the `midi` extra) and ABC (`.abc`, needs the `abc` extra). The file is converted to MusicXML when it is read and wrapped as a score block, so the rest of the pipeline treats it exactly like a MusicXML file; a missing extra raises `MissingExtraError` naming the package to install.

## `brailix.ir` — the intermediate representations

Document-level blocks: `DocumentIR`, `Block`, `Heading`, `Paragraph`, `List`, `Table`, `MathBlock`, `ScoreBlock`. Inline nodes: `InlineNode`, `Word`, `HanziChar`, `Number`, `Punct`, `Space`, `MathInline`, `MusicInline`. Braille output: `BrailleCell`, `BrailleDocument`, and `BLANK_CELL` (the empty braille cell, U+2800).

A `BrailleCell` carries its `dots` (a tuple such as `(1, 2, 4)`), an optional `unicode` character, a `role` (for example `"zh_syllable"` or `"number_sign"`), and the `source_span` plus `source_text` it was produced from. Math and music inline nodes hold their normalized MathML / MusicXML tree directly (an `xml.etree.ElementTree.Element`), because that tree is the subsystem's IR.

## `brailix.core` — shared types

- `Span` and `merge_spans` — source-position tracking for IR nodes.
- `Warning` and `WarningCollector` — structured diagnostics (a code, a level, a message, the surface, candidate readings, and a span).
- `BrailixError` — the base exception type.
- `FrontendContext`, `BackendContext`, `MathContext`, `MusicContext` — the per-stage context objects passed to adapters.
- `RunMode` — the `strict` / `normal` / `lenient` policy enum.
- `DEFAULT_PROFILE` and `DEFAULT_LANGUAGE` — the built-in defaults.

## `brailix.core.models` — downloadable model assets

The adapter-facing half of model management (the network downloader lives in a separate front-end layer, not in this library).

- `ModelAsset` — an adapter's declaration that it needs a weight on disk.
- `all_assets() -> list[ModelAsset]` — every registered asset, name-sorted.
- `get_model_dir(name)` and `get_models_root()` — resolve where models live (next to the executable in a frozen build, under the working directory in development).
- `set_managed_download(enabled=True)` and `is_managed_download()` — the download policy. By default an adapter that needs a missing model lets its backend auto-download it on first use. A front-end that ships its own download manager calls `set_managed_download(True)` so adapters instead raise `ModelNotInstalledError` and defer the fetch to that manager.

## `brailix.renderer` — output encoders

- `renderer_registry` — a name-to-renderer registry. Built-in names: `"unicode"` (Unicode braille string), `"brf"` (BRF bytes), `"cells"` (a dot/cell array), and `"layout"` (a laid-out page with line breaks, indentation, and pagination).
- `LayoutOptions` and `LayoutRenderer` — configuration and the renderer for laid-out output.
- `cell_to_char(cell) -> str` — the single-cell Unicode-braille mapping.

```python
from brailix.renderer import renderer_registry
out = renderer_registry.get("unicode").render(result.braille_ir)
```

## `brailix.frontend` — lower-level entry points

These are public for advanced use and for building tools on top of the frontend:

- `parse_math_tree(...)` — parse a math formula (LaTeX, MathML, and so on) into a normalized MathML tree.
- `segment(...)` and `normalize(...)` — block segmentation and number/date/unit tagging.
- `tokenize_zh(...)` and `annotate_pinyin(...)` — the Chinese segmentation and pinyin steps.
- `language_frontend_registry` — the per-language frontend registry (see [Extending brailix](extending.md)).

## Errors

- `brailix.core.BrailixError` — base class for the library's exceptions.
- `MissingExtraError` (from `brailix.core`) — raised when an adapter's optional dependency is not installed; its message names the extra to `pip install`.
- `ParseError` — malformed input.
- `ModelNotInstalledError` — a needed model is absent (only raised under managed download; see `set_managed_download` above).
