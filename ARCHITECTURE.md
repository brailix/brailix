<!-- brailix architecture overview (English). The overview is maintained in more
     than one language and kept in sync by hand; the copies share one section
     structure and the same section numbering. -->

# brailix Architecture

## 0. What brailix is

`brailix` is a **braille compiler**: it takes text or documents from any source, runs them through frontend structural analysis, a unified intermediate representation (IR), and a pluggable backend, and compiles them into something a hand can read. There are two output domains: text, mathematics and music compile to **braille** (Unicode Braille, BRF, a dot array, or a laid-out braille page), and vector graphics rasterize to a **tactile image** (an embossable BMP, PNG or PDF, or a preview on a refreshable display).

**Scope.** `brailix` is exactly the *compilation path* — source → semantic IR → output-domain IR → encoded output. A generic `Pipeline.translate_block(ir_transformer=...)` hook lets a front-end insert its own IR transform between the frontend and the backend, so a CLI, a server, a textbook-publishing system, or an editing UI can build its own features on top of the compiler core. That keeps `brailix` usable as a standalone library.

Design goals:

- **Pluggable** — the tokenizer, pinyin engine, math parser, braille rules, and output format are all replaceable.
- **Profile-driven** — the same IR can be rendered by different braille standards (mainland Chinese schemes, UEB, Nemeth, textbook-specific).
- **Traceable** — every braille cell maps back to a source span, which makes human proofreading easy (the two-level coordinate contract — cell spans relative to their own leaf block, block spans locating the block in the source — is §4.4).
- **Structure-preserving** — numbers, formulae, and English each travel their own track through the frontend, keeping their native structure.

**How the code cites this document.** Section *numbers* are not stable
references: the overview is maintained in more than one language, and although
the copies now share one structure and numbering, that alignment is a
maintained convention rather than a mechanism — and the public mirror carries
only this copy, so a number written in a comment is a claim about a document the
reader may not be holding. Code therefore cites a **stable anchor** instead,
written `ARCHITECTURE#arch-boundaries`, which is both a working link and a
string you can search for. Each anchor is declared as an `<a id="...">` above
the section it names, in every copy, and `tests/test_architecture_anchors.py`
fails if code cites an anchor that any copy in the checkout leaves undeclared.
Renumbering or reordering a section is free; moving an invariant means moving
its anchor with it.

---

<a id="arch-layers"></a>
## 1. The layered model

The compiler is a stack of layers. The Profile and its resource tables sit alongside the whole stack, supplying the rules and dot tables that the backend and renderer read.

```
┌───────────────────────────────────────────────────────┐
│  Input Layer       many sources → one Document        │
├───────────────────────────────────────────────────────┤
│  Frontend Layer    text → structured IR               │
│  ├─ LanguageFrontend  per language: segment + prose   │
│  ├─ Normalize      tag numbers / dates / punctuation  │
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

> The math subsystem keeps no separate IR of its own: it uses the normalized MathML tree (`xml.etree.ElementTree.Element`) directly.

> **The music subsystem** (added after this document was first written; isomorphic to math): a source (MusicXML / `.mxl` / MIDI / ABC) goes through an adapter into a normalized **MusicXML tree** (`ET.Element` — that tree is the IR), and the MusicBraille backend dispatches by element tag and runs a contextual state machine (BANA 2015). The code lives in the frontend `frontend/music/`, the backend `backend/music/` (whose `handlers/` subpackage is split into files by BANA chapter), the resources `resources/music/`, and the input adapter `input/music_xml.py`. The sections below use the text and math paths as their running examples; music follows the same adapter-plus-normalized-mediator pattern and is not re-walked section by section.

> **The tactile-graphics subsystem** (isomorphic to math and music, though its product is not braille cells): a source (SVG / a primitives spec / a figure spec / an external image reference) goes through an adapter into a normalized **SVG tree** (`ET.Element` — the IR), and the tactile backend dispatches by element tag and rasterizes it into a `TactileRaster` (`ir/tactile.py`; the backend's product, the counterpart of BrailleIR). A graphic is never translated into braille cells: the `<text>` labels inside it are translated through an injected `LabelTranslator` callable and stamped in as braille dots (a dependency-injection seam — see §12). The raster is encoded by the bmp / png / pdf / tactile_preview renderers in the **shared** `renderer_registry` (the first three emit image bytes, the last a U+2800 preview text; each renderer self-describes which IR it `consumes`). The code lives in the frontend `frontend/graphics/`, the backend `backend/tactile/` (including `profile.py`'s `TactileProfile`: millimetre adaptation parameters plus the single DPI dial), and the resources `resources/tactile/`. The entry points are the **module-level** `brailix.translate_graphic` (compiling a graphic needs no braille standard — the product is a raster, not cells; only `<text>` label translation touches one; `Pipeline.translate_graphic` is a convenience delegate that reuses its own text path for labels when the standards match) and `Pipeline.translate_document_to_pages`.

**Core principle: each layer answers exactly one question.**

| Layer | What it answers | What it does **not** answer |
|---|---|---|
| Frontend | what is this? | how is it written in braille? |
| IR | how is it structured? | which characters represent it? |
| Backend | how do the rules write it? | how does it wrap and lay out? |
| Renderer | what form does it leave in? | what does it mean? |

**The input/frontend boundary.** Input answers only "what blocks does this document have, and where is the raw content": it cracks open containers (the `.docx` OOXML and OLE, the `.mxl` ZIP), picks a parser by file identity (suffix or content sniff), and yields a `DocumentIR` of block structure with inline content still raw text. Frontend answers "what each inline region is": it translates a known source dialect (LaTeX, MathML, OMML, MTEF, MIDI, ...) into normalized IR (a MathML or MusicXML tree), picking an adapter by context and soft-failing to `<merror>` / `<music-error>`. Both parse source formats; the dividing line is **payload shape**, not timing:

1. A **text** dialect (OMML, Word EQ field, LaTeX, ABC) is kept raw in the input layer and deferred to the frontend — block-level as `MathBlock(source=...)`, inline as a source-tagged `$...$` island (`brailix.core.inline_math`) embedded in `Block.text`. Both are converted by the frontend's `parse_math_tree` (via `FrontendDriver.attach_math` / `populate_math_block`).
2. A **binary** dialect (MathType MTEF, MIDI, the `.mxl` ZIP) is decoded at the input boundary, because the text IR carries no binary payload. This is the deliberate exception to the rule, not an asymmetry.
3. **Word-native formatting that "reads as a formula"** (super/subscript runs via `<w:vertAlign>`, e.g. formatted x-squared or H₂O restored to a formula) is not a foreign dialect — but neither does the input layer build a tree: it only *linearizes* a maximal cluster of script-bearing runs into a mini-language of its own (`base ^{sup} _{sub}`) and defers it like rule 1, tagged `script_cluster` (or `script_cluster_chem` when chemistry detection is on). The tree is built at Pipeline time by the same-named adapter in the math source registry (`frontend/math/adapters/script_cluster.py`). It is a special case of rule 1, not an exception: the payload is text, just in a dialect this repository invented.
4. A **reference** payload (a tactile graphic's `<image href>`): a bitmap or an external SVG file lives outside the document container — the fence body carries only a textual path / spec — so it stays a *reference* in the IR. The graphics frontend's image adapter reads only the file's dimensions (to set the `viewBox` and physical size); pixel decoding is deferred to the tactile backend at rasterize time (`backend/tactile/_image.py` resolves the href — data URI and filesystem path alike). A deliberate exception to rule 2: the binary never enters the IR and is not decoded at input, at the cost that a graphic IR is not self-contained (moving the referenced file makes a recompile soft-fail to a blank raster plus a `GRAPHICS_IMAGE_LOAD_FAILED` warning, never a crash), in exchange for sources and project files that don't balloon with embedded bitmaps.
   - A **document-embedded bitmap** (a picture inserted into a Word `.docx`) blends rules 2 and 4: the bytes *are* in the document container (`word/media/`), so they are extracted eagerly at the input boundary (rule 2) onto a document-level side table `DocumentIR.assets` (name → bytes; excluded from `to_dict`, which is the text-IR view); but the IR keeps only a *reference* (the asset name `media/imageN.png`, carried by an `ImageAlt` placeholder or, once converted, a `graphic-image` fence's `path`), which a caller-injected `GraphicAssetResolver` on the `GraphicsContext` (the same injection seam as `InlineTextTranslator`, §12) resolves back to bytes — inlined as a data URI — at compile time. The bytes never enter the source text (megabyte base64 lines would hurt screen-reader navigation and per-keystroke recompilation); a project file persists them as base64 instead, so an imported picture survives a save / reopen with no external file. Whether such a picture *becomes* a tactile graphic is the user's explicit, per-image decision (the `ImageAlt` placeholder otherwise emits just its alt text plus an `IMAGE_NOT_CONVERTED` warning).

So the input layer imports no math/music source registry from the frontend except from the explicitly allowlisted binary-container decoders — today `input/music_xml.py` (`.mxl` / MIDI → the music source registry) and `input/docx/_ole.py` (MathType MTEF inside a `.docx` → the math source registry). It is an allowlist rather than a single blessed site: a new binary container argues its way onto the list (`tests/test_core_layering.py`), and a reader meeting the second entry is not looking at a violation. The dependency is strictly one-way: the frontend never imports the input layer. The graphics fence likewise stays registry-free on the input side — a purely **structural** rule (bare ```` ```graphic ```` is the SVG alias; ```` ```graphic-<name> ```` carries `<name>` verbatim as the source name, the same shape as inline math's dialect-tagged islands), so a newly registered graphics source gets a fence tag with no input-layer change and an unknown name soft-fails at compile time (`GRAPHICS_ADAPTER_MISSING` plus a blank raster). Both directions of the fence grammar have one owner, `input/markdown.py` (`graphic_fence_source` / `graphic_fence_open`) — an editor re-tagging a fence never spells the tag itself.

---

## 2. Directory structure

> The tree below reflects the current real structure (the early single-file version was long ago split into subpackages). File names follow what is actually in the repo; entries an older draft of this document drew — `ir/schema.py`, `frontend/punctuation.py`, `input/latex.py`, `asciimath`, `pkuseg`, and the like — never existed and have been deleted. (`cli.py` was once on that never-existed list, but it landed on 2026-06-07; see §9 / §11 below.)

```
brailix/
├── brailix/
│   ├── __init__.py
│   ├── __main__.py          # `python -m brailix` entry point (forwards to cli.main)
│   ├── cli.py               # command-line interface (argparse; registry-enumerated dynamic choices; stdin/UTF-8; the exit-code contract)
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
│   │   ├── segment.py        # Segment — the mediator between the segmentation and normalization passes (named by a core protocol, so not in the frontend; not an IR node either)
│   │   ├── registry.py       # generic name→loader registry (lazy load + MissingExtraError)
│   │   ├── protocols.py      # LanguageFrontend / LanguageBackend / MathSourceAdapter / MusicSourceAdapter / GraphicSourceAdapter / InlineTextTranslator / GraphicAssetResolver / Renderer
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
│   │   ├── segmentation.py   # built-in character-class chunking (segment / segment_text / char_category); every language's segmentation builds on it
│   │   ├── normalization.py  # fixed pass: Segment → semantic inline IR (numbers / dates / punctuation / math islands)
│   │   ├── zh/               # Chinese-specific (language folder)
│   │   │   ├── __init__.py        # umbrella: re-exports the analyzer's subsystem entry points
│   │   │   ├── analyzer/          # segmentation subsystem
│   │   │   │   ├── registry.py        # ChineseAnalyzer registry
│   │   │   │   └── adapters/         # auto / char / jieba / hanlp / thulac → ChineseToken
│   │   │   └── pinyin/            # pinyin + polyphone disambiguation (independent subsystem)
│   │   │       ├── registry.py        # PinyinResolver registry
│   │   │       └── adapters/         # auto / null / pypinyin / g2pm / g2pw
│   │   ├── ja/               # Japanese (language folder): kana/kanji segmentation + analyzer adapters (kana / janome / fugashi / sudachi) + 文節 spacing
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

> The CLI (`brailix/cli.py`) landed 2026-06-07: the `brailix` / `python -m brailix` entry point, built on `argparse`, with `--to` / `--list-*` choices enumerated dynamically from the renderers and the registries (no hard-coded allowlist), stdin and output handled as UTF-8 (coping with non-UTF-8 Windows console code pages), and an exit-code contract of 0 for success / 1 for a translation or IO error / 2 for a usage error. See §9 / §11.

---

<a id="arch-traceability"></a>
## 3. The data flow (end to end)

```
source document
  │
  ▼  Input
DocumentIR (block structure only; inline content still raw text)
  │
  ▼  Segment: recognize the inline regions inside each block
DocumentIR + a list of inline Segments
  │
  ▼  Normalize: tag dates / numbers / units
        ↓                            ↓
   ▼ ZhAnalyzer       ▼ MathParser          ▼ MusicParser
   segmentation + POS  source→MathML tree (= IR)  source→MusicXML tree (= IR)
        ↓                            ↓
   ▼ PinyinResolver
   polyphone disambiguation + confidence
  │
  ▼  IRBuilder: merge into the complete InlineIR
DocumentIR (complete)
  │
  ▼  IRValidator: structural validity checks
  │
  ▼  Backend.dispatch: dispatch by node type
BrailleIR (a cell sequence, every cell carrying its source span)
  │
  ▼  Layout: line wrapping / indentation / pagination
  │
  ▼  Renderer: unicode / brf / cells / layout
final output + WarningCollector
```

The diagram above shows the braille domain. Tactile graphics take the other half of the same chain: the graphics frontend produces an SVG tree (the semantic IR), the tactile backend rasterizes it into a `TactileRaster` (the output-domain IR), and the tactile renderers bmp / png / pdf / tactile_preview produce the final artefact (the first three image bytes, the last a U+2800 preview text). The two domains fork after the Backend and share everything before it.

Key points:

- **Segmentation runs only on Chinese regions.** Numbers, formulae, and English are protected back at the segment stage.
- **Pinyin runs only on Chinese tokens.** `2026`, `x^2`, and `CPU` never get pinyin.
- **Math parses on its own dedicated track.** It never joins the generic token stream.
- **Every BrailleIR cell carries a `source_span`.** That single field is what makes the output debuggable, lets the renderer wrap lines without losing provenance, and powers the proofreading system: a proofreader clicking any braille cell can jump back to the exact source position behind it. And not only cells derived from source characters: the control and spacing cells the backend inserts carry one too. The number sign anchors to the zero-width position at the start of the digit run it prefixes; word / column / punctuation spacing blanks, the line breaks of matrices and equation systems (`line_break`), and the hanging-indent brackets (`hang_open` / `hang_close`) are all built by the span-carrying factories in `brailix.ir.braille` (`blank_cell` / `line_break_cell` / `hang_open_cell` / `hang_close_cell`), carrying the span of the node that triggered them or an adjacent zero-width boundary span. The invariant admits no role exceptions (`role` is a display label, not a provenance contract); `tests/integration/test_source_span_contract.py` walks the whole BrailleIR to guard it.
- **A cell's span originates in a token's span, so the entrance is where it gets checked.** A tokenizer or a reading engine is selected by name from an open registry, and its `Protocol` can only prove the adapter *has* an `analyze` or `resolve` method — never what comes back out of it. The spans are not merely carried, either: `tokens_to_inline` places a word-boundary blank at each token's `span.end`, the cross-kind boundary rules decide between a space and a connector by asking whether two runs are source-adjacent, and Japanese bunsetsu spacing does the same, so coordinates that overlap change the braille rather than only its provenance. Each of the three subsystem entry points (`tokenize`, `analyze`, `annotate`) therefore validates the result as it crosses into the library: container and element types, that a span is a `Span`, that it stays inside the analyzed text, that consecutive spans are ordered and non-overlapping, and — for a resolver — that the number, order, surfaces, spans and POS tags of the tokens it was handed are unchanged, plus that the two fields it *may* change (`pinyin`, `confidence`) hold what `ChineseToken` declares, a confidence being a probability in `[0, 1]` because a threshold is compared against it. What the ordering rules are applied to is the span each token **ends up with**: an adapter may omit spans, in which case coordinates are laid out from a running cursor, and checking only the explicit ones checks an order nothing downstream ever sees — a spanless token followed by a span pointing backwards passed a check on each half and produced overlapping provenance out of the pair. A violation raises `FrontendContractError`, the input-side counterpart of `BackendContractError` and drawn on the same line: a defect in an adapter's code is not a property of the user's document, so no run mode downgrades it. **One thing is deliberately not an error**: a surface that does not match the text its span points at. An analyzer that normalises its input legitimately produces one (both shipped cursor-recovery adapters do), which makes that token's coordinates approximate rather than the document untranslatable, so it is reported as a `TOKEN_SPAN_MISMATCH` warning.

---

## 4. IR design

Five IRs, in two groups. Three describe the document, coarse to fine: block-level `DocumentIR`, inline `InlineIR`, and the normalized tree each of the math / music / graphics verticals uses directly as its own IR. Two are **output-domain IR** — the backend's product, one per output path: `BrailleIR` (a cell sequence) for braille and `TactileRaster` (a dot grid; see §1) for tactile graphics. The two output domains are peers, not braille plus an appendix.

### 4.1 DocumentIR (block level)

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

Block types: `heading / paragraph / list / list_item / table / table_row / table_cell / quote / footnote / code_block / math_block / score / music_block / image_alt / graphic`. (`score` is a whole score, `ScoreBlock`; `music_block` a single passage, `MusicBlock` — both share `EmbeddedBlock` with `MathBlock` and `GraphicBlock`, holding their normalized tree on the block's own `tree`. `graphic` is a tactile graphic, `GraphicBlock`, whose source is SVG, a primitives spec or a figure spec; it is the one block that does not translate to braille cells, rasterizing instead into a `TactileRaster` — see §1 and §4.3.)

**A block has two kinds of content, one field each.** `inlines` are the typed tokens a leaf block's text became; `blocks` are nested blocks — a list's items, a table's rows, a row's cells. Those used to be `children` (inline-only, despite the name) plus a per-class structural field (`List.items`, `Table.rows`, `TableRow.cells`), which made the document two trees wearing one name: everything that walked it had to know which of four field names a given block kept its children under — the serializer, the deserializer, `structure_key`, the frontend driver, the block backend, the surface reconstruction, the front-end's leaf collector. One name for one relationship deletes all of that. *Which* class the nested blocks must be is the owning class's `child_type` (`List` → `ListItem`, `Table` → `TableRow`, `TableRow` → `TableCell`; `None` everywhere else, meaning "holds no nested blocks").

### 4.2 InlineIR (inline tokens)

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

> `phonetic_inline` is an English IPA transcription: a `/.../` or `[...]` region in prose whose content carries an IPA-distinct character is recognised during segmentation as a protected region (same mechanism as `$...$`; math wins any conflict). The node holds only the phoneme run with its delimiters stripped, and `backend/phonetic` greedily longest-matches each phoneme against the profile's phonetic table (two-character phonemes like `tʃ` / `eɪ` beat their single-character prefixes), flagging a symbol the table doesn't define (a stress mark) with `PHONETIC_UNKNOWN_SYMBOL` rather than inventing braille.

> A single character is simply a one-character `word` — there is no separate node type for it (both language backends translated it through an identical call, and every consumer discriminated on it alongside `word` anyway); `unknown` keeps the pipeline running on anything else.

> `math_inline` carries a tree IR (the normalized MathML) and is the **only** inline node that carries a tree — because a formula genuinely does appear mid-sentence. `music_inline` and `graphic_inline` once sat beside it, but the frontend never produced them: they merely wrapped the block-level `score` / `music_block` / `graphic` tree in a single-element `children` list so the backend dispatch table could route it. Once the blocks held their own trees (`EmbeddedBlock.tree`, §4.3), the two nodes had no reason to exist — and with them went the block backend's "don't translate the graphic child" special case.

### 4.3 The math subsystem (MathML as the IR)

A math formula keeps no separate IR; the normalized MathML tree is used directly — a score its normalized MusicXML tree and a tactile graphic its normalized SVG tree, in exactly the same way (§1). The math example:

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

The backend dispatches by element tag, and every translation rule is profile-driven.

<a id="arch-braille-ir"></a>
### 4.4 BrailleIR (cell sequence)

```python
@dataclass(slots=True, frozen=True)
class BrailleCell:
    dots: tuple[int, ...] = ()  # e.g. (1, 2, 4); normalised to ascending order in __post_init__
    role: str | None = None     # 'number_sign' / 'zh_initial' / 'math_op' ...
    source_span: Span | None = None  # serialised as [start, end]
    source_text: str | None = None
```

<a id="arch-spans"></a>
**The span coordinate contract** (two coordinate systems, one per level): a cell's `source_span` (like an inline node's `span`) is **leaf-local** — offsets into the owning leaf block's own `text`, starting at 0 per block — because formats like `.docx` have no document-wide character coordinate at all, so leaf-local is the only system well-defined for every input. The block-level `Block.span` is what locates a block in its source document; whenever a block upholds the **exact-slice contract** `source[block.span] == block.text` (every plain-text block; Markdown headings, list items and single-line paragraphs — marker prefixes like `# ` / `- ` sit *outside* the span), `block.span.start + leaf_local` is the exact source position. Blocks whose `text` is derived rather than sliced (multi-line paragraphs joined with spaces, quotes with `> ` stripped, fence bodies) carry a line-range span: located, but without a per-character promise.

**The one exception: a table row is the leaf.** The backend flattens a whole `TableRow` into a single braille block, joining its cells with two blank cells, so the unit a consumer resolves against is the row, not the cell. A `TableCell`'s own `span` and every span inside it are therefore **row-local** — offsets into `"  ".join(cell.text for cell in row.cells)`, so `row_text[node.span]` slices the node's surface exactly. `FrontendDriver._populate_row` re-establishes that invariant on **every** populate pass (rather than shifting once when a cell is first filled), so re-compiling after an edit that changes one column's width still leaves the later columns pointing at the right characters. The price is that a table cell has no source-document coordinate at all — the Markdown adapter already rebuilds cell offsets from the de-syntaxed cell text instead of slicing the source row.

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

## 5. The math subsystem (MathML normalization)

Math is the part of the project most likely to break and the biggest long-term extensibility risk: it will eventually need many sources and targets — Word, EPUB, LaTeX, HTML, MathJax output, and so on. So it is the fullest expression of the §7.1 pattern: every source is routed through a single mediator, **MathML**, by adapters that reuse existing tools. The project is tied to no single math parsing library, and no LaTeX / OMML parser is written by hand here.

### 5.1 The design idea: MathML as both the IR and the normalization mediator

Treat **MathML as the unified mediator for every math source format**. The normalized MathML tree (`xml.etree.ElementTree.Element`) is at the same time the math subsystem's IR — there is no home-grown intermediate structure, and the backend dispatches directly by element tag.

```
LaTeX         ──┐
OMML (Word)     ─┤
MTEF / EQ field ─┤  → MathML string (an adapter emits it) → ET.Element tree → MathBraille backend
chem          ───┤
plain / Unicode ┘
```

Why MathML:

- It is a W3C standard — the lingua franca between Word, LibreOffice, EPUB3, MathJax, and KaTeX.
- LaTeX → MathML has `latex2mathml`, `pylatexenc`, MathJax-node, and others.
- Word's OMML → MathML has the XSL transform that ships with OOXML, and pandoc.
- MathML inside HTML/EPUB can be parsed directly with `lxml`.
- A new source format later means **one more → MathML adapter**, and nothing downstream changes.

### 5.2 The two stages

```
raw formula (any source)
   ↓  MathSourceAdapter (dispatched by source)
MathML (a standard XML string)
   ↓  MathMLNormalizer (strip namespaces, collapse single-child mrows, trim whitespace, wrap <merror>)
normalized MathML (ET.Element)  ← this is the IR
   ↓
MathBraille backend (dispatches by element tag)
```

### 5.3 The MathSourceAdapter interface

```python
class MathSourceAdapter(Protocol):
    source: str  # "latex" / "omml" / "mathml" / "chem" / ...

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        """Convert math from any source into a standard MathML string."""
```

Default implementations:

| source | shipped adapter | notes |
|---|---|---|
| `mathml` | straight through `xml.etree.ElementTree` | stdlib; `lxml` is an alternative |
| `latex` | `latex2mathml` (the `latex` extra) | `pylatexenc` / `mathjax-node` are possible alternatives |
| `omml` | built-in OOXML `<m:oMath>` → MathML converter | pure stdlib; Word formulae (extracting them from a `.docx` rides the `docx` extra) |
| `mtef` / `eq_field` | built-in MathType / Equation 3.0 extractors → MathML | pure stdlib; legacy Word equation objects (cracking the OLE payload needs `olefile`) |
| `chem` | built-in `\ce{...}` → MathML | pure stdlib; chemical equations |
| `script_cluster` | built-in Word super/subscript cluster → MathML | the repository's own dialect of §1, rule 3 |
| `plain` / `unicode` | a minimal heuristic → MathML | simple structures only (fallback) |

Each adapter does exactly one thing — **emit a valid MathML string**; it emits no braille, and on error it returns `<merror>` and adds a warning.

### 5.4 MathContext

```python
@dataclass
class MathContext:
    mode: Literal["inline", "display"]
    source: str               # latex / omml / mathml / plain
    profile: str
    surrounding_text: tuple[str, str] | None = None  # (before, after)
```

The context carries only what the tree does not: the mode, the source, the profile, and the surrounding text (which the backend sometimes needs). Structure itself lives entirely in the MathML tree — there are no lexer-token / state-stack / `in_fraction` fields any more; the tree carries all of that.

### 5.5 Key rules

- **The MathSourceAdapter emits only a MathML string.**
- **The `ET.Element` the MathMLNormalizer emits *is* the IR** — the backend consumes the tree directly.
- **A parse failure stays in-band.** The adapter returns MathML containing `<merror>`, the normalizer passes it through, and the backend (in `_emit_merror`) emits a `MATH_ERROR` warning plus an unknown cell, and continues.
- **The backend runs a contextual state machine.** As MathBraille walks the tree, `MathBrailleContext` controls when to emit a superscript indicator, when to reset `need_number_sign`, and when to add a separator — braille output rules are inherently context-dependent.

Two finer invariants keep the layers clean: the MathML tree stays pure structure (dots and profile keys live in the backend and profile), and the profile JSON stays a data table (rules live in code). The math backend works from the normalized tree alone.

---

## 6. Backend design

<a id="arch-dispatch"></a>
### 6.1 Dispatcher

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

> Prose nodes (`word`) are translated by the `LanguageBackend` for the profile's language — the `self.zh` above is just a Chinese stand-in, and the real dispatch picks an implementation by `profile.language` (see §7.6). All other nodes go through the shared dispatch table by type.

### 6.2 BackendContext

It carries only the profile name, the run mode, the current block type, the shared warning collector, and an options bag. Context-dependent **braille state** — the number-sign latch, math nesting depth and the rest — deliberately does *not* live here; each subsystem's own state machine holds it (`MathBrailleContext.need_number_sign`, `MusicBrailleContext`). A single shared bag of those flags was never read by the dispatcher and only invited writes that were silently ignored, so it was removed. The inline-text translator is injected through `options` (the controlled exception in §12).

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

### 6.3 Profile

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

<a id="arch-adapters"></a>
## 7. The plugin interface and the "adapter + normalized mediator" pattern

§7.1 states the pattern; the rest of this section is its machinery. The library core ships with **zero third-party parsing dependencies** — every concrete parser is an adapter behind an optional extra.

<a id="arch-mediators"></a>
### 7.1 The core pattern

The whole library follows **one uniform pattern**:

> **The library itself depends on no concrete third-party implementation; it defines "normalized mediator formats" only, and plugs the various external tools in through adapters.**

Every subsystem with a choice of external library is organized the same three-part way:

| Subsystem | adapter input | **normalized mediator** | downstream consumers |
|---|---|---|---|
| Chinese segmentation | HanLP / jieba / THULAC / custom | `ChineseToken` | PinyinResolver, IRBuilder |
| Pinyin | g2pW / pypinyin / dictionary lookup | pinyin annotation (numeric tones) | Backend |
| Math parsing | latex2mathml / built-in OMML · MTEF · EQ-field · chem converters / passthrough | **MathML (`ET.Element`)** | MathBraille backend |
| Music parsing | MusicXML passthrough / `.mxl` unpack / mido + partitura / abc-xml-converter / custom | **MusicXML tree (`ET.Element`)** | MusicBraille backend |
| Graphics parsing | SVG passthrough / primitives spec / figure spec / image reference (Pillow) | **SVG tree (`ET.Element`)** | tactile backend (rasterization) |
| Document input | python-docx / markdown-it / lxml / plain text | `DocumentIR` | Frontend |
| Braille output | — | `BrailleIR` | braille renderers (unicode / brf / cells / layout) |
| Tactile rasterization | — | `TactileRaster` | tactile renderers (bmp / png / pdf / tactile_preview) |

Whichever adapter you pick, **downstream only ever sees the normalized mediator format**, so swapping an adapter never touches a line of downstream code. The same property is what makes each layer testable on its own: feed a fixed mediator value in, assert on the mediator value out.

### 7.2 Protocol definitions

```python
# core/protocols.py

class LanguageFrontend(Protocol):        # one language: cut its own prose out, then prose → inline IR
    prose_types: Collection[str]
    def segment(self, block: Block, ctx: FrontendContext | None) -> list[Segment]: ...
    def process(self, surface: str, base: int, ctx: FrontendContext) -> list[InlineNode]: ...

class LanguageBackend(Protocol):  # prose nodes (Word / DateComponent) → cells, per language
    def translate_word(self, node: Word, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]: ...
    # Both are required — the registry runs a runtime protocol check on
    # first resolution, so an implementation missing one is rejected at get().
    def translate_date_marker(self, component: DateComponent, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]: ...

class MathSourceAdapter(Protocol):
    source: str  # latex / omml / mathml / chem / ...
    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str: ...

class MusicSourceAdapter(Protocol):
    source: str  # musicxml / mxl / midi / abc / plain
    def to_musicxml(self, src: str | bytes, ctx: MusicContext | None = None) -> str: ...

class GraphicSourceAdapter(Protocol):
    source: str  # svg / primitives / figure / image / ...
    def to_svg(self, src: str | bytes, ctx: GraphicsContext | None = None) -> str: ...

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

(Two further protocols support the backend rather than extend a source family: `InlineTextTranslator`, the one controlled backend→frontend dependency, injected through `BackendContext.options` to translate embedded prose (§12); and `GraphicAssetResolver`, which resolves a graphic's asset reference to in-document bytes. `core/protocols.py` is the authoritative list, and the extension-surface manifest in the test suite keeps the two in step.)

There is deliberately **no `Backend` protocol**. The backend isn't a pluggable-by-name adapter; it's a node-type dispatcher (§6.1), so it has no registry and no name→implementation contract. A new braille standard is added with a Profile JSON plus resources, not by registering a backend. Per-language *prose* translation is the one pluggable seam, and it goes through `LanguageBackend` above (§7.6).

There are deliberately **no `Segmenter` and `Normalizer` protocols** either. Both used to be full plugin families — a protocol, a registry, an `auto` adapter, a `ctx.options` key each — and both were keyed by the language subtag, making them two locks on the key `language_frontend_registry` already turned. Segmentation is **one language's own lexical policy** (does a kana run continue into the kanji beside it? only that language can say), so it is `LanguageFrontend.segment`: one object, one registration, alongside `process`. The language-*neutral* half — digits, Latin, Greek, punctuation, `$...$` and IPA protected regions — is not rewritten per language: `frontend.segmentation` publishes `segment_text` (the chunker, parameterised by a character classifier) and `char_category` (the built-in classifier), so Japanese writes only "kana and kanji are both `ja_text`". Normalization is not a strategy at all but the canonical lowering from Segment to semantic inline IR (`digit_run` *is* a `Number`) — part of the IR's contract, and a fixed pass. Its registry held, to the end, the built-in plus an `auto` that delegated back to the built-in.

<a id="arch-registries"></a>
### 7.3 Registries and on-demand loading

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

### 7.4 Dependency groups (pyproject extras)

The library core depends on no third-party parsing library; every adapter rides on an optional extra:

```toml
[project.optional-dependencies]
# Chinese segmentation
hanlp    = ["hanlp", "transformers<4.55"]  # hanlp puts no upper bound on transformers; 5.0 breaks it
thulac   = ["thulac"]
jieba    = ["jieba"]
# pinyin
g2pm     = ["g2pM", "numpy"]        # g2pM fails to declare the numpy it runs on; pinned here
g2pw     = ["g2pw", "torch", "requests"]  # g2pw imports torch and requests at import time, undeclared
pypinyin = ["pypinyin"]
# math sources (MathML parsing is stdlib; the OMML / MTEF / EQ-field / chem adapters are
# pure stdlib — only the LaTeX source needs a third-party package)
latex    = ["latex2mathml"]
# document input (the legacy binary .doc format needs a system LibreOffice, not a Python dep)
docx     = ["python-docx", "lxml", "olefile"]
# music sources (MusicXML / .mxl are stdlib; MIDI / ABC need packages)
midi     = ["mido", "partitura"]
abc      = ["abc-xml-converter"]
music    = ["mido", "partitura", "abc-xml-converter"]
# tactile graphics (SVG parsing + BMP/PNG/PDF writing are all stdlib; only reading an
# external bitmap and fully rendering an external SVG need packages)
graphics = ["pillow"]
graphics-svg-raster = ["resvg-py", "pillow"]
# Japanese morphology (kanji reading annotation)
janome   = ["janome"]
fugashi  = ["fugashi", "unidic-lite"]
sudachi  = ["sudachipy", "sudachidict-core"]
# convenience bundle (the upstream-gap pins above ride along here too)
recommended = ["hanlp", "transformers<4.55", "g2pw", "torch", "latex2mathml"]
```

```bash
pip install brailix[recommended]            # the recommended path
pip install brailix[jieba,pypinyin]         # a light path
pip install brailix[thulac,g2pm,latex,docx] # the fully offline path + Word input
```

Three of those extras pin a dependency the upstream package failed to declare, which is worth spelling out because it looks arbitrary otherwise. HanLP 2.1.3 puts no upper bound on `transformers`, but transformers 5.0 removed an interface it still calls, so `transformers<4.55` rides along with it; g2pM ships its weights but runs them on numpy without declaring numpy, so `numpy` rides along; and g2pw 0.1.1 imports `torch.utils.data` and `requests` at import time while declaring only onnxruntime / tqdm / transformers, so `torch` and `requests` ride along with that. `pyproject.toml` is the authoritative list and records the conditions for dropping each.

If an adapter's package is missing at runtime, the registry raises a clear **`MissingExtraError`** that names the extra to install.

<a id="arch-open-closed"></a>
### 7.5 The extensibility this mechanism buys

Adding any external tool means writing one adapter file:

```
a new tokenizer (LAC, ckip)    → frontend/zh/analyzer/adapters/<name>.py
a new pinyin engine (xpinyin)  → frontend/zh/pinyin/adapters/<name>.py
a new math source (MathJax JSON) → frontend/math/adapters/<name>.py
a new braille standard         → a new package under backend/ + a new profile
a new output format (PEF, ...) → one more module under renderer/
```

To be precise about where that promise holds: for a new tool behind an **existing protocol** — the cases listed above — no core code changes; the adapter file registers itself and a profile (or an explicit option) selects it. Two kinds of extension *do* grow the core, by design: a **new IR node type** must join the backend dispatch table (and its serialization and tests), and a **new domain vertical** — a fifth mediator alongside text, math, music, and graphics — touches orchestration the same way. Adding a whole new *language* sits in between: it is registration-only (no orchestrator branches), but it does mean registering several implementations plus a profile — §7.6 walks through exactly what it takes.

Put plainly: **the adapter layer is the open extension surface (Registry + Protocol, plug-and-play), while the set of IR node and block types is a repo-internal closed world.** Adding an `InlineNode` or `Block` is a coordinated change across several points (dataclass + registry + serialization + backend dispatch + schema + tests) — a deliberate closed set, not a plugin seam. Serialization is the one of those that is declarative: a block's nested blocks all live in `blocks`, and which class their entries must be is the class's own `child_type` — that single declaration is what writes the field out *and* what rebuilds it on the way back, with both directions checking every entry against it, so a tree that serializes is a tree that reloads. Nested blocks put anywhere else make `to_dict` raise rather than skip them, which is what it used to do — saving succeeded, the JSON was valid, and the field was gone after a reload. The normal move is to fold a new domain into one of the existing mediators (text, math, music, or graphics) and carry it on the nodes already there.

<a id="arch-language-slots"></a>
### 7.6 Adding a language

§7.5 is about swapping one adapter in a single layer; this is the bigger step of making the whole pipeline support a new language (Japanese, Korean, and so on). The design goal is to keep the orchestrator (`Pipeline` and `backend.dispatch`) entirely language-agnostic: all four subsystems — segmentation, normalization, frontend, backend — pick their implementation by language, a new language is realized only by registering at these protocol seams plus adding resources, and the orchestrator contains no language-specific branch.

A profile's `language` field drives the whole chain; it takes the primary subtag before the hyphen (for example `ja-JP` → `ja`). Registered keys match that subtag, and the chain connects. A language itself has no "default implementation": either it is registered or it is not (a prose segment then reports `NO_LANGUAGE_FRONTEND`, while the rest of the text still goes through the built-in language-neutral chunking). The pluggable families *inside* a language — the analyzer, the reading resolver — default to `auto`, and `auto` is **an ordinary adapter registered in the same registry**, not a special case in the orchestrator: it picks by the environment (the first engine on a preference chain that actually loads).

`Pipeline` exposes only the `analyzer` / `resolver` fields, because one language really does have several segmentation and reading implementations with accuracy-versus-size trade-offs for a caller to weigh. **Segmentation is not a field, and not a family of its own**: recognising a writing system is a property of the language, not a strategy like "jieba or HanLP", so it is a method on `LanguageFrontend` and travels with the rest of that language. A knob whose only correct value is derived from another field is not configuration; it is a second place for the same fact to be wrong. To add a language, follow these steps:

1. **Frontend**: implement the `LanguageFrontend` protocol's two methods and register it in `frontend.language_frontend_registry` under the language subtag.
    - `segment(block, ctx)` is the language's **lexical policy**: cut the raw text into typed `Segment`s, tagging your own prose with your own type (Japanese folds kana *and* kanji into one `ja_text` run, because readings and particles resolve across that boundary). Do not rewrite the language-neutral half: `frontend.segmentation.segment_text(text, base_offset, categorize=...)` already handles protected regions (`$...$` math islands, IPA transcriptions), digit runs, Latin, Greek and punctuation — pass it a character classifier of your own, which is usually the few lines of "my script is mine, everything else goes to `char_category`" (`frontend/ja/_chars.py` is the worked example). A language the built-in classification already covers delegates straight to `frontend.segmentation.segment`, as Chinese does.
    - `process(surface, base, ctx)` tokenizes a run of the language's prose, annotates its reading, and turns it into inline IR nodes.
    - Declare which `Segment` types are this language's prose via `prose_types` (Chinese `{"hanzi_text"}`, Japanese `{"ja_text"}`). The Pipeline routes a segment back to `process` by that declaration, so the segment type stays "writing-system accurate" while routing stays "by language" — and since those types come out of the same object's `segment`, the two halves cannot disagree about the name (they could when the segmenter was a separate registration). The Chinese implementation `_ZhFrontend` is the worked example: it wires the zh analyzer and the pinyin resolver together.
    - Two **optional** declarations decide whether the language shows up where a user picks an engine: `display_name` is the English name a listing prints, and `adapters` is a `{family: () -> list[str]}` mapping saying what can be chosen for this language per family (`"analyzer"` for the segmentation or morphological engine, `"resolver"` for a reading engine — a language whose readings come out of its analyzer has none). `brailix --list-analyzers` and a front-end's engine picker read them through `frontend.list_language_adapters`, so declaring them is what makes the language appear there, with no change on the reading side. Both are read with a fallback, so an implementation that omits them is still valid.
2. **Backend**: implement the `LanguageBackend` protocol — `translate_word` **and** `translate_date_marker` — translating prose nodes into cells by the language's braille rules, and register it in `backend.dispatch.language_backend_registry`. Both are required: the registry runs a runtime protocol check when it first resolves your adapter, so an implementation missing one is rejected at `get()`, not at registration. `translate_date_marker` owns both a marker's reading (年/月/日/号/时/分/秒 and their equivalents) and the orthographic rule for whether a joiner cell precedes it after a number — the language-neutral date skeleton in `backend.number` delegates every marker to it, so no date-marker rule may live outside a `LanguageBackend`. A language with no special date handling still supplies an explicit implementation; there is no inherited default, and returning the plain reading is a one-line body. Language-agnostic nodes (numbers, punctuation, Latin, math, music) keep going through the shared `_DISPATCH` table — leave them alone.
3. **Word-boundary rules (as needed)**: whether a blank cell lands between two adjacent inline nodes is the language's orthography (Chinese writes word-by-word, Japanese uses 分かち書き), not a backend braille rule. Implement a `BoundaryHandler` (takes the two neighbouring inline nodes, returns whether to insert a blank cell) and register it in `brailix.frontend.boundary_registry` under the language subtag; the zh and ja handlers are the worked examples.
4. **Resources and profile**: put the language's braille rule tables under `resources/<language>/`; the shared resources (number sign, Latin, Greek, music) are already reusable at the top level. Write a profile JSON whose `language` points at the new language and whose `tables` point at those resources. A profile's `tables.<language subtag>` group is the **generic language table slot**: the loader maps it into `BrailleProfile.lang_tables[<subtag>]` and the backend reads it via `profile.lang_table(lang, name)` (for example `lang_tables["ja"]["kana"]`) — a new language's tables need no new field on the profile dataclass.

The existing IR node set suffices. The language-neutral `reading` field on `Word` (a phonetic annotation that works equally for Hanyu Pinyin and Japanese kana) is enough to carry an ideographic or a phonetic language, and a date marker's reading is just as neutral, riding on the `DateComponent.reading` value object; this is the "the IR's existing nodes are enough, only generalize the front and back ends" point in action. A single character is not a node type of its own — it is a one-character `Word`.

**The line between infrastructure and implementation.** All four seams above are registration seams, and the orchestrator stays language-agnostic — adding a language is purely additive. The *shared built-in pieces* are still tuned for Chinese: `char_category` singles out Han characters, and the date matcher in the normalization pass knows 年 / 月 / 日. The first is a **parameter** — a new language passes its own classifier and is past it. The second is part of a fixed pass today; if another structural convention genuinely turns up, the place for it is a hook on `LanguageFrontend` (already selected by language), not a second registry indexed by the same subtag. In other words, the infrastructure (picking the frontend by language plus the generic routing by `prose_types`) is already in place; what remains for any given language is writing its concrete recognition and rules on top of unchanged architecture. Japanese (kana braille) has landed through all four steps and is the second in-library language after Chinese.

---

## 8. Error recovery and proofreading

### 8.1 Three run modes

- `strict` — raise on any unrecognized structure (for textbook publishing).
- `normal` — recover as much as possible and emit warnings (the default).
- `lenient` — emit as much as possible, falling back to unknown tokens (for experiments / trial translation).

### 8.2 Warning format

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

### 8.3 Proofreading friendliness

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

`text` is the proofreading coordinate system: blocks are joined with newlines between them, and **inside a table** it is the *row's* display text — the cell texts joined by **two spaces** (the same ruler as the row-local coordinates of §4.4, i.e. `FrontendDriver._TABLE_CELL_GAP`), so for any span-carrying node in `ir`, any braille cell's `source_span`, and any warning's span, slicing with `text[span]` cuts out exactly that node's own surface. The table `text` was at one point joined with `" | "` for display — a separator no span ever accounted for, under which the consumption pattern of §8.3 would mis-align whole columns — consistency won, and it was changed back to two spaces.

---

## 9. The Pipeline API

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
### 9.1 Public entry points and the implementation boundary

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

- `Pipeline.parse_text(text)` / `Pipeline.parse_file(path)` return an **unpopulated** `DocumentIR`: the blocks carry their raw `text`, and the content fields (a leaf block's `inlines`, an embedded block's `tree`) are filled once `translate_document` / `translate_block` runs the frontend over them. That is what they are for — a caller doing incremental compilation wants the structure *before* the frontend runs, to compile block by block (and so does a proofreading tree or a structural check).

Rendering stays **deferred** throughout: the compiling entry points return a result object, and concrete formats come from `result.render(name)` on demand.

When the Pipeline processes a multi-block document it follows these rules:

- The `text` of `Heading` / `Paragraph` / `Quote` / `Footnote` / `ImageAlt` / `ListItem` / `TableCell` goes through the language frontend, filling `inlines` (nodes such as Word / Space / Number / ...).
- The `text` of `MathBlock` / `CodeBlock` takes a dedicated path — the Pipeline **pre-fills** their content during block population (`FrontendDriver.populate_block` dispatching to the per-kind handler in `_populate`). A `MathBlock` goes through the **math frontend** (`brailix.frontend.parse_math_tree`) to parse LaTeX/MathML, and the normalized MathML tree lands on the block itself (`EmbeddedBlock.tree`, §4.2); on parse failure it raises a `MATH_BLOCK_PARSE_FAILED` warning and leaves the block treeless, after which the backend's `MATH_NO_IR` path emits one unknown cell per source character to preserve the layout placeholder. A `CodeBlock` wraps its `text` in **one** `CodeInline` in `inlines`, which the punct backend emits cell by cell. The point: the backend only ever sees a block whose content is already filled, and it consumes the IR forward-only.
- At render time `renderer/layout` decides indentation and blank lines by `block_type`; level-1 headings are centered, deeper headings are left-aligned, and `code_block` / `table_row` / `table` are emitted verbatim.
- `translate_document` compiles the `DocumentIR` **in place** — this is a deliberate contract, not an implementation detail. The caller owns the source-side fields (`text`, block types, document structure); the compiler owns the derived fields it fills in (`inlines` / `tree`, spans, the pipeline-fingerprint stamp on populated blocks). Compiling in place is what makes incremental re-translation cheap: an unchanged, same-configuration block skips the frontend on the next pass, while an edited `text` or a differently-configured pipeline drops and rebuilds what it filled. Hand the method a document you own; deep-copy first if you must keep a pristine original.

If you need custom block boundaries (for example, preserving soft line breaks), build the `DocumentIR(blocks=[...])` yourself, hand it to `Pipeline.translate_document(doc)`, and render with `result.render("layout")` if you want it laid out: hand-built blocks are used as they are (see the population contract above), so where the blocks are cut is entirely yours to decide. There is no need to step around the supported entry point into the backend's or the renderer's deep modules — the top-level policy calls those internal and free to move, so a recipe that sends you there is asking you to depend on something nobody promised.

---

<a id="arch-testing"></a>
## 10. Testing strategy

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

## 11. Implementation roadmap (recommended order)

Walk the stages in this order and each one independently delivers something demoable.

| Stage | Content | Deliverable | Status |
|---|---|---|---|
| **P0** | core/ + ir/inline + ir/document + WarningCollector | the type skeleton | done (121 tests, 99% cov) |
| **P1** | segment + normalize + zh_analyzer (HanLP) + pinyin_resolver (g2pW) | Chinese input → pinyin-annotated IR | done (245 tests, 98% cov) |
| **P2** | backend/zh + backend/punct + backend/number + renderer/unicode + the cn_current profile | the first runnable braille output | done (512 tests, 98% cov) |
| **P3** | math/adapters/latex (latex2mathml) + math/adapters/mathml + math/normalizer | LaTeX / MathML → normalized MathML tree | done |
| **P4** | backend/math (the state machine) | math braille | done |
| **P4.5** | letter resources split out: `resources/latin/letters.json` + `resources/greek/letters.json` (neutral tables carrying no context prefixes); `BrailleProfile.letter(ch)` composes `structures.letter_prefix.*` with the letter on the fly (the old name `math_identifier` is gone) | the alphabet decoupled from math symbols — one letter dataset shared by math / number / the future LatinBraille | done |
| **R1–R5** | delete the MathIR layer (MathML itself as the IR), rewrite the resource JSON (entity names / roles / nested structures / per-subsystem feature groups), rewrite the loader, rewrite the backend, close out the documentation | the math path closed out | done (1008 tests) |
| **R5+** | boundary tail-cleanup: `features.math.number_sign` independent of zh; `backend/latin.py` split out (Latin no longer squatting inside punct); `profile.letter(ch)` neutral naming (the old name `math_identifier` deleted) | cross-layer / cross-namespace references put right | done (1034 tests) |
| **P5** | renderer/brf + renderer/layout (wrapping, page width, the heading_level centering policy) | publication-grade output | done |
| **P6** | DocumentIR completion: heading / list / table / quote / footnote + the Markdown input adapter + Pipeline.translate_document + math and code no longer polluting children | real documents | done |
| **P7** | the LatinBraille backend: V2 minimal (the simplest form of Current Chinese Braille) has landed in `backend/latin.py` — one latin_lower/upper prefix at the start of a word plus bare cells for the rest; within a consecutive English run, later lowercase words omit the latin_lower sign (the sign marks "letters start"; once English is open it is redundant; the caps sign still carries case per word and re-opens after Chinese interrupts the run; run state is maintained by `block._translate_inlines` via `latin.english_run_role`); an all-caps word (2+ letters) doubles the caps sign ⠠⠠ (`backend/latin.py`'s `_emit_all_caps_word`; formerly split into `backend/_letters.py` and shared with math identifiers, folded back once the second consumer was gone); optional future: contractions / full UEB support + finer code_inline handling | mixed Chinese-English | partial: V2 minimal done, full UEB pending |
| **CLI** | the command-line interface `brailix/cli.py`: `brailix` / `python -m brailix`, argparse + registry-enumerated choices + stdin/UTF-8 + the exit-code contract (0/1/2) | usable from a terminal | done (2026-06-07) |
| **P8** | the proofreading tool (HTML preview + revision write-back) | closing the loop | pending |

> The CLI was once numbered together with the proofreading tool under one P8, which made the stage numbering contradict itself; the CLI is now its own landed row, and P8 names the proofreading tool only.

**MVP = P0 + P1 + P2.** Run the whole pipeline on plain Chinese paragraphs first, then add math and document structure.

---

<a id="arch-boundaries"></a>
## 12. Component responsibilities

These are the invariants that keep each component swappable — each does exactly its own job:

- The **normalization pass**'s only reading-related job is the **fixed** readings of structural markers (year → nián, month → yuè, day → rì), written straight onto `DateComponent.reading`; all polyphone disambiguation belongs to the PinyinResolver (see `_MARKER_PINYIN` in `frontend/normalization.py`).
- The **ZhAnalyzer** handles only Chinese word segmentation + POS.
- The **PinyinResolver**'s sole effect is filling the `pinyin` field; token types and boundaries are preserved.
- The **MathParser** (adapter + normalizer) emits only a MathML tree.
- The **Backend** consumes IR forward-only: it reads the content the Pipeline pre-filled (a leaf block's `inlines` — code → `CodeInline`; an embedded block's `tree` — math frontend → MathML; see §9.1) and translates it — segmentation and language selection already happened upstream. It never re-segments, never re-detects the language, and never calls back into the frontend. **One controlled seam**: music `<words>` / embedded lyrics and the Chinese inside chemical-reaction conditions need their embedded prose rendered to braille, so the Backend consumes a callable the `Pipeline` injects into `BackendContext.options` implementing the `InlineTextTranslator` protocol (read via `BackendContext.inline_text_translator()`, key constant `INLINE_TEXT_TRANSLATOR_KEY`). That is dependency injection, so the Backend stays importable and unit-testable on its own; with nothing injected, the handler emits a warning plus a placeholder marker.
- The **Renderer** is the presentation and output layer: it consumes `BrailleIR` (or a `TactileRaster`) and owns layout — line wrapping, indentation, pagination — plus encoding into Unicode braille, BRF bytes, dot matrices, and image/print formats. What keeps it swappable is what it must **not** do: it never makes a translation decision and never reads the source language — everything it needs is already in the cells.
- The tactile-graphics vertical holds the same lines: a **GraphicSourceAdapter** emits only an SVG string; the **tactile backend** consumes the normalized SVG tree (the graphics IR) and never imports the frontend — a graphic's `<text>` labels are translated through an injected `LabelTranslator` callable, the same DI seam as `InlineTextTranslator`; the **tactile renderers** consume only a `TactileRaster`. External `<image href>` assets resolve in the tactile backend at rasterize time — the sanctioned exception spelled out as payload rule 4 in §1.
- The math subsystem's further lines: the MathML tree carries no dots and no profile keys, the Profile JSON is not a rules DSL, and the math backend never re-parses the source formula.
- **The compilation path retains no compilation results; the caller owns them.** That is a claim about the *path*, not about the whole library, and the line between the two matters.
  - **The path really is stateless.** The `Pipeline` keeps no per-document or per-call result cache between calls: `CompiledBlock` and `tree_subcache` are handed back for the caller to manage ("Pipeline produces these but does not keep a cache itself"), and a block's compile provenance (`Block.frontend_fingerprint` — which configuration populated its `children`) rides on the caller's own IR object, stamped on the `Block` itself. The corollary: never move **compilation results or per-block metadata** into a process-level container (a global block cache, a side table keyed by IR object identity) to "clean up" the IR — that is what would make "hand the same IR to two front-ends and compile concurrently" unsafe. With that rule in force the only home for per-block provenance is the block itself; the field is in-memory-only and excluded from serialization, equality, and the structure key, so it never pollutes IR semantics.
  - **The library side does hold process-scoped mutable configuration, deliberately.** The adapter registries' loaders / instance caches / generation counters, the renderer registry, `core.models.asset_registry`'s asset table, and the fingerprint module's per-instance resolver-token table. These are the **assembly surface** — what is registered, what a name resolves to — not compilation output, and process scope is where they belong: every Pipeline in one process should see the same adapters. Model policy is not on this list: adapters only ever *check* availability (an absent model raises `ModelNotInstalledError` and the `auto` chain degrades to the next engine); fetching a model is exclusively a front-end download manager's job — there is no download switch and no download path in the library.
  - **Concurrency therefore splits in two.** A single compile's run state (the `CompilationSession`'s collector, contexts, and tree pools) is isolated, so concurrent compiles do not interfere. Every assembly surface named above carries its own lock, so registering at runtime while other threads compile is safe — but the change is **globally visible** by design: it advances a generation and therefore the fingerprint of every live Pipeline. That is deliberate conservative invalidation, not isolation. "Internally locked" is honoured one by one, not as a summary: `core/registry.py`'s `Registry` keeps one `RLock` over the loaders / instance cache / extras / generation (only the cache-hit fast path of `get` goes lock-free, because a single `dict.get` is atomic under the GIL); `asset_registry` likewise holds one `RLock`, and `all_assets()` takes a stable snapshot inside it — asset registration looks like a one-time import-time action, but adapter modules are imported lazily by their registries, in whichever thread first resolves them, so a front-end refreshing its model list and a first HanLP compile really can race, and "registration only happens at startup" is not a premise to lean on. The GIL is not relied on here: a single `dict` read-write is indeed atomic under CPython, but that is an implementation detail of one interpreter build — the free-threading build (PEP 703) does not provide it — and a composable guarantee belongs in the contract, not in a bet on the implementation. The one deliberate lock-free site is the fingerprint module's per-resolver token table: two threads racing a resolver's first identity read each mint a token and the later write wins, at the cost of one spurious cache invalidation, never a wrong hit (the trade-off is written down in the `_RESOLVER_TOKENS` comment in `_fingerprint.py`).

Keeping each component to its own job is what lets any one of them be swapped or rewritten in isolation. Break any one of these lines and the next upgrade turns into a full-stack rewrite.

---

## 13. Technology choices (initial version)

The library core carries **zero third-party parsing dependencies**. Every concrete implementation ships as an adapter, installed on demand through extras (§7.4). The table below is the first batch of adapters delivered with the library — there is no "default", only "whichever the profile selects".

| Subsystem | adapters shipped | normalized mediator | recommended to start |
|---|---|---|---|
| Chinese segmentation | `auto` (picks by environment) / `char` (pure-stdlib fallback) / `jieba` / `thulac` / `hanlp` | `ChineseToken` | `hanlp` (accuracy first) |
| Pinyin | `auto` / `null` / `pypinyin` / `g2pm` / `g2pw` | pinyin annotation (numeric tones) | `g2pw` (deep polyphone model) |
| Japanese morphological analysis | `auto` / `kana` (pure stdlib, direct kana reading) / `janome` / `fugashi` (UniDic) / `sudachi` | reading annotation (kana) | `janome` (pure Python, offline) |
| Math parsing | `latex2mathml` (LaTeX) / passthrough (MathML) / OMML · MTEF · EqField · chem · script_cluster (all pure stdlib; Word / mhchem / Word-native formatting) | **MathML (`ET.Element`)** | start with LaTeX + MathML + Word (`docx`) |
| Music parsing | passthrough (MusicXML) / `.mxl` (zip unpack) / `partitura` (MIDI) / `abc-xml-converter` (ABC) | **MusicXML tree** | MusicXML / `.mxl` |
| Graphics parsing | passthrough (SVG) / primitives spec / figure generators (all pure stdlib) / image reference (`pillow`; full external-SVG rendering adds `resvg-py`) | **SVG tree (`ET.Element`)** | SVG / primitives |
| Document input | plain text / Markdown (a pure-stdlib subset) / `python-docx` + `olefile` (docx math) / score files (`.musicxml` / `.mxl`) | `DocumentIR` | enable per scenario |
| XML processing | stdlib `xml.etree` (math / music tree walking); `lxml` only in the `docx` extra | — | — |
| Configuration | JSON + `dataclass` (no pydantic) | — | — |
| CLI | `argparse` (standard library; landed) | — | — |
| Testing | `pytest` + golden files | — | — |

> The extension boundary of this architecture: **whenever a third-party library turns out to be a poor fit, only its adapter is swapped, and nothing downstream moves.** That is why math goes through MathML, segmentation through ChineseToken, and documents through DocumentIR — the normalized mediator format is the real API.

Python version requirement: `>=3.13` (the code uses `match` and modern type syntax).

---

<a id="arch-summary"></a>
## 14. Summary

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
