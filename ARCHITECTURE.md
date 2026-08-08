<!-- brailix architecture overview (English). The overview is maintained in more
     than one language and kept in sync by hand; each copy is organised on its
     own terms, so the structure may differ between them. -->

# brailix Architecture

## 1. What brailix is

`brailix` is a **braille compiler**: it takes text or documents from any source, runs them through frontend structural analysis, a unified intermediate representation (IR), and a pluggable backend, and compiles them into something a hand can read. There are two output domains: text, mathematics and music compile to **braille** (Unicode Braille, BRF, a dot array, or a laid-out braille page), and vector graphics rasterize to a **tactile image** (an embossable BMP, PNG or PDF, or a preview on a refreshable display).

**Scope.** `brailix` is exactly the *compilation path* — source → semantic IR → output-domain IR → encoded output. A generic `Pipeline.translate_block(ir_transformer=...)` hook lets a front-end insert its own IR transform between the frontend and the backend, so a CLI, a server, a textbook-publishing system, or an editing UI can build its own features on top of the compiler core. That keeps `brailix` usable as a standalone library.

Design goals:

- **Pluggable** — the tokenizer, pinyin engine, math parser, braille rules, and output format are all replaceable.
- **Profile-driven** — the same IR can be rendered by different braille standards (mainland Chinese schemes, UEB, Nemeth, textbook-specific).
- **Traceable** — every braille cell maps back to a source span, which makes human proofreading easy.
- **Structure-preserving** — numbers, formulae, and English each travel their own track through the frontend, keeping their native structure.

Requirements: Python `>=3.13` (the code uses `match` and modern type syntax).

**How the code cites this document.** Section *numbers* are not stable
references: the overview is maintained in more than one language, each copy
organised on its own terms, so one number names different sections in different
copies and a comment citing it would be right in at most one of them. Code
therefore cites a **stable anchor** instead, written
`ARCHITECTURE#arch-boundaries`, which is both a working link and a string you can
search for. Each anchor is declared as an `<a id="...">` above the section it
names, in every copy, and `tests/test_architecture_anchors.py` fails if code
cites an anchor that any copy in the checkout leaves undeclared. Renumbering or
reordering a section is free; moving an invariant means moving its anchor with
it.

---

## 2. Two ideas the whole design rests on

Everything below is an application of two decisions.

<a id="arch-mediators"></a>
### 2.1 Normalized mediators and adapters

> For each subsystem that has a choice of external library, `brailix` defines its own **normalized mediator format** and plugs the external tools in through **adapters**, so the library stays independent of any one third-party implementation.

Each such subsystem is built the same three-part way: an adapter converts some external input into the mediator format, and every downstream consumer reads only the mediator.

| Subsystem | normalized mediator | what downstream sees |
|---|---|---|
| Chinese segmentation | `ChineseToken` | PinyinResolver, IRBuilder |
| Pinyin | pinyin annotation (numeric tones) | Backend |
| Math parsing | **MathML (`ET.Element`)** | MathBraille backend |
| Music parsing | **MusicXML tree (`ET.Element`)** | MusicBraille backend |
| Graphics parsing | **SVG tree (`ET.Element`)** | Tactile backend |
| Document input | `DocumentIR` | Frontend |
| Braille output | `BrailleIR` | braille renderers (unicode / BRF / cells / layout) |
| Tactile rasterization | `TactileRaster` | tactile renderers (BMP / PNG / PDF / preview) |

Whichever adapter you pick, downstream only ever sees the mediator format, so **swapping an adapter leaves every line of downstream code untouched.** The same property is what makes each layer testable on its own: feed a fixed mediator value in, assert on the mediator value out.

<a id="arch-traceability"></a>
### 2.2 Source-span traceability

Every `BrailleCell` carries the `source_span` it was produced from. That single field is what makes the output debuggable, lets the renderer wrap lines without losing provenance, and powers the proofreading system (§10): a tool can map any braille cell back to the exact source characters behind it.

Those coordinates originate in the frontend, as the spans an analyzer puts on its tokens, so that is where they are checked. A tokenizer or a reading engine is selected by name from an open registry, and its `Protocol` can only prove the adapter *has* an `analyze` or `resolve` method — never what comes back out of it. The spans are not merely carried, either: `tokens_to_inline` places a word-boundary blank at each token's `span.end`, the cross-kind boundary rules decide between a space and a connector by asking whether two runs are source-adjacent, and Japanese bunsetsu spacing does the same, so coordinates that overlap change the braille rather than only its provenance. Each of the three subsystem entry points (`tokenize`, `analyze`, `annotate`) therefore validates the result as it crosses into the library: container and element types, that a span is a `Span`, that it stays inside the analyzed text, that consecutive spans are ordered and non-overlapping, and — for a resolver — that the number, order, surfaces, spans and POS tags of the tokens it was handed are unchanged, plus that the two fields it *may* change (`pinyin`, `confidence`) hold what `ChineseToken` declares, a confidence being a probability in `[0, 1]` because a threshold is compared against it. What the ordering rules are applied to is the span each token **ends up with**: an adapter may omit spans, in which case coordinates are laid out from a running cursor, and checking only the explicit ones checks an order nothing downstream ever sees — a spanless token followed by a span pointing backwards passed a check on each half and produced overlapping provenance out of the pair. A violation raises `FrontendContractError`, the input-side counterpart of `BackendContractError` and drawn on the same line: a defect in an adapter's code is not a property of the user's document, so no run mode downgrades it. **One thing is deliberately not an error**: a surface that does not match the text its span points at. An analyzer that normalises its input legitimately produces one (both shipped cursor-recovery adapters do), which makes that token's coordinates approximate rather than the document untranslatable, so it is reported as a `TOKEN_SPAN_MISMATCH` warning.

<a id="arch-spans"></a>
Two coordinate systems share the work, one per level. A cell's `source_span` (like an inline node's `span`) is **leaf-local**: offsets into the owning leaf block's own `text`, starting at 0 per block — formats like `.docx` have no document-wide character coordinate at all, so leaf-local is the only system well-defined for every input. The block-level `Block.span` is what locates a block in its source document; whenever a block upholds the **exact-slice contract** `source[block.span] == block.text` (every plain-text block; Markdown headings, list items and single-line paragraphs — marker prefixes like `# ` / `- ` sit *outside* the span), `block.span.start + leaf_local` is the exact source position. Blocks whose `text` is derived rather than sliced (multi-line paragraphs joined with spaces, quotes with `> ` stripped, fence bodies) carry a line-range span: located, but without a per-character promise.

**The one exception: a table row is the leaf.** The backend flattens a whole `TableRow` into a single braille block, joining its cells with two blank cells, so the unit a consumer resolves against is the row, not the cell. A `TableCell`'s own `span` and every span inside it are therefore **row-local** — offsets into `"  ".join(cell.text for cell in row.cells)`, so `row_text[node.span]` slices the node's surface exactly. `FrontendDriver._populate_row` re-establishes that invariant on **every** populate pass (rather than shifting once when a cell is first filled), so re-compiling after an edit that changes one column's width still leaves the later columns pointing at the right characters. The price is that a table cell has no source-document coordinate at all — the Markdown adapter already rebuilds cell offsets from the de-syntaxed cell text instead of slicing the source row.

These two ideas — *isolate behind a mediator* and *keep provenance on every cell* — are the criteria the rest of the architecture is judged against.

---

<a id="arch-layers"></a>
## 3. The pipeline

The compiler is a stack of layers. The Profile and its resource tables sit alongside the whole stack, supplying the rules and dot tables that the backend and renderer read.

```
┌───────────────────────────────────────────────────────┐
│  Input Layer       many sources → one Document        │
├───────────────────────────────────────────────────────┤
│  Frontend Layer    text → structured IR               │
│  ├─ Segmenter      block / inline / special regions   │
│  ├─ Normalizer     tag numbers / dates / units / ...  │
│  ├─ ZhAnalyzer     Chinese segmentation + POS         │
│  ├─ PinyinResolver pinyin + polyphone disambiguation  │
│  ├─ JaAnalyzer     Japanese morphology + readings     │
│  ├─ MathParser     source → MathML tree (= IR)        │
│  ├─ MusicParser    source → MusicXML tree (= IR)      │
│  └─ GraphicsParser source → SVG tree (= IR)           │
├───────────────────────────────────────────────────────┤
│  IR Layer          DocumentIR / InlineIR /            │
│                    MathML / MusicXML / SVG /          │
│                    BrailleIR / TactileRaster          │
├───────────────────────────────────────────────────────┤
│  Backend Layer     semantic IR → output-domain IR     │
│  ├─ Dispatcher     dispatch by node type              │
│  ├─ ZhBraille      Chinese braille                    │
│  ├─ NumberBraille  numbers / dates / quantities       │
│  ├─ MathBraille    math braille (also a state machine)│
│  ├─ LatinBraille   English / foreign                  │
│  ├─ PunctBraille   punctuation                        │
│  └─ TactileBackend SVG tree → TactileRaster           │
├───────────────────────────────────────────────────────┤
│  Renderer Layer    output-domain IR → output format   │
│  ├─ braille        Unicode │ BRF │ Cells              │
│  ├─ tactile        BMP │ PNG │ PDF │ tactile preview  │
│  └─ Layout         line breaks / indent / pagination  │
└───────────────────────────────────────────────────────┘
          ↑                                    ↑
          └────────── Profile / Resources ─────┘
```

**The backend has two output domains, not one.** Text, math and music semantic IR compile
into a `BrailleDocument` (a cell sequence); a tactile graphic's SVG tree rasterizes into a
`TactileRaster` (a dot grid). Both are *output-domain IR* — the backend's product and the
renderer's input — and each is encoded into an external representation by the renderers that
understand it (bytes, a string, a list of cells, a JSON structure; the `Renderer` protocol's
return type is `Any` for that reason). Both
sets of renderers share one `renderer_registry`, each self-describing what it takes via
`consumes` — saying nothing means `"braille"`, a tactile renderer says
`"tactile_raster"`, and the result object compares the two before handing over its IR (a
mismatch is an `IncompatibleRendererError`). So braille is not the pipeline's
only terminus, just one of its output domains: a new product vertical is added by
introducing another output-domain IR plus the renderers that read it, never as a special
case outside the layers.

Each layer answers exactly one question:

| Layer | The one thing it decides |
|---|---|
| Frontend | what each piece of input *is* |
| IR | how that meaning is structured |
| Backend | how the rules write it (cells, or raised dots) |
| Renderer | what form it leaves in |

**The input/frontend boundary.** Input answers only "what blocks does this document have, and where is the raw content": it cracks open containers (the `.docx` OOXML and OLE, the `.mxl` ZIP), picks a parser by file identity (suffix or content sniff), and yields a `DocumentIR` of block structure with inline content still raw text. Frontend answers "what each inline region is": it translates a known source dialect (LaTeX, MathML, OMML, MTEF, MIDI, ...) into normalized IR (a MathML or MusicXML tree), picking an adapter by context and soft-failing to `<merror>` / `<music-error>`. Both parse source formats; the dividing line is **payload shape**, not timing:

1. A **text** dialect (OMML, Word EQ field, LaTeX, ABC) is kept raw in the input layer and deferred to the frontend — block-level as `MathBlock(source=...)`, inline as a source-tagged `$...$` island (`brailix.core.inline_math`) embedded in `Block.text`. Both are converted by the frontend's `parse_math_tree` (via `FrontendDriver.attach_math` / `populate_math_block`).
2. A **binary** dialect (MathType MTEF, MIDI, the `.mxl` ZIP) is decoded at the input boundary, because the text IR carries no binary payload. This is the deliberate exception to the rule, not an asymmetry.
3. **Self-synthesized MathML** (an `<msup>` / `<msub>` tree reconstructed from Word super/subscript formatting) is not a foreign dialect at all, so the input layer builds the tree directly.
4. A **reference** payload (a tactile graphic's `<image href>`): a bitmap or an external SVG file lives outside the document container — the fence body carries only a textual path / spec — so it stays a *reference* in the IR. The graphics frontend's image adapter reads only the file's dimensions (to set the `viewBox` and physical size); pixel decoding is deferred to the tactile backend at rasterize time (`backend/tactile/_image.py` resolves the href — data URI and filesystem path alike). A deliberate exception to rule 2: the binary never enters the IR and is not decoded at input, at the cost that a graphic IR is not self-contained (moving the referenced file makes a recompile soft-fail to a blank raster plus a `GRAPHICS_IMAGE_LOAD_FAILED` warning, never a crash), in exchange for sources and project files that don't balloon with embedded bitmaps.
   - A **document-embedded bitmap** (a picture inserted into a Word `.docx`) blends rules 2 and 4: the bytes *are* in the document container (`word/media/`), so they are extracted eagerly at the input boundary (rule 2) onto a document-level side table `DocumentIR.assets` (name → bytes; excluded from `to_dict`, which is the text-IR view); but the IR keeps only a *reference* (the asset name `media/imageN.png`, carried by an `ImageAlt` placeholder or, once converted, a `graphic-image` fence's `path`), which a caller-injected `GraphicAssetResolver` on the `GraphicsContext` (the same injection seam as `InlineTextTranslator`, §14) resolves back to bytes — inlined as a data URI — at compile time. The bytes never enter the source text (megabyte base64 lines would hurt screen-reader navigation and per-keystroke recompilation); a project file persists them as base64 instead, so an imported picture survives a save / reopen with no external file. Whether such a picture *becomes* a tactile graphic is the user's explicit, per-image decision (the `ImageAlt` placeholder otherwise emits just its alt text plus an `IMAGE_NOT_CONVERTED` warning).

So the input layer imports no math/music source registry from the frontend except from the explicitly allowlisted binary-container decoders — today `input/music_xml.py` (`.mxl` / MIDI → the music source registry) and `input/docx/_ole.py` (MathType MTEF inside a `.docx` → the math source registry). It is an allowlist rather than a single blessed site: a new binary container argues its way onto the list (`tests/test_core_layering.py`), and a reader meeting the second entry is not looking at a violation. The dependency is strictly one-way: the frontend never imports the input layer. The graphics fence likewise stays registry-free on the input side — a purely **structural** rule (bare ```` ```graphic ```` is the SVG alias; ```` ```graphic-<name> ```` carries `<name>` verbatim as the source name, the same shape as inline math's dialect-tagged islands), so a newly registered graphics source gets a fence tag with no input-layer change and an unknown name soft-fails at compile time (`GRAPHICS_ADAPTER_MISSING` plus a blank raster). Both directions of the fence grammar have one owner, `input/markdown.py` (`graphic_fence_source` / `graphic_fence_open`) — an editor re-tagging a fence never spells the tag itself.

A document flows top to bottom. The input layer turns any source into one `DocumentIR` whose blocks still hold raw text. The frontend detects inline regions, tags numbers, dates, and units, and routes each region down its own track. An IR builder merges everything into a complete `DocumentIR`, an IR validator checks structural validity, and the backend dispatches each node by type to a translator. The renderer then lays out and encodes the resulting cells, alongside a `WarningCollector`. Two properties of that flow matter most:

- **Each kind of content keeps its own track.** Chinese segmentation runs only on Chinese regions, and pinyin runs only on Chinese tokens, so `2026`, `x^2`, and `CPU` are never pushed through the Chinese path. Numbers, formulae, and English are protected back at the segmentation stage and reach the backend with their native structure intact.
- **Math and music parse on a dedicated path.** A formula is not part of the generic token stream; it is parsed into its own tree IR (§7, §8) and dispatched separately.

---

## 4. Directory structure

File names below follow what is actually in the repo.

```
brailix/
├── brailix/
│   ├── __init__.py
│   ├── pipeline/             # end-to-end entry package (__init__ is a pure facade: the eight names in __all__, nothing else)
│   │   ├── _pipeline.py      # the Pipeline orchestrator + module-level translate_graphic (translate_text / translate_document / translate_block / translate_document_to_pages / translate_math_inline)
│   │   ├── _results.py / _helpers.py      # result value types; standalone helpers such as block_hash
│   │   ├── _fingerprint.py / _session.py  # compilation-configuration digest; one run's session state
│   │   ├── _incremental.py / _pages.py    # block-level incremental compile; mixed-page composition
│   │   └── frontend_driver.py             # frontend driver (segment → normalize → route → populate)
│   ├── core/                 # shared types, contexts, errors, config loading, registries
│   │   ├── context.py        # FrontendContext / BackendContext / MathContext / MusicContext / GraphicsContext
│   │   ├── errors.py         # ParseError / WarningCollector / RunMode
│   │   ├── span.py           # Span utilities, source-position tracking for IR nodes
│   │   ├── segment.py        # Segment — the segmenter→normalizer mediator (named by a core protocol, so not in the frontend; not an IR node either)
│   │   ├── registry.py       # generic name→loader registry (lazy load + MissingExtraError)
│   │   ├── protocols.py      # Segmenter / Normalizer / LanguageFrontend / LanguageBackend / MathSourceAdapter / MusicSourceAdapter / GraphicSourceAdapter / InlineTextTranslator / GraphicAssetResolver / Renderer
│   │   ├── _xml.py           # shared XML helpers (safe_fromstring: parsing with entity expansion off; byte decoding by XML's own encoding rules; prologue scan to the root element)
│   │   ├── _zip.py           # ZIP member count, walked off the central directory (EOCD / ZIP64 only locate it) — one fact, read by docx and mxl before either opens the container
│   │   ├── chars.py          # irregular-character sets (one authority, consumed by the backend and by front-ends)
│   │   ├── paths.py          # a configured *name* is not a path (one validator, shared by every resource loader)
│   │   ├── measure.py        # positive-finite check for physical measurements (shared by the raster IR and the tactile profile; each keeps its own error type)
│   │   ├── inline_math.py    # inline-formula island codec (source tag + original text)
│   │   ├── dispatch.py
│   │   ├── config/           # profile loaders
│   │   │   ├── profile.py    # BrailleProfile
│   │   │   ├── validator.py / zh_ncb_tables.py
│   │   │   └── loader/       # letters / math / music / numbers / punct / zh / _refs
│   │   └── models/           # asset_registry / paths (frozen detection)
│   ├── input/                # document input adapters (dispatched by extension)
│   │   ├── plain.py / markdown.py   # markdown is a pure-stdlib reader (no extra)
│   │   ├── docx/             # .docx/.docm package (split by concern: blocks / properties / OLE / XML / embedded media; incl. OMML / MTEF / EqField math extraction)
│   │   └── music_xml.py      # .musicxml / .xml / .mxl direct; .mid/.midi eager (binary); .abc deferred (text)
│   ├── frontend/             # text → structured IR
│   │   ├── segmentation.py   # block segmentation + inline-region detection
│   │   ├── normalization.py  # tag numbers / dates
│   │   ├── zh/               # Chinese-specific (language folder)
│   │   │   ├── __init__.py        # umbrella: re-exports the analyzer's subsystem entry points
│   │   │   ├── analyzer/          # segmentation subsystem
│   │   │   │   ├── registry.py        # ChineseAnalyzer registry
│   │   │   │   └── adapters/         # auto / char / jieba / hanlp / thulac → ChineseToken
│   │   │   └── pinyin/            # pinyin + polyphone disambiguation (independent subsystem)
│   │   │       ├── registry.py        # PinyinResolver registry
│   │   │       └── adapters/         # auto / null / pypinyin / g2pm / g2pw
│   │   ├── ja/               # Japanese (language folder): kana/kanji segmenter + analyzer adapters (kana / janome / fugashi / sudachi) + 文節 spacing
│   │   ├── math/            # source → MathML tree (= IR)
│   │   │   ├── normalizer.py     # MathML normalization (emits ET.Element, i.e. the IR)
│   │   │   ├── registry.py        # math_source_registry
│   │   │   └── adapters/         # latex / mathml / omml / mtef / eq_field / chem / script_cluster / plain
│   │   ├── music/          # source → MusicXML tree (= IR)
│   │   │   ├── normalizer.py / registry.py  # music_source_registry
│   │   │   └── adapters/         # musicxml / mxl / midi / abc / plain
│   │   └── graphics/       # source → SVG tree (= IR, tactile graphics)
│   │       ├── normalizer.py / registry.py  # graphic_source_registry
│   │       ├── generate.py       # figure spec → primitives spec generators (pure stdlib)
│   │       ├── _numbers.py       # is this spec value drawable? (finite, in budget)
│   │       └── adapters/         # svg / primitives / figure / image (image needs the graphics extra)
│   ├── ir/
│   │   ├── document.py       # DocumentIR: block level (incl. MathBlock / CodeBlock / ScoreBlock ...)
│   │   ├── inline.py         # InlineIR: inline tokens (incl. MathInline.tree: ET.Element)
│   │   ├── braille.py        # BrailleIR: cell sequence
│   │   └── tactile.py        # TactileRaster: tactile dot grid (tactile-backend product, the graphics counterpart of BrailleIR)
│   ├── backend/              # semantic IR → output-domain IR (BrailleIR / TactileRaster)
│   │   ├── dispatch.py       # dispatch by node type; prose nodes then pick a LanguageBackend by profile.language
│   │   ├── number.py         # language-agnostic translator (numbers / dates)
│   │   ├── latin.py          # Latin backend (standalone, separate from punct)
│   │   ├── punct.py
│   │   ├── block.py          # heading/list/table block-level translation
│   │   ├── zh/               # Chinese-specific (language folder)
│   │   │   ├── __init__.py        # translate_word / translate_date_marker
│   │   │   ├── tone/              # tone policy (basic / ncb_omission)
│   │   │   └── pinyin_parser.py   # pinyin syllable → (initial, final, tone)
│   │   ├── ja/               # Japanese kana → cells (LanguageBackend)
│   │   ├── math/            # math braille state machine (chem / context / dispatch / handlers / utils)
│   │   ├── music/          # music braille (handlers/ split into files by BANA chapter)
│   │   └── tactile/        # SVG tree → TactileRaster (tactile rasterizer; page.py mixed-page compositor + profile.py TactileProfile)
│   ├── renderer/            # output-domain IR → output format
│   │   ├── unicode_braille.py / brf.py / cells.py
│   │   ├── layout.py        # line breaks / indent / pagination
│   │   ├── music_layout.py / _page_digits.py
│   │   └── bmp.py / png.py / pdf.py / tactile_preview.py  # tactile renderers (consume TactileRaster; same renderer_registry, self-described via ``consumes``)
│   ├── profiles/
│   │   ├── cn_current.json   # Current Chinese Braille (no built-in default: the caller names one)
│   │   ├── cn_ncb.json       # National Common Braille
│   │   └── ja_current.json   # Japanese kana braille
│   └── resources/            # braille tables: shared ones at the top, region/scheme-specific under <region>/<scheme>/
│       ├── cells.json        # globally named cell pool (shared)
│       ├── numbers.json      # numbers: number sign + a–j (shared, used worldwide)
│       ├── latin/ / greek/   # neutral alphabets (shared, scheme/language-agnostic)
│       ├── phonetic.json     # English IPA phonetic symbols → cells (shared, English-Braille letter/digraph values, scheme-agnostic)
│       ├── music/            # music resources (BANA 2015 tables + instruments/ + vocal/, international)
│       ├── tactile/          # tactile profiles (generic / letter: millimetre adaptation params + the one DPI dial)
│       ├── cn/               # Chinese braille resources
│       │   ├── compounds.json # letter+hanzi compound-word lexicon (a Chinese-language fact, scheme-agnostic)
│       │   ├── current/      # Current Chinese Braille: initials / finals / tones / punct + math/
│       │   └── ncb/          # National Common Braille: an exceptions overlay (everything else inherits current)
│       └── ja/               # Japanese braille resources (kana tables under current/)
├── tests/                   # backend / core / frontend / golden / integration / ir / ...
├── pyproject.toml
└── ARCHITECTURE.md
```

---

## 5. The intermediate representations

Five IRs in two groups. Three describe the document, coarse to fine: block-level `DocumentIR`, inline `InlineIR`, and the normalized tree each of the math / music / graphics verticals uses directly as its own IR. Two are **output-domain IR** — the backend's product, one per output path: `BrailleIR` (a cell sequence) for braille and `TactileRaster` (a dot grid, §8) for tactile graphics. The two output domains are peers, not braille plus an appendix.

### 5.1 DocumentIR (block level)

```json
{
  "version": "2.0",
  "type": "document",
  "metadata": {"language": "zh-CN", "profile": "cn_current"},
  "blocks": [
    {"id": "b1", "type": "heading", "level": 1, "inlines": [...]},
    {"id": "b2", "type": "paragraph", "inlines": [...]},
    {"id": "b3", "type": "list", "ordered": false, "blocks": [...]}
  ]
}
```

Block types: `heading / paragraph / list / list_item / table / table_row / table_cell / quote / footnote / code_block / math_block / score / music_block / image_alt / graphic`. (`score` is a whole score, `ScoreBlock`; `music_block` a single passage, `MusicBlock` — both share `EmbeddedBlock` with `MathBlock` and `GraphicBlock`, holding their normalized tree on the block's own `tree`. `graphic` is a tactile graphic, `GraphicBlock`, whose source is SVG, a primitives spec or a figure spec; it is the one block that does not translate to braille cells, rasterizing instead into a `TactileRaster` — see §5.3 and §8.)

**A block has two kinds of content, one field each.** `inlines` are the typed tokens a leaf block's text became; `blocks` are nested blocks — a list's items, a table's rows, a row's cells. Those used to be `children` (inline-only, despite the name) plus a per-class structural field (`List.items`, `Table.rows`, `TableRow.cells`), which made the document two trees wearing one name: everything that walked it had to know which of four field names a given block kept its children under — the serializer, the deserializer, `structure_key`, the frontend driver, the block backend, the surface reconstruction, the front-end's leaf collector. One name for one relationship deletes all of that. *Which* class the nested blocks must be is the owning class's `child_type` (`List` → `ListItem`, `Table` → `TableRow`, `TableRow` → `TableCell`; `None` everywhere else, meaning "holds no nested blocks").

### 5.2 InlineIR (inline tokens)

```json
{
  "type": "word",
  "surface": "重庆",
  "reading": "chong2 qing4",
  "span": [15, 17]
}
```

Inline token types:

```
word / number / date /
punct / latin_word /
code_inline / phonetic_inline / math_inline /
space / connector / unknown
```

> What a composite node holds inside is **value objects, not nodes**: a `date` is a list of `DateComponent` records (`digits` + `marker`, each with its own span, plus the marker's reading). The year / month / day markers used to be a public `hanzi_marker` node type, paying for a registry entry, a wire tag, a facade export and a recursive typed-child check on `parts` — while there is no such thing as a loose 年 and nothing dispatches on one. Only something a consumer dispatches on independently earns nodehood.

> A single character is simply a one-character `word` — there is no separate node type for it (both language backends translated it through an identical call, and every consumer discriminated on it alongside `word` anyway); `unknown` keeps the pipeline running on anything else.

> `phonetic_inline` is an English IPA transcription: a `/.../` or `[...]` region in prose whose content carries an IPA-distinct character is recognised by the segmenter as a protected region (same mechanism as `$...$`; math wins any conflict). The node holds only the phoneme run with its delimiters stripped, and `backend/phonetic` greedily longest-matches each phoneme against the profile's phonetic table (two-character phonemes like `tʃ` / `eɪ` beat their single-character prefixes), flagging a symbol the table doesn't define (a stress mark) with `PHONETIC_UNKNOWN_SYMBOL` rather than inventing braille.

### 5.3 Math, music and graphics as tree IRs

A math formula uses its **normalized MathML tree** as its IR directly, a score uses its **normalized MusicXML tree** the same way, and a tactile graphic its **normalized SVG tree**. In all three the mediator format (§2.1) *is* the IR, and the backend dispatches by element tag. Where graphics differs is only the product: math and music compile to braille cells, an SVG tree rasterizes into a `TactileRaster` (§8). The math tree looks like:

```xml
<math>
  <mfrac>
    <mrow>
      <mi>x</mi><mo>+</mo><mn>1</mn>
    </mrow>
    <msup>
      <mi>y</mi><mn>2</mn>
    </msup>
  </mfrac>
</math>
```

A block whose content is one of these trees holds it directly, on `EmbeddedBlock.tree` — the shared base of `math_block`, `score` / `music_block` and `graphic`, which is also what lets the backend route all three through one branch keyed on the block's `domain`. Each tree used to hang off an inline *carrier* node instead (`math_inline`, `music_inline`, `graphic_inline`), sitting alone in the block's `children`; two of those three had no other producer, and the graphics one had no braille to give at all, so the block backend carried a special case telling it not to translate that child. Only `math_inline` survives, for the case that earns it: a formula inside a sentence. The full math, music and tactile-graphics subsystems are described in §7 and §8.

<a id="arch-braille-ir"></a>
### 5.4 BrailleIR (cell sequence)

```python
@dataclass(slots=True, frozen=True)
class BrailleCell:
    dots: tuple[int, ...] = ()  # e.g. (1, 2, 4); normalised to ascending order in __post_init__
    role: str | None = None     # 'number_sign' / 'zh_initial' / 'math_op' ...
    source_span: Span | None = None  # serialised as [start, end]
    source_text: str | None = None
```

```json
{
  "type": "braille_document",
  "blocks": [
    {"type": "braille_block", "block_type": "paragraph", "cells": [
      {"role": "zh_initial", "source_text": "我", "dots": [/*...*/]},
      {"role": "number_sign", "dots": [3, 4, 5, 6]},
      {"role": "number",      "source_text": "2026", "dots": [/*...*/]}
    ]}
  ]
}
```

What BrailleIR buys you: easy debugging, traceability, line-wrapping, BRF generation, and proofreading. (The unicode character is not stored on a cell — it is derived from `dots` and computed by the renderer; see the renderer's role in §1.)

---

<a id="arch-adapters"></a>
## 6. Adapters: protocols, registries, and dependency groups

§2.1 stated the pattern; this section is its machinery. The library core ships with **zero third-party parsing dependencies** — every concrete parser is an adapter behind an optional extra.

### 6.1 Protocol definitions

```python
# core/protocols.py

class Segmenter(Protocol):
    name: str
    def segment(self, block: Block, ctx: FrontendContext | None) -> list[Segment]: ...

class MathSourceAdapter(Protocol):
    source: str  # latex / omml / mathml / chem / ...
    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str: ...

class MusicSourceAdapter(Protocol):
    source: str  # musicxml / mxl / midi / abc / plain
    def to_musicxml(self, src: str | bytes, ctx: MusicContext) -> str: ...

class GraphicSourceAdapter(Protocol):
    source: str  # svg / primitives / figure / image / ...
    def to_svg(self, src: str | bytes, ctx: GraphicsContext | None = None) -> str: ...

class Normalizer(Protocol):
    def normalize(self, nodes: list[InlineNode], ctx: FrontendContext) -> list[InlineNode]: ...

class LanguageFrontend(Protocol):        # one language's prose → inline IR
    prose_types: Collection[str]
    def process(self, surface: str, base: int, ctx: FrontendContext) -> list[InlineNode]: ...

class LanguageBackend(Protocol):  # prose nodes (Word / HanziMarker) → cells, per language
    def translate_word(self, node: Word, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]: ...
    # Both are required — the registry runs a runtime protocol check on
    # first resolution, so an implementation missing one is rejected at get().
    def translate_date_marker(self, marker: HanziMarker, follows_number: bool, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]: ...

class Renderer(Protocol):
    name: str
    def render(self, bir: BrailleRenderable) -> Any: ...  # str / bytes / cells / ...
```

**Per-language contracts are not here.** A language's analyzer / reading-resolver protocol lives in that language's own package (`ChineseAnalyzer` in `frontend/zh/analyzer`, `PinyinResolver` in `frontend/zh/pinyin`, `JapaneseAnalyzer` in `frontend/ja/analyzer`), as does the intermediate type they pass between them (`ChineseToken` in `frontend/zh/tokens.py`). A protocol whose signature names one language's types is that language's contract, not the shared layer's; keeping it on the shared layer made each language's seam a different shape, so an adapter author had to learn a different import path per language.

```python
# frontend/zh/analyzer/__init__.py
class ChineseAnalyzer(Protocol):
    name: str
    def analyze(self, text: str, ctx: FrontendContext | None) -> list[ChineseToken]: ...

# frontend/zh/pinyin/__init__.py
class PinyinResolver(Protocol):
    name: str
    def resolve(self, tokens: list[ChineseToken], ctx: FrontendContext | None) -> list[ChineseToken]: ...
```

(Two further protocols support the backend rather than extend a source family: `InlineTextTranslator`, the one controlled backend→frontend dependency, injected through `BackendContext.options` to translate embedded prose (§14); and `GraphicAssetResolver`, which resolves a graphic's asset reference to in-document bytes. `core/protocols.py` is the authoritative list, and the extension-surface manifest in the test suite keeps the two in step.)

There is deliberately **no `Backend` protocol**. The backend isn't a pluggable-by-name adapter; it's a node-type dispatcher (§9.1), so it has no registry and no name→implementation contract. A new braille standard is added with a Profile JSON plus resources, not by registering a backend. Per-language *prose* translation is the one pluggable seam, and it goes through `LanguageBackend` above (§12).

<a id="arch-registries"></a>
### 6.2 Registries and on-demand loading

Each subsystem keeps a name→implementation registry, and **an adapter is imported only when it is first requested**, so a user who hasn't installed HanLP can still run a jieba-only path.

> **Exception: the input layer's format dispatch keeps no core registry.** Every other subsystem has its implementation chosen *by name from the profile* (`zh_analyzer: "hanlp"`), so the registry naturally lives in core. But *which adapter parses a given file* is decided by the file itself (extension / content), not a profile option — so core `brailix.input` ships the `parse_*` adapter functions plus a `parse_file` data table mapping a suffix set to a handler (adding a format is one more row), and the format-dispatch layer keeps no name→implementation registry. Which formats an application offers — file-dialog filters, fallback rules, third-party adapter discovery — is an application concern, wired through a registry the application builds over these functions. On-demand loading is achieved with in-function imports (`parse_docx` does `import docx` only when called). (Where input genuinely has competing implementations — handing `.mxl` / `.mid` to a music source adapter — it still uses `music_source_registry`, exactly as the registry pattern prescribes; `.abc` is a text dialect, kept raw and deferred to the frontend per §1 rule 1, so it is not decoded on the input side. Like the backend's node-type dispatcher in §6.1, the format seam is a deliberate non-registry choice.)

```python
# frontend/zh/analyzer/registry.py
analyzer_registry: Registry[ChineseAnalyzer] = Registry(
    "zh.analyzer", protocol=ChineseAnalyzer
)

# frontend/zh/analyzer/adapters/hanlp.py
def _load() -> ChineseAnalyzer:
    import hanlp  # imported only when actually used
    ...
analyzer_registry.register("hanlp", _load, extra="hanlp")
```

The generic `Registry` class in `core/registry.py` provides the machinery: `get(name)` runs the loader on first resolution and caches the instance under the name, validates the implementation against `protocol`, and raises `MissingExtraError` (naming the `pip install brailix[...]` extra) when an optional dependency is absent — under a lock, so concurrent threads resolve a name once. It also keeps a `generation` counter, advanced by every `register` / `unregister` / `clear_cache`: `Pipeline.fingerprint` folds in the generation of every compile-relevant registry, so swapping an implementation at runtime moves the fingerprint of every live Pipeline — and with it every `CompiledBlock.source_hash` — so cached output from the replaced implementation can no longer be served under the same key.

The profile names the implementation by string; the registry resolves it:

```json
{
  "frontend": {
    "zh_analyzer": "hanlp",
    "pinyin": "g2pw"
  },
  "math": {
    "adapters": {"latex": "latex2mathml", "omml": "pandoc"}
  }
}
```

### 6.3 Dependency groups (pyproject extras)

Every adapter rides on an optional extra:

```toml
[project.optional-dependencies]
zh     = ["jieba", "pypinyin"]                 # light, offline Chinese (good default)
hanlp  = ["hanlp", "transformers<4.55"]        # transformer tokenizer (downloads a model)
thulac = ["thulac"]
g2pw   = ["g2pw", "torch"]                     # deep polyphone model (downloads a model)
g2pm   = ["g2pM", "numpy"]
latex  = ["latex2mathml"]                      # LaTeX → MathML
docx   = ["python-docx", "lxml", "olefile"]   # Word .docx / .docm (incl. OMML / MathType)
midi   = ["mido", "partitura"]                 # MIDI scores → MusicXML
abc    = ["abc-xml-converter"]                 # ABC scores → MusicXML
graphics = ["pillow"]                          # tactile graphics: read an external bitmap
graphics-svg-raster = ["resvg-py", "pillow"]   # tactile graphics: full-fidelity external-SVG render
ja     = ["janome"]                            # light, offline Japanese
all    = [...]                                 # every tool + each language's default analyzer
```

```bash
pip install brailix[zh]                 # light, offline Chinese
pip install brailix[zh,latex]           # + LaTeX math
pip install brailix[hanlp,g2pw]         # accurate Chinese engines (download models)
```

Two of those extras pin a dependency their own package failed to declare, which is worth spelling out because it looks arbitrary otherwise. HanLP 2.1.3 puts no upper bound on `transformers`, but transformers 5.0 removed an interface it still calls, so `transformers<4.55` rides along with it; g2pw 0.1.1 imports `torch.utils.data` at import time while declaring only onnxruntime / tqdm / transformers, so `torch` rides along with that. `pyproject.toml` is the authoritative list and records the conditions for dropping each.

If an adapter's package is missing at runtime, the registry raises a clear **`MissingExtraError`** that names the extra to install. (The MathML and MusicXML readers use the stdlib `xml.etree`, so the math and music subsystems themselves need no extra — only the source adapters that wrap a third-party converter do.)

### 6.4 What ships today

The first batch of adapters in the box — the profile always selects which one runs:

| Subsystem | adapters shipped | recommended to start |
|---|---|---|
| Chinese segmentation | `char` / `jieba` / `thulac` / `hanlp` (plus `auto`) | `jieba` (light) or `hanlp` (accuracy) |
| Pinyin | `null` / `pypinyin` / `g2pm` / `g2pw` (plus `auto`) | `pypinyin` (light) or `g2pw` (deep polyphone model) |
| Japanese analysis | `kana` (no extra) / `janome` / `fugashi` / `sudachi` (plus `auto`) | `janome` (light) |
| Math sources | `mathml` (stdlib passthrough) / `latex` (`latex2mathml`) / `omml` / `mtef` / `eq_field` / `chem` | LaTeX + MathML; OMML / MTEF / EqField land with Word |
| Music sources | `musicxml` (stdlib) / `mxl` (zip unpack) / `midi` (`partitura`) / `abc` (`abc-xml-converter`) / `plain` | MusicXML and `.mxl` |
| Graphic sources | `svg` (stdlib tag-walk) / `primitives` / `figure` (both stdlib) / `image` (`pillow`; full external-SVG render adds `resvg-py`) | SVG and primitives |
| Document input | plain text / Markdown (pure-stdlib reader) / Word `.docx` / `.doc` (`python-docx` + `olefile`) / score files | enable per scenario |

<a id="arch-open-closed"></a>
### 6.5 Adding a tool is one file

Adding any external tool means writing one adapter file: a new tokenizer goes under `frontend/zh/analyzer/adapters/`, a new pinyin engine under `frontend/zh/pinyin/adapters/`, a new math source under `frontend/math/adapters/`, a new language's braille rules become a `LanguageBackend` module under `backend/` plus a profile (a new *standard* for an existing language is just a profile + resources, no code — see §9.3), and a new output format becomes a module under `renderer/`.

To be precise about where that promise holds: for a new tool behind an **existing protocol** — the cases listed above — no core code changes; the adapter file registers itself and a profile (or an explicit option) selects it. Two kinds of extension *do* grow the core, by design: a **new IR node type** must join the backend dispatch table (and its serialization and tests), and a **new domain vertical** — a fifth mediator alongside text, math, music, and graphics — touches orchestration the same way. Adding a whole new *language* sits in between: it is registration-only (no orchestrator branches), but it does mean registering several implementations plus a profile — §12 walks through exactly what it takes.

Put plainly: **the adapter layer is the open extension surface (Registry + Protocol, plug-and-play), while the set of IR node and block types is a repo-internal closed world.** Adding an `InlineNode` or `Block` is a coordinated change across several points (dataclass + registry + serialization + backend dispatch + schema + tests) — a deliberate closed set, not a plugin seam. Serialization is the one of those that is declarative: a block's nested blocks all live in `blocks`, and which class their entries must be is the class's own `child_type` — that single declaration is what writes the field out *and* what rebuilds it on the way back, with both directions checking every entry against it, so a tree that serializes is a tree that reloads. Nested blocks put anywhere else make `to_dict` raise rather than skip them, which is what it used to do — saving succeeded, the JSON was valid, and the field was gone after a reload. The normal move is to fold a new domain into one of the existing mediators (text, math, music, or graphics) and carry it on the nodes already there.

---

## 7. The math subsystem

Math is the part of the project most likely to break and the biggest long-term extensibility risk: it will eventually need many sources and targets — Word, EPUB, LaTeX, HTML, MathJax output, and so on. So it is the fullest expression of the §2.1 pattern: every source is routed through a single mediator, **MathML**, by adapters that reuse existing tools.

### 7.1 MathML as both the mediator and the IR

Treat MathML as the unified mediator for every math source format. The normalized MathML tree (`xml.etree.ElementTree.Element`) *is* the math subsystem's IR — the backend dispatches directly by element tag. LaTeX, OMML (Word), ASCIIMath, MathJax, and plain Unicode text each have an off-the-shelf converter to a MathML string; that string is parsed into an `ET.Element` tree and handed to the MathBraille backend.

Why MathML:

- It is a W3C standard — the lingua franca between Word, LibreOffice, EPUB3, MathJax, and KaTeX.
- LaTeX → MathML has `latex2mathml`, `pylatexenc`, MathJax-node, and others.
- Word's OMML → MathML has the XSL transform that ships with OOXML, and pandoc.
- MathML inside HTML/EPUB can be parsed directly with `lxml`.
- A new source format later means **one more → MathML adapter**, and nothing downstream changes.

### 7.2 Three stages

1. A `MathSourceAdapter`, chosen by source, converts the raw formula (from any source) into a standard MathML string.
2. The `MathMLNormalizer` strips namespaces, collapses single-child `mrow`s, trims whitespace, and wraps errors in `<merror>`, emitting the normalized `ET.Element` tree — this is the IR.
3. The MathBraille backend walks that tree, dispatching by element tag.

### 7.3 The MathSourceAdapter interface

```python
class MathSourceAdapter(Protocol):
    source: str  # "latex" / "omml" / "asciimath" / "mathml" / ...

    def to_mathml(self, formula: str | bytes, ctx: MathContext) -> str:
        """Convert math from any source into a standard MathML string."""
```

Default implementations:

| source | shipped adapter | notes |
|---|---|---|
| `mathml` | straight through `xml.etree.ElementTree` | stdlib; `lxml` is an alternative |
| `latex` | `latex2mathml` (the `latex` extra) | `pylatexenc` / `mathjax-node` are possible alternatives |
| `omml` | built-in OOXML `<m:oMath>` → MathML converter | Word formulae; rides the `docx` extra |
| `mtef` / `eq_field` | built-in MathType / Equation 3.0 extractors → MathML | legacy Word equation objects |
| `chem` | built-in `\ce{...}` → MathML | chemical equations |
| `plain` / `unicode` | a minimal heuristic → MathML | simple structures only (fallback) |

Each adapter does exactly one thing — **emit valid MathML**; on error it returns `<merror>` and adds a warning.

### 7.4 MathContext

```python
@dataclass
class MathContext:
    mode: Literal["inline", "display"]
    source: str               # latex / omml / mathml / asciimath / plain
    profile: str
    surrounding_text: tuple[str, str] | None = None  # (before, after)
```

The context carries only what the tree does not: the mode, the source, the profile, and the surrounding text (which the backend sometimes needs). Structure itself lives entirely in the MathML tree.

### 7.5 Key rules

- **The MathSourceAdapter emits only a MathML string.**
- **The `ET.Element` the MathMLNormalizer emits *is* the IR** — the backend consumes the tree directly.
- **A parse failure stays in-band.** The adapter returns MathML containing `<merror>`, the normalizer passes it through, and the backend (in `_emit_merror`) emits a `MATH_ERROR` warning plus an unknown cell, and continues.
- **The backend runs a contextual state machine.** As MathBraille walks the tree, `MathBrailleContext` controls when to emit a superscript indicator, when to reset `need_number_sign`, and when to add a separator — braille output rules are inherently context-dependent.

Two finer invariants keep the layers clean: the MathML tree stays pure structure (dots and profile keys live in the backend and profile), and the profile JSON stays a data table (rules live in code). The math backend works from the normalized tree alone.

---

## 8. The music and tactile-graphics subsystems

The music path mirrors the math path exactly. A source — MusicXML, a compressed `.mxl`, MIDI, or ABC — goes through an adapter into a normalized **MusicXML tree** (`ET.Element`), which is the music IR. The MusicBraille backend dispatches by element tag and runs a contextual state machine implementing BANA 2015 braille music. The code lives in the frontend `frontend/music/`, the backend `backend/music/` (whose `handlers/` subpackage is split into files by BANA chapter), the resources `resources/music/`, and the input adapter `input/music_xml.py`. Because it reuses the same adapter-plus-mediator shape, adding a new score format is, again, one adapter file.

The tactile-graphics path reuses the same shape with a different product. A source — raw SVG, a primitives spec, a figure spec, or an external image reference — goes through an adapter into a normalized **SVG tree** (`ET.Element`), which is the graphics IR. The tactile backend (`backend/tactile/`) dispatches by element tag and rasterizes the tree into a `TactileRaster` (`ir/tactile.py`) — a grid of raise levels, the graphics counterpart of BrailleIR — driven by a `TactileProfile` (millimetre adaptation parameters plus one device dial, DPI; JSON under `resources/tactile/`). A graphic never becomes braille cells; its `<text>` labels are translated through an injected `LabelTranslator` callable (the same dependency-injection seam as `InlineTextTranslator`, §14) and stamped as physically-sized braille dots. The rasters render to `.bmp` / `.png` / `.pdf` / a U+2800 preview through the **same** `renderer_registry` as the braille renderers — each renderer self-describes what it consumes. The entry point is the **module-level** `brailix.translate_graphic`: a graphic's compile needs no braille standard (its product is a raster, not cells; only `<text>` label translation touches braille), so it stands Pipeline-free, and `Pipeline.translate_graphic` merely delegates, reusing its own text path for labels when the standards match; `Pipeline.translate_document_to_pages` composes mixed pages. External `<image href>` assets resolve at rasterize time (§3, payload rule 4).

---

## 9. The backend

<a id="arch-dispatch"></a>
### 9.1 Dispatcher

```python
class BrailleBackend:
    def translate(self, node: IRNode, ctx: BackendContext) -> list[BrailleCell]:
        match node.type:
            case "word":        return self.zh.translate_word(node, ctx)
            case "number":      return self.number.translate(node, ctx)
            case "date":        return self.number.translate_date(node, ctx)
            case "math_inline": return self.math.translate(node, ctx)
            case "latin_word":  return self.latin.translate(node, ctx)
            case "punct":       return self.punct.translate(node, ctx)
            case _:             return self.fallback(node, ctx)
```

> Prose nodes (`word`) are translated by the `LanguageBackend` for the profile's language — the `self.zh` above is just a Chinese stand-in, and the real dispatch picks an implementation by `profile.language` (see §12). All other nodes go through the shared dispatch table by type.

### 9.2 BackendContext

It carries only the profile name, the run mode, the current block type, the shared warning collector, and an options bag. Context-dependent **braille state** — the number-sign latch, math nesting depth and the rest — deliberately does *not* live here; each subsystem's own state machine holds it (`MathBrailleContext.need_number_sign`, `MusicBrailleContext`). A single shared bag of those flags was never read by the dispatcher and only invited writes that were silently ignored, so it was removed. The inline-text translator is injected through `options` (the controlled exception in §14).

`profile` is **required**: the library has no built-in default braille standard, so the caller (normally `Pipeline`) always passes the chosen one, and the profile's own `language` decides the language — there is no `zh-CN` fallback. `MathContext` / `MusicContext` make `profile` a required keyword-only argument for the same reason, field order being the only difference.

```python
@dataclass(slots=True)
class BackendContext:
    profile: str                       # required: no built-in default standard
    mode: RunMode | str = RunMode.NORMAL
    block_type: str = "paragraph"      # paragraph / heading / table_cell ...
    warnings: WarningCollector = field(default_factory=WarningCollector)
    options: dict[str, Any] = field(default_factory=dict)
```

### 9.3 Profile

A different standard = a different profile; the library itself stays scheme-agnostic.

```json
{
  "name": "cn_current",
  "language": "zh-CN",
  "cell": "six_dot",
  "features": {
    "math": {
      "simplify_fraction": true,
      "simplify_script": true,
      "op_spacing": true
    },
    "zh": {
      "tone": true,
      "tone_omit_neutral": true,
      "number_sign": true
    }
  },
  "tables": {
    "cells":  "resources/cells.json",
    "latin":  "resources/latin/letters.json",
    "greek":  "resources/greek/letters.json",
    "zh": {
      "initials":    "resources/cn/current/initials.json",
      "finals":      "resources/cn/current/finals.json",
      "tones":       "resources/cn/current/tones.json",
      "punctuation": "resources/cn/current/punct.json",
      "numbers":     "resources/numbers.json"
    },
    "math": {
      "symbols":      "resources/cn/current/math/symbols.json",
      "functions":    "resources/cn/current/math/functions.json",
      "structures":   "resources/cn/current/math/structures.json",
      "digits_lower": "resources/cn/current/math/digits_lower.json"
    }
  }
}
```

---

## 10. Error recovery and proofreading

### 10.1 Three run modes

- `strict` — raise on any unrecognized structure (for textbook publishing).
- `normal` — recover as much as possible and emit warnings (the default).
- `lenient` — emit as much as possible, falling back to unknown tokens (for experiments / trial translation).

### 10.2 Warning format

```json
{
  "code": "LOW_CONFIDENCE_PINYIN",
  "level": "warn",
  "message": "polyphone reading has low confidence",
  "surface": "单于",
  "candidates": ["chan2 yu2", "dan1 yu2"],
  "span": [20, 22]
}
```

Common codes (only names the core actually emits are listed here — consumers key quickfixes and i18n entries off the code, and the test suite guards both directions against drift): `LOW_CONFIDENCE_PINYIN / MISSING_PINYIN / UNKNOWN_PUNCT / MATH_UNKNOWN_SYMBOL / MUSIC_UNSUPPORTED_NOTATION`.

Inputs with no usable text span carry **structural provenance** in `anchor` — domain-defined string key/value pairs, a public ABI (the authoritative definition is the `Warning.anchor` field comment in `brailix/core/errors.py`). Music-backend handlers always warn through `MusicBrailleContext.warn`, which fills `{"part_id": ..., "measure_number": ...}` — the same labels every braille cell's `source_text` provenance tags (`[p=,m=]`) carry: normalized MusicXML elements have no source offsets, so `span` cannot serve in a score, and a frontend (the warning panel's "locate the score measure" jump) navigates by `anchor` instead. Outside a part / measure both keys are absent and `anchor` is omitted entirely, which downstream reads as "score level, no narrower location".

```json
{
  "code": "MUSIC_UNSUPPORTED_NOTATION",
  "level": "warn",
  "message": "unsupported clef sign 'TAB'",
  "anchor": {"part_id": "P1", "measure_number": "12"}
}
```

### 10.3 Proofreading friendliness

Because every BrailleCell carries a `source_span`, the system can emit a **proofreading JSON**:

```json
{
  "text":       "我在2026年5月17日去了重庆银行。",
  "ir":         { "...": "DocumentIR.to_dict()" },
  "braille_ir": { "...": "BrailleDocument.to_dict(): every cell carries source_span + source_text" },
  "warnings":   ["..."]
}
```

`proofread_json()` returns exactly these keys — no output is pre-rendered, since each braille cell already carries the `source_span` / `source_text` a front-end needs. A tool (an HTML preview) can use this to highlight, click-to-correct, and batch-edit pinyin, and render any output format on demand.

---

## 11. The Pipeline API

```python
from brailix import Pipeline

pipe = Pipeline(profile="cn_current", mode="normal")

result = pipe.translate_text(
    "我在2026年5月17日去了重庆银行，计算 $x^2 + y^2 = z^2$。"
)

result.render()           # str: ⠁⠃⠉... (unicode by default)
result.render("unicode")  # explicitly choose the renderer
result.ir                 # DocumentIR
result.braille_ir         # BrailleDocument
result.warnings           # WarningCollector
result.proofread_json()   # JSON proofreading structure (incl. IR, warnings)
```

A CLI landed 2026-06-07 — a thin shell over `Pipeline` plus the renderer registry, invoked as `brailix` or `python -m brailix`. It is built on `argparse` with option `choices` enumerated dynamically from the registries (`--list-profiles` / `--list-analyzers` / `--list-resolvers` / `--list-renderers` print and exit), takes the text as an argument, from `--file` (dispatched by suffix), or from piped stdin (UTF-8) — exactly one of the three, with stdin as the fallback when neither of the others is given, and a positional string together with `--file` (or `--in-format` together with `--file`) refused as a usage error rather than resolved by an implicit precedence — and follows an exit-code contract (`0` success / `1` translation error / `2` bad invocation):

```bash
brailix "我在重庆。" --profile cn_current
brailix --file input.md --profile cn_current --to brf --output out.brf
echo "文本" | brailix --profile cn_current --to unicode
```

<a id="arch-pipeline-api"></a>
### 11.1 What the Pipeline does

The Pipeline's public entry points group by what you want back, and all of them work under one configuration (profile, adapter selection, run mode).

**Compile to braille**

- `Pipeline.translate_text(text)` wraps the input in a single `Paragraph` block — the simplest entry point.
- `Pipeline.translate_document(doc)` accepts a full `DocumentIR` and runs frontend + backend block by block. Combined with `brailix.input.parse_markdown(text)` it can consume Markdown text directly.
- `Pipeline.translate_file(path)` dispatches an input adapter by suffix, then runs `translate_document`.
- `Pipeline.translate_block(block, *, ir_transformer=None, tree_subcache=None)` is the **incremental compilation primitive**: it re-compiles one block and returns a `CompiledBlock` carrying the cache-keying `source_hash` and the parsed-tree reuse pool. This is what a proofreading front-end builds on.
- `Pipeline.translate_math_inline(surface, source)` previews one formula straight to a braille string, discarding diagnostics (the documented preview contract).

**Compile to tactile output**

- `Pipeline.translate_graphic(...)`, and the module-level `brailix.translate_graphic(...)`, rasterize one figure into a `TactileRaster`.
- `Pipeline.translate_document_to_pages(doc)` composes a document with embedded figures into tactile page rasters (braille text stamped as real dots, figures scaled into the same page).

**Parse without translating**

- `Pipeline.parse_text(text)` / `Pipeline.parse_file(path)` return an **unpopulated** `DocumentIR`: the blocks carry their raw `text`, and `children` are filled once `translate_document` / `translate_block` runs the frontend over them. That is what they are for — a caller doing incremental compilation wants the structure *before* the frontend runs, to compile block by block (and so does a proofreading tree or a structural check).

Rendering stays **deferred** throughout: the compiling entry points return a result object, and concrete formats come from `result.render(name)` on demand.

When the Pipeline processes a multi-block document it follows these rules:

- The `text` of `Heading` / `Paragraph` / `Quote` / `Footnote` / `ImageAlt` / `ListItem` / `TableCell` goes through the language frontend, producing `children` (inline nodes such as Word / Space / Number / ...).
- The `text` of `MathBlock` / `CodeBlock` takes a dedicated path — the Pipeline **pre-fills** their `children` during block population (`FrontendDriver.populate_block` dispatching to the per-kind handler in `_populate`). A `MathBlock` goes through the **math frontend** (`brailix.frontend.parse_math_tree`) to parse LaTeX/MathML and produce **one** `MathInline` holding the normalized MathML tree; on parse failure it raises a `MATH_BLOCK_PARSE_FAILED` warning and fills per-character `Unknown` nodes to preserve the layout placeholder. A `CodeBlock` wraps its `text` in **one** `CodeInline`, which the punct backend emits cell by cell. The point: the backend only ever sees a block whose `children` are already filled, and it consumes the IR forward-only.
- At render time `renderer/layout` decides indentation and blank lines by `block_type`; level-1 headings are centered, deeper headings are left-aligned, and `code_block` / `table_row` / `table` are emitted verbatim.
- `translate_document` compiles the `DocumentIR` **in place** — this is a deliberate contract, not an implementation detail. The caller owns the source-side fields (`text`, block types, document structure); the compiler owns the derived fields it fills in (`children`, spans, the pipeline-fingerprint stamp on populated blocks). Compiling in place is what makes incremental re-translation cheap: an unchanged, same-configuration block skips the frontend on the next pass, while an edited `text` or a differently-configured pipeline drops and rebuilds its `children`. Hand the method a document you own; deep-copy first if you must keep a pristine original.

If you need custom block boundaries (for example, preserving soft line breaks), build the `DocumentIR(blocks=[...])` yourself, hand it to `Pipeline.translate_document(doc)`, and render with `result.render("layout")` if you want it laid out: hand-built blocks are used as they are (see the population contract above), so where the blocks are cut is entirely yours to decide. There is no need to step around the supported entry point into the backend's or the renderer's deep modules — the top-level policy calls those internal and free to move, so a recipe that sends you there is asking you to depend on something nobody promised.

---

<a id="arch-language-slots"></a>
## 12. Adding a language

§6.5 is about swapping one adapter in a single layer; this is the bigger step of making the whole pipeline support a new language (Japanese, Korean, and so on). The design goal is to keep the orchestrator (`Pipeline` and `backend.dispatch`) entirely language-agnostic: all four subsystems — segmentation, normalization, frontend, backend — pick their implementation by language, a new language is realized only by registering at these protocol seams plus adding resources, and the orchestrator contains no language-specific branch.

A profile's `language` field drives the whole chain; it takes the primary subtag before the hyphen (for example `ja-JP` → `ja`). Registered keys match that subtag, and the chain connects. Every pluggable family defaults to `auto`, and `auto` is **an ordinary adapter registered in the same registry**, not a special case in the orchestrator: the segmenter's and normalizer's `auto` picks by the active language (an adapter registered under the language subtag, else the built-in `default`), while the analyzer's and resolver's `auto` picks by the environment (the first engine on a preference chain that actually loads).

How far each kind is exposed on `Pipeline` differs, deliberately. The analyzer and resolver are the `analyzer` / `resolver` fields, because one language really does have several implementations with accuracy-versus-size trade-offs for a caller to weigh. **The segmenter and normalizer are not fields**: each language ships one, and which applies follows from `profile.language` with nothing left to decide — recognising a writing system is a property of the language, not a strategy like "jieba or HanLP". A knob whose only correct value is derived from another field is not configuration; it is a second place for the same fact to be wrong. A caller who does want to name one can still do it through `ctx.options` on the frontend entry points, where a name is always taken literally — `default` included, since it is the name of an adapter rather than a synonym for "no preference". To add a language, follow these steps:

1. **Segmenter**: implement the `Segmenter` protocol, recognize the language's writing system and cut its prose into typed `Segment`s (for example, tag a Japanese kana run as `kana_text`), and register it in `frontend.segmentation.segmenter_registry` under the language subtag. The built-in `default` segmenter recognizes only Han characters (emitting `hanzi_text`) plus the shared categories (numbers, Latin, Greek, and so on), so a non-Han writing system plugs in at this step.
2. **Frontend**: implement the `LanguageFrontend` protocol's `process(surface, base, ctx)`, which segments a run of the language's prose, annotates its reading, and turns it into inline IR nodes; declare which `Segment` types it consumes via `prose_types` (Chinese is `{"hanzi_text"}`, Japanese might be `{"hanzi_text", "kana_text"}`), and register it in `frontend.language_frontend_registry`. The Pipeline dispatches by `prose_types`, so the segment type stays "writing-system accurate" while routing stays "by language." The Chinese implementation `_ZhFrontend` is the worked example: it wires the zh segmenter and the pinyin resolver together. Two **optional** declarations decide whether the language shows up where a user picks an engine: `display_name` is the English name a listing prints, and `adapters` is a `{family: () -> list[str]}` mapping saying what can be chosen for this language per family (`"analyzer"` for the segmentation or morphological engine, `"resolver"` for a reading engine — a language whose readings come out of its analyzer has none). `brailix --list-analyzers` and a front-end's engine picker read them through `frontend.list_language_adapters`, so declaring them is what makes the language appear there, with no change on the reading side. Both are read with a fallback, so an implementation that omits them is still valid.
3. **Backend**: implement the `LanguageBackend` protocol — `translate_word` **and** `translate_date_marker` — translating prose nodes into cells by the language's braille rules, and register it in `backend.dispatch.language_backend_registry`. Both are required: the registry runs a runtime protocol check when it first resolves your adapter, so an implementation missing one is rejected at `get()`, not at registration. `translate_date_marker` owns both a marker's reading (年/月/日/号/时/分/秒 and their equivalents) and the orthographic rule for whether a joiner cell precedes it after a number — the language-neutral date skeleton in `backend.number` delegates every marker to it, so no date-marker rule may live outside a `LanguageBackend`. A language with no special date handling still supplies an explicit implementation; there is no inherited default, and returning the plain reading is a one-line body. Language-agnostic nodes (numbers, punctuation, Latin, math, music) keep going through the shared `_DISPATCH` table — leave them alone.
4. **Word-boundary rules (as needed)**: whether a blank cell lands between two adjacent inline nodes is the language's orthography (Chinese writes word-by-word, Japanese uses 分かち書き), not a backend braille rule. Implement a `BoundaryHandler` (takes the two neighbouring inline nodes, returns whether to insert a blank cell) and register it in `brailix.frontend.boundary_registry` under the language subtag; the zh and ja handlers are the worked examples.
5. **Normalizer (as needed)**: the default normalizer carries Chinese structural rules (fixed readings for date markers like year/month/day). If the new language has its own structural conventions, implement the `Normalizer` protocol and register it in `frontend.normalization.normalizer_registry` under the language subtag; if not, reuse `default`.
6. **Resources and profile**: put the language's braille rule tables under `resources/<language>/`; the shared resources (number sign, Latin, Greek, music) are already reusable at the top level. Write a profile JSON whose `language` points at the new language and whose `tables` point at those resources. A profile's `tables.<language subtag>` group is the **generic language table slot**: the loader maps it into `BrailleProfile.lang_tables[<subtag>]` and the backend reads it via `profile.lang_table(lang, name)` (for example `lang_tables["ja"]["kana"]`) — a new language's tables need no new field on the profile dataclass.

The existing IR node set suffices. `Word` and `HanziMarker`, plus the language-neutral `reading` field (a phonetic annotation that works equally for Hanyu Pinyin and Japanese kana), are enough to carry an ideographic or a phonetic language; this is the "the IR's existing nodes are enough, only generalize the front and back ends" point in action. A single character is not a node type of its own — it is a one-character `Word`.

**The line between infrastructure and implementation.** All six seams above are registration seams, and the orchestrator stays language-agnostic — adding a language is purely additive. The *built-in implementations* are still tuned for Chinese: the `default` segmenter recognizes only Han characters, and the `default` normalizer understands only Chinese date markers. These are default implementations awaiting replacement — a new language overrides them by registering its own segmenter and normalizer. In other words, the infrastructure (each subsystem's language selection plus the generic routing by `prose_types`) is already in place; what remains for any given language is writing its concrete recognition and rules on top of unchanged architecture. Japanese (kana braille) has landed through all six steps and is the second in-library language after Chinese.

---

<a id="arch-testing"></a>
## 13. Testing strategy

The suite is organised by layer, because that is the promise being kept: each directory under `tests/` runs on its own, against the layer that can be loaded on its own.

| Layer | What it tests | Independent of |
|---|---|---|
| Input | containers and dialects decoded into raw blocks (`.docx`, Markdown, `.mxl`, `.mid`, MTEF-in-OLE); a malformed file produces a diagnosis, not a traceback | the Backend and the Renderer |
| Frontend | type recognition, segmentation, pinyin and polyphone resolution, the Japanese kana and wakachigaki path, and the math / music / graphics parse entry points | the Backend |
| IR | the mediator types on their own terms: serialization round-trips, nested-block validation, JSON-schema conformance, and the nesting-depth limits | every stage — it carries core primitives alone, which is checked by loading it in a fresh interpreter |
| Backend | fixed IR → fixed BrailleIR, per language and per subsystem (Chinese, Latin, Japanese, math, music, chemistry), plus the tactile backend turning a normalized SVG tree into a `TactileRaster` | segmentation models, so model drift cannot move the assertions |
| Renderer | cells and dots into their external form: Unicode, BRF, the cell listing, layout and pagination, music layout schemes, and the raster encoders (BMP, PNG, PDF, and the U+2800 preview) | the source language — everything needed is already in the cells |
| Pipeline | the layers together: end-to-end compilation, incremental recompilation and cache identity, override application, span provenance, and mixed pages carrying braille beside figures | — this is the level where the seams are the subject |
| Public surface and architecture | that `__all__`, the hand-written manifest and the runtime namespace agree; that the layer matrix holds in the source *and* in a fresh interpreter's `sys.modules`; that this document, the docstrings and the user guides still describe the code | — these read the tree rather than compile anything |

Both output domains — `braille` and `tactile_raster` — are held to that table, not just the first. A tactile figure has a frontend (source adapter to normalized SVG), a backend (`TactileRaster`), renderers (the raster encoders), and its own end-to-end and page-composition tests; the renderer registry check derives the roster from the registry itself, so a renderer added to one domain and described in neither this document nor the `Renderer` protocol fails rather than passes.

Three kinds of check carry what per-layer examples cannot:

- **Golden cases** — human-proofread source and braille pairs, in JSON so the data is reviewable apart from the code. They cover, at minimum, primary-school Chinese paragraphs; middle-school math with formulae; news text with numbers, dates, and foreign words; mixed Chinese and English; tables and lists; polyphone boundaries (重庆 / 银行 / 朝阳 / 长安); formula boundaries (nested fractions, nested radicals, matrices, error recovery); and the warnings a bad input is expected to raise. Japanese carries its own golden set beside the Chinese one.
- **Schema tests** — JSON Schemas for the document IR, the braille IR, a profile, a warning and a golden case, exercised both against real artefacts and against generated instances, which is how a loader was found to crash bare on input its own schema permits.
- **Property tests** — invariants stated once and checked over generated input: span arithmetic, serialization round-trips, incremental recompilation agreeing with a full compile, layout never losing a cell. Run under two engines, since random generation and symbolic exploration reach different corners.

Run the golden suite on every rule change; **the diff must be reviewed by hand.**

---

<a id="arch-boundaries"></a>
## 14. Component responsibilities

These are the invariants that keep each component swappable — each does exactly its own job:

- The **Normalizer**'s only reading-related job is the **fixed** readings of structural markers (year → nián, month → yuè, day → rì), written straight onto `HanziMarker.reading`; all polyphone disambiguation belongs to the PinyinResolver (see `_MARKER_PINYIN` in `frontend/normalization.py`).
- The **ZhAnalyzer** handles only Chinese word segmentation + POS.
- The **PinyinResolver**'s sole effect is filling the `pinyin` field; token types and boundaries are preserved.
- The **MathParser** (adapter + normalizer) emits only a MathML tree.
- The **Backend** consumes IR forward-only: it reads the `children` the Pipeline pre-filled (math frontend → `MathInline`, code → `CodeInline`; see §11.1) and translates them — segmentation and language selection already happened upstream. **One controlled seam**: music `<words>` / embedded lyrics and the Chinese inside chemical-reaction conditions need their embedded prose rendered to braille, so the Backend consumes a callable the `Pipeline` injects into `BackendContext.options` implementing the `InlineTextTranslator` protocol (read via `BackendContext.inline_text_translator()`, key constant `INLINE_TEXT_TRANSLATOR_KEY`). That is dependency injection, so the Backend stays importable and unit-testable on its own; with nothing injected, the handler emits a warning plus a placeholder marker.
- The **Renderer** is the presentation and output layer: it consumes `BrailleIR` (or a `TactileRaster`) and owns layout — line wrapping, indentation, pagination — plus encoding into Unicode braille, BRF bytes, dot matrices, and image/print formats. What keeps it swappable is what it must **not** do: it never makes a translation decision and never reads the source language — everything it needs is already in the cells.
- The tactile-graphics vertical holds the same lines: a **GraphicSourceAdapter** emits only an SVG string; the **tactile backend** consumes the normalized SVG tree (the graphics IR) and never imports the frontend — a graphic's `<text>` labels are translated through an injected `LabelTranslator` callable, the same DI seam as `InlineTextTranslator`; the **tactile renderers** consume only a `TactileRaster`. External `<image href>` assets resolve in the tactile backend at rasterize time — the sanctioned exception spelled out as payload rule 4 in §3.
- **The compilation path retains no compilation results; the caller owns them.** That is a claim about the *path*, not about the whole library, and the line between the two matters.
  - **The path really is stateless.** The `Pipeline` keeps no per-document or per-call result cache between calls: `CompiledBlock` and `tree_subcache` are handed back for the caller to manage ("Pipeline produces these but does not keep a cache itself"), and a block's compile provenance (`Block.frontend_fingerprint` — which configuration populated its `children`) rides on the caller's own IR object, stamped on the `Block` itself. The corollary: never move **compilation results or per-block metadata** into a process-level container (a global block cache, a side table keyed by IR object identity) to "clean up" the IR — that is what would make "hand the same IR to two front-ends and compile concurrently" unsafe. With that rule in force the only home for per-block provenance is the block itself; the field is in-memory-only and excluded from serialization, equality, and the structure key, so it never pollutes IR semantics.
  - **The library side does hold process-scoped mutable configuration, deliberately.** The adapter registries' loaders / instance caches / generation counters, the renderer registry, `core.models.asset_registry`'s asset table and the `set_managed_download` policy switch, and the fingerprint module's per-instance resolver-token table. These are the **assembly surface** — what is registered, what a name resolves to, who downloads a model — not compilation output, and process scope is where they belong: every Pipeline in one process should see the same adapters. State the cost plainly: `set_managed_download()` changes the behaviour of every adapter in the process, not just one run, so a multi-tenant host cannot use it to give tenants different policies.
  - **Concurrency therefore splits in two.** A single compile's run state (the `CompilationSession`'s collector, contexts, and tree pools) is isolated, so concurrent compiles do not interfere. The registries are internally locked, so registering at runtime while other threads compile is safe — but the change is **globally visible** by design: it advances a generation and therefore the fingerprint of every live Pipeline. That is deliberate conservative invalidation, not isolation.

Keeping each component to its own job is what lets any one of them be swapped or rewritten in isolation.

---

<a id="arch-summary"></a>
## 15. Summary

`brailix` compiles along **two** paths, and they are deliberately the same shape.

The braille path takes a source document in five moves: the frontend recognizes and structures the input; `DocumentIR` holds that meaning in a unified form; the backend applies profile-driven braille rules; `BrailleDocument` records the result as a traceable cell sequence; and a braille renderer encodes it as Unicode, BRF, or a laid-out page.

The tactile-graphics path takes a graphic source in the same five: a source adapter turns one graphic format into SVG; the normalized SVG tree *is* the graphics IR; the tactile backend applies profile-driven dot geometry; `TactileRaster` records the result as a traceable dot grid carrying its own physical size; and a tactile renderer encodes it as BMP, PNG, PDF, or a U+2800 braille-display preview.

The two meet in a mixed document, where a `DocumentIR` carrying figures composes onto tactile pages — braille text stamped as real dots beside the figures it describes. They also share their machinery rather than running in parallel: one adapter-and-registry pattern, one warning collector, one renderer registry in which every renderer declares which IR it `consumes` (`braille` or `tactile_raster`) and the result object checks that before handing one over.

- Chinese is handled by segmentation, pinyin, and polyphone disambiguation.
- Numbers and dates stay structured and travel on their own track.
- Math and music each parse into a tree IR (MathML, MusicXML), and the backend dispatches by tag through a contextual state machine.
- The braille standard is a swappable profile, and so is the tactile page's physical geometry.
- The output is traceable, proofreadable, and format-swappable, in both domains.

The whole design holds to one test: **every layer can be replaced or tested on its own.**
