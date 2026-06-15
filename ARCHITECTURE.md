<!-- brailix architecture overview (English). The canonical version is in Chinese
     and kept in sync by hand; this public overview may differ in structure. -->

# brailix Architecture

## 1. What brailix is

`brailix` is a **braille compiler**: it takes text or documents from any source, runs them through frontend structural analysis, a unified intermediate representation (IR), and a pluggable braille backend, and finally emits Unicode Braille, BRF, a dot array, or a laid-out braille page.

**Scope.** `brailix` is exactly the *compilation path* — text → IR → braille. A generic `Pipeline.translate_block(ir_transformer=...)` hook lets a front-end insert its own IR transform between the frontend and the backend, so a CLI, a server, a textbook-publishing system, or an editing UI can build its own features on top of the compiler core. That keeps `brailix` usable as a standalone library.

Design goals:

- **Pluggable** — the tokenizer, pinyin engine, math parser, braille rules, and output format are all replaceable.
- **Profile-driven** — the same IR can be rendered by different braille standards (mainland Chinese schemes, UEB, Nemeth, textbook-specific).
- **Traceable** — every braille cell maps back to a source span, which makes human proofreading easy.
- **Structure-preserving** — numbers, formulae, and English each travel their own track through the frontend, keeping their native structure.

Requirements: Python `>=3.13` (the code uses `match` and modern type syntax).

---

## 2. Two ideas the whole design rests on

Everything below is an application of two decisions.

### 2.1 Normalized mediators and adapters

> For each subsystem that has a choice of external library, `brailix` defines its own **normalized mediator format** and plugs the external tools in through **adapters**, so the library stays independent of any one third-party implementation.

Each such subsystem is built the same three-part way: an adapter converts some external input into the mediator format, and every downstream consumer reads only the mediator.

| Subsystem | normalized mediator | what downstream sees |
|---|---|---|
| Chinese segmentation | `ChineseToken` | PinyinResolver, IRBuilder |
| Pinyin | pinyin annotation (numeric tones) | Backend |
| Math parsing | **MathML (`ET.Element`)** | MathBraille backend |
| Music parsing | **MusicXML tree (`ET.Element`)** | MusicBraille backend |
| Document input | `DocumentIR` | Frontend |
| Braille output | `BrailleIR` | Renderer |

Whichever adapter you pick, downstream only ever sees the mediator format, so **swapping an adapter leaves every line of downstream code untouched.** The same property is what makes each layer testable on its own: feed a fixed mediator value in, assert on the mediator value out.

### 2.2 Source-span traceability

Every `BrailleCell` carries the `source_span` it was produced from. That single field is what makes the output debuggable, lets the renderer wrap lines without losing provenance, and powers the proofreading system (§10): a tool can map any braille cell back to the exact source characters behind it.

These two ideas — *isolate behind a mediator* and *keep provenance on every cell* — are the criteria the rest of the architecture is judged against.

---

## 3. The pipeline

The compiler is a stack of layers. The Profile and its resource tables sit alongside the whole stack, supplying the rules and dot tables that the backend and renderer read.

```
┌─────────────────────────────────────────────────────┐
│  Input Layer       many sources → one Document        │
├─────────────────────────────────────────────────────┤
│  Frontend Layer    text → structured IR               │
│  ├─ Segmenter      block / inline / special regions   │
│  ├─ Normalizer     tag numbers / dates / units / ...  │
│  ├─ ZhAnalyzer     Chinese segmentation + POS         │
│  ├─ PinyinResolver pinyin + polyphone disambiguation  │
│  ├─ MathParser     source → MathML tree (= IR)        │
│  └─ LatinAnalyzer  English / acronyms / foreign runs  │
├─────────────────────────────────────────────────────┤
│  IR Layer          DocumentIR / InlineIR /            │
│                    MathML / BrailleIR                 │
├─────────────────────────────────────────────────────┤
│  Backend Layer     IR → BrailleIR                     │
│  ├─ Dispatcher     dispatch by node type              │
│  ├─ ZhBraille      Chinese braille                    │
│  ├─ NumberBraille  numbers / dates / quantities       │
│  ├─ MathBraille    math braille (also a state machine)│
│  ├─ LatinBraille   English / foreign                  │
│  └─ PunctBraille   punctuation                        │
├─────────────────────────────────────────────────────┤
│  Renderer Layer    BrailleIR → output format          │
│  ├─ Unicode Braille │ BRF │ Cells │ HTML preview      │
│  └─ Layout          line breaks / indent / pagination │
└─────────────────────────────────────────────────────┘
          ↑                                    ↑
          └────────── Profile / Resources ─────┘
```

Each layer answers exactly one question:

| Layer | The one thing it decides |
|---|---|
| Frontend | what each piece of input *is* |
| IR | how that meaning is structured |
| Backend | how the rules write it in braille |
| Renderer | what bytes it becomes |

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
│   ├── pipeline/             # end-to-end entry (translate_text / translate_document / translate_block)
│   ├── core/                 # shared types, contexts, errors, config loading, registries
│   │   ├── context.py        # FrontendContext / BackendContext / MathContext / MusicContext
│   │   ├── errors.py         # ParseError / WarningCollector / RunMode
│   │   ├── span.py           # Span utilities, source-position tracking for IR nodes
│   │   ├── registry.py       # generic name→loader registry (lazy load + MissingExtraError)
│   │   ├── protocols.py      # Segmenter / Analyzer / Resolver / Adapter / Backend / Renderer
│   │   ├── defaults.py / dispatch.py
│   │   ├── config/           # profile loaders
│   │   │   ├── profile.py    # BrailleProfile
│   │   │   ├── validator.py / zh_ncb_tables.py
│   │   │   └── loader/       # letters / math / music / numbers / punct / zh / _refs
│   │   └── models/           # asset_registry / paths (frozen detection)
│   ├── input/                # document input adapters (dispatched by extension)
│   │   ├── plain.py / markdown.py   # markdown is a pure-stdlib reader (no extra)
│   │   ├── docx/             # .docx/.docm package (__init__ + _blocks + _ole + _xml; incl. OMML / MTEF / EqField math extraction)
│   │   └── music_xml.py      # .musicxml / .xml / .mxl direct; .mid/.midi/.abc via source adapters
│   ├── frontend/             # text → structured IR
│   │   ├── segment.py        # block segmentation + inline-region detection
│   │   ├── normalize.py      # tag numbers / dates / units / percent signs
│   │   ├── _xml.py
│   │   ├── zh/               # Chinese-specific (language folder)
│   │   │   ├── __init__.py        # umbrella: re-exports the analyzer's public entry points
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
│   │   │   └── adapters/         # latex / mathml / omml / mtef / eq_field / chem
│   │   └── music/          # source → MusicXML tree (= IR)
│   │       ├── normalizer.py / registry.py  # music_source_registry
│   │       └── adapters/         # musicxml / mxl / midi / abc / plain
│   ├── ir/
│   │   ├── document.py       # DocumentIR: block level (incl. MathBlock / CodeBlock / ScoreBlock ...)
│   │   ├── inline.py         # InlineIR: inline tokens (incl. MathInline.math: ET.Element)
│   │   └── braille.py        # BrailleIR: cell sequence
│   ├── backend/              # IR → BrailleIR
│   │   ├── dispatch.py       # dispatch by node type; prose nodes then pick a LanguageBackend by profile.language
│   │   ├── number.py         # language-agnostic translator (numbers / dates / percent / quantities)
│   │   ├── latin.py          # Latin backend (standalone, separate from punct)
│   │   ├── punct.py
│   │   ├── block.py          # heading/list/table block-level translation
│   │   ├── zh/               # Chinese-specific (language folder)
│   │   │   ├── __init__.py        # translate_word / translate_hanzi_char
│   │   │   ├── tone/              # tone policy (basic / ncb_omission)
│   │   │   └── pinyin_parser.py   # pinyin syllable → (initial, final, tone)
│   │   ├── ja/               # Japanese kana → cells (LanguageBackend)
│   │   ├── math/            # math braille state machine (chem / context / dispatch / handlers / utils)
│   │   └── music/          # music braille (handlers/ split into files by BANA chapter)
│   ├── renderer/            # BrailleIR → output format
│   │   ├── unicode_braille.py / brf.py / cells.py
│   │   ├── layout.py        # line breaks / indent / pagination
│   │   └── music_layout.py / _page_digits.py
│   ├── profiles/
│   │   ├── cn_current.json   # Current Chinese Braille (default)
│   │   ├── cn_ncb.json       # National Common Braille
│   │   └── ja_current.json   # Japanese kana braille
│   └── resources/            # braille tables: shared ones at the top, region/scheme-specific under <region>/<scheme>/
│       ├── cells.json        # globally named cell pool (shared)
│       ├── numbers.json      # numbers: number sign + a–j (shared, used worldwide)
│       ├── latin/ / greek/   # neutral alphabets (shared, scheme/language-agnostic)
│       ├── music/            # music resources (BANA 2015 tables + instruments/ + vocal/, international)
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

Four IRs, from coarse to fine. The first three describe the document; the last is the braille result.

### 5.1 DocumentIR (block level)

```json
{
  "version": "1.0",
  "type": "document",
  "metadata": {"language": "zh-CN", "profile": "cn_current"},
  "blocks": [
    {"id": "b1", "type": "heading", "level": 1, "children": [...]},
    {"id": "b2", "type": "paragraph", "children": [...]}
  ]
}
```

Block types: `heading / paragraph / list / list_item / table / table_row / table_cell / quote / footnote / code_block / math_block / image_alt`.

### 5.2 InlineIR (inline tokens)

```json
{
  "type": "word",
  "surface": "重庆",
  "pinyin": "chong2 qing4",
  "confidence": 0.99,
  "span": [15, 17]
}
```

Inline token types:

```
word / hanzi_char / number / date / time / quantity / percent /
punct / latin_word / latin_acronym /
code_inline / math_inline / space / unknown
```

> `hanzi_char` is the single-character fallback when segmentation fails; `unknown` keeps the pipeline running on anything else.

### 5.3 Math and music as tree IRs

A math formula uses its **normalized MathML tree** as its IR directly, and a score uses its **normalized MusicXML tree** the same way. In both cases the mediator format (§2.1) *is* the IR, and the backend dispatches by element tag. The math tree looks like:

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

The full math and music subsystems are described in §7 and §8.

### 5.4 BrailleIR (cell sequence)

```python
@dataclass
class BrailleCell:
    dots: tuple[int, ...]      # e.g. (1, 2, 4)
    unicode: str | None = None # ⠋
    role: str | None = None    # 'number_sign' / 'zh_syllable' / 'math_op' ...
    source_span: tuple[int, int] | None = None
    source_text: str | None = None
```

```json
{
  "type": "braille_document",
  "blocks": [
    {"type": "braille_paragraph", "cells": [
      {"role": "zh_syllable", "source_text": "我", "dots": [/*...*/]},
      {"role": "number_sign", "dots": [3, 4, 5, 6]},
      {"role": "number",      "source_text": "2026", "dots": [/*...*/]}
    ]}
  ]
}
```

What BrailleIR buys you: easy debugging, traceability, line-wrapping, BRF generation, and proofreading.

---

## 6. Adapters: protocols, registries, and dependency groups

§2.1 stated the pattern; this section is its machinery. The library core ships with **zero third-party parsing dependencies** — every concrete parser is an adapter behind an optional extra.

### 6.1 Protocol definitions

```python
# core/protocols.py

class Segmenter(Protocol):
    name: str
    def segment(self, block: Block, ctx: FrontendContext | None) -> list[Segment]: ...

class ChineseAnalyzer(Protocol):
    name: str
    def analyze(self, text: str, ctx: FrontendContext | None) -> list[ChineseToken]: ...

class PinyinResolver(Protocol):
    name: str
    def resolve(self, tokens: list[ChineseToken], ctx: FrontendContext | None) -> list[ChineseToken]: ...

class MathSourceAdapter(Protocol):
    source: str  # latex / omml / mathml / chem / ...
    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str: ...

class MusicSourceAdapter(Protocol):
    source: str  # musicxml / mxl / midi / abc / plain
    def to_musicxml(self, src: str | bytes, ctx: MusicContext) -> str: ...

class LanguageBackend(Protocol):  # prose nodes (Word / HanziChar) → cells, per language
    def translate_word(self, node: Word, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]: ...
    def translate_hanzi_char(self, node: HanziChar, ctx: BackendContext, profile: BrailleProfile) -> list[BrailleCell]: ...

class Renderer(Protocol):
    name: str
    def render(self, bir: BrailleRenderable) -> Any: ...  # str / bytes / cells / ...
```

There is deliberately **no `Backend` protocol**. The backend isn't a pluggable-by-name adapter; it's a node-type dispatcher (§9.1), so it has no registry and no name→implementation contract. A new braille standard is added with a Profile JSON plus resources, not by registering a backend. Per-language *prose* translation is the one pluggable seam, and it goes through `LanguageBackend` above (§12).

### 6.2 Registries and on-demand loading

Each subsystem keeps a name→implementation registry, and **an adapter is imported only when it is first requested**, so a user who hasn't installed HanLP can still run a jieba-only path.

> **Exception: the input layer's format dispatch keeps no core registry.** Every other subsystem has its implementation chosen *by name from the profile* (`zh_analyzer: "hanlp"`), so the registry naturally lives in core. But *which adapter parses a given file* is decided by the file itself (extension / content), not a profile option — so core `brailix.input` ships the `parse_*` adapter functions plus a `parse_file` data table mapping a suffix set to a handler (adding a format is one more row), and the format-dispatch layer keeps no name→implementation registry. Which formats an application offers — file-dialog filters, fallback rules, third-party adapter discovery — is an application concern, wired through a registry the application builds over these functions. On-demand loading is achieved with in-function imports (`parse_docx` does `import docx` only when called). (Where input genuinely has competing implementations — handing `.mxl` / `.mid` / `.abc` to a music source adapter — it still uses `music_source_registry`, exactly as the registry pattern prescribes. Like the backend's node-type dispatcher in §6.1, the format seam is a deliberate non-registry choice.)

```python
# frontend/zh/analyzer/registry.py
_REGISTRY: dict[str, Callable[[], ChineseAnalyzer]] = {}

def register(name: str, loader: Callable[[], ChineseAnalyzer]) -> None: ...
def get(name: str) -> ChineseAnalyzer: ...   # lazy load

# frontend/zh/analyzer/adapters/hanlp.py
def _load() -> ChineseAnalyzer:
    import hanlp  # imported only when actually used
    ...
register("hanlp", _load)
```

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
hanlp  = ["hanlp"]                             # transformer tokenizer (downloads a model)
thulac = ["thulac"]
g2pw   = ["g2pw"]                              # deep polyphone model (downloads a model)
g2pm   = ["g2pM", "numpy"]
latex  = ["latex2mathml"]                      # LaTeX → MathML
docx   = ["python-docx", "lxml", "olefile"]   # Word .docx / .docm (incl. OMML / MathType)
midi   = ["mido", "partitura"]                 # MIDI scores → MusicXML
abc    = ["abc-xml-converter"]                 # ABC scores → MusicXML
ja     = ["janome"]                            # light, offline Japanese
all    = [...]                                 # every tool + each language's default analyzer
```

```bash
pip install brailix[zh]                 # light, offline Chinese
pip install brailix[zh,latex]           # + LaTeX math
pip install brailix[hanlp,g2pw]         # accurate Chinese engines (download models)
```

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
| Document input | plain text / Markdown (pure-stdlib reader) / Word `.docx` / `.doc` (`python-docx` + `olefile`) / score files | enable per scenario |

### 6.5 Adding a tool is one file

Adding any external tool means writing one adapter file: a new tokenizer goes under `frontend/zh/analyzer/adapters/`, a new pinyin engine under `frontend/zh/pinyin/adapters/`, a new math source under `frontend/math/adapters/`, a new language's braille rules become a `LanguageBackend` module under `backend/` plus a profile (a new *standard* for an existing language is just a profile + resources, no code — see §9.3), and a new output format becomes a module under `renderer/`. **Not a single line of core code needs to change.**

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

### 7.2 Two stages

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

## 8. The music subsystem

The music path mirrors the math path exactly. A source — MusicXML, a compressed `.mxl`, MIDI, or ABC — goes through an adapter into a normalized **MusicXML tree** (`ET.Element`), which is the music IR. The MusicBraille backend dispatches by element tag and runs a contextual state machine implementing BANA 2015 braille music. The code lives in the frontend `frontend/music/`, the backend `backend/music/` (whose `handlers/` subpackage is split into files by BANA chapter), the resources `resources/music/`, and the input adapter `input/music_xml.py`. Because it reuses the same adapter-plus-mediator shape, adding a new score format is, again, one adapter file.

---

## 9. The backend

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

> Prose nodes (`word` / `hanzi_char`) are translated by the `LanguageBackend` for the profile's language — the `self.zh` above is just a Chinese stand-in, and the real dispatch picks an implementation by `profile.language` (see §12). All other nodes go through the shared dispatch table by type.

### 9.2 BackendContext

Controls global side effects (whether the number sign is still in force, whether we are in math mode, the current block type, and so on):

```python
@dataclass
class BackendContext:
    profile: BrailleProfile
    block_type: str           # paragraph / heading / table_cell ...
    inline_mode: str          # text / math / latin / code
    prev_node: IRNode | None
    cur_node:  IRNode | None
    nxt_node:  IRNode | None
    need_number_sign: bool = False
    need_capital_sign: bool = False
    math_depth: int = 0
    line_width: int | None = None
    page_width: int | None = None
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

A CLI is planned:

```bash
brailix translate input.md --profile cn_current --out out.brf
brailix translate input.txt --format unicode --proofread out.json
```

### 11.1 What the Pipeline does

The Pipeline offers two entry points:

- `Pipeline.translate_text(text)` wraps the input in a single `Paragraph` block.
- `Pipeline.translate_document(doc)` accepts a full `DocumentIR` and runs frontend + backend block by block. Combined with `brailix.input.parse_markdown(text)` it can consume Markdown text directly.

When the Pipeline processes a multi-block document it follows these rules:

- The `text` of `Heading` / `Paragraph` / `Quote` / `Footnote` / `ImageAlt` / `ListItem` / `TableCell` goes through the language frontend, producing `children` (inline nodes such as HanziChar / Word / Space / Number / ...).
- The `text` of `MathBlock` / `CodeBlock` takes a dedicated path — the Pipeline **pre-fills** their `children` in `_populate_block`. A `MathBlock` goes through the **math frontend** (`brailix.frontend.parse_math_tree`) to parse LaTeX/MathML and produce **one** `MathInline` holding the normalized MathML tree; on parse failure it raises a `MATH_BLOCK_PARSE_FAILED` warning and fills per-character `Unknown` nodes to preserve the layout placeholder. A `CodeBlock` wraps its `text` in **one** `CodeInline`, which the punct backend emits cell by cell. The point: the backend only ever sees a block whose `children` are already filled, and it consumes the IR forward-only.
- At render time `renderer/layout` decides indentation and blank lines by `block_type`; level-1 headings are centered, deeper headings are left-aligned, and `code_block` / `table_row` / `table` are emitted verbatim.

If you need custom block boundaries (for example, preserving soft line breaks), construct `DocumentIR(blocks=[...])` and call `backend.dispatch.translate_document` + `renderer/layout` directly; the Pipeline is just a convenience shell over that common composition.

---

## 12. Adding a language

§6.5 is about swapping one adapter in a single layer; this is the bigger step of making the whole pipeline support a new language (Japanese, Korean, and so on). The design goal is to keep the orchestrator (`Pipeline` and `backend.dispatch`) entirely language-agnostic: all four subsystems — segmentation, normalization, frontend, backend — pick their implementation by language, a new language is realized only by registering at these protocol seams plus adding resources, and the orchestrator contains no language-specific branch.

A profile's `language` field drives the whole chain; it takes the primary subtag before the hyphen (for example `ja-JP` → `ja`). Registered keys match that subtag, and the chain connects. Each subsystem's selection priority is: the adapter name passed explicitly to `Pipeline`, then the adapter registered for the language, then the built-in `default`. To add a language, follow these steps:

1. **Segmenter**: implement the `Segmenter` protocol, recognize the language's writing system and cut its prose into typed `Segment`s (for example, tag a Japanese kana run as `kana_text`), and register it in `frontend.segment.segmenter_registry` under the language subtag. The built-in `default` segmenter recognizes only Han characters (emitting `hanzi_text`) plus the shared categories (numbers, Latin, Greek, and so on), so a non-Han writing system plugs in at this step.
2. **Frontend**: implement the `LanguageFrontend` protocol's `process(surface, base, ctx)`, which segments a run of the language's prose, annotates its reading, and turns it into inline IR nodes; declare which `Segment` types it consumes via `prose_types` (Chinese is `{"hanzi_text"}`, Japanese might be `{"hanzi_text", "kana_text"}`), and register it in `frontend.language_frontend_registry`. The Pipeline dispatches by `prose_types`, so the segment type stays "writing-system accurate" while routing stays "by language." The Chinese implementation `_ZhFrontend` is the worked example: it wires the zh segmenter and the pinyin resolver together.
3. **Backend**: implement the `LanguageBackend` protocol's `translate_word` and `translate_hanzi_char`, translating prose nodes into cells by the language's braille rules, and register it in `backend.dispatch.language_backend_registry`. Language-agnostic nodes (numbers, punctuation, Latin, math, music) keep going through the shared `_DISPATCH` table — leave them alone.
4. **Word-boundary rules (as needed)**: whether a blank cell lands between two adjacent inline nodes is the language's orthography (Chinese writes word-by-word, Japanese uses 分かち書き), not a backend braille rule. Implement a `BoundaryHandler` (takes the two neighbouring inline nodes, returns whether to insert a blank cell) and register it in `brailix.frontend.boundary_registry` under the language subtag; the zh and ja handlers are the worked examples.
5. **Normalizer (as needed)**: the default normalizer carries Chinese structural rules (fixed readings for date markers like year/month/day). If the new language has its own structural conventions, implement the `Normalizer` protocol and register it in `frontend.normalize.normalizer_registry` under the language subtag; if not, reuse `default`.
6. **Resources and profile**: put the language's braille rule tables under `resources/<language>/`; the shared resources (number sign, Latin, Greek, music) are already reusable at the top level. Write a profile JSON whose `language` points at the new language and whose `tables` point at those resources. A profile's `tables.<language subtag>` group is the **generic language table slot**: the loader maps it into `BrailleProfile.lang_tables[<subtag>]` and the backend reads it via `profile.lang_table(lang, name)` (for example `lang_tables["ja"]["kana"]`) — a new language's tables need no new field on the profile dataclass.

The existing IR node set suffices. `Word`, `HanziChar`, and `HanziMarker`, plus the language-neutral `reading` field (a phonetic annotation that works equally for Hanyu Pinyin and Japanese kana), are enough to carry an ideographic or a phonetic language; this is the "the IR's existing nodes are enough, only generalize the front and back ends" point in action.

**The line between infrastructure and implementation.** All six seams above are registration seams, and the orchestrator stays language-agnostic — adding a language is purely additive. The *built-in implementations* are still tuned for Chinese: the `default` segmenter recognizes only Han characters, and the `default` normalizer understands only Chinese date markers. These are default implementations awaiting replacement — a new language overrides them by registering its own segmenter and normalizer. In other words, the infrastructure (each subsystem's language selection plus the generic routing by `prose_types`) is already in place; what remains for any given language is writing its concrete recognition and rules on top of unchanged architecture. Japanese (kana braille) has landed through all six steps and is the second in-library language after Chinese.

---

## 13. Testing strategy

Four layers, each runnable on its own.

| Layer | What it tests | Independent of |
|---|---|---|
| Frontend | type recognition, segmentation, pinyin, state machine | the Backend |
| MathParser | structural equivalence of LaTeX → MathML tree | the Backend |
| Backend | fixed IR → fixed BrailleIR | segmentation models (so model drift can't move the assertions) |
| Pipeline | end-to-end golden tests | — (uses human-proofread samples) |

The golden test set covers, at minimum, primary-school Chinese paragraphs; middle-school math with formulae; news text with numbers, dates, and foreign words; mixed Chinese and English; tables and lists; polyphone boundaries (重庆 / 银行 / 朝阳 / 长安); and formula boundaries (nested fractions, nested radicals, matrices, error recovery).

Run the golden suite on every rule change; **the diff must be reviewed by hand.**

---

## 14. Component responsibilities

These are the invariants that keep each component swappable — each does exactly its own job:

- The **Normalizer**'s only reading-related job is the **fixed** readings of structural markers (year → nián, month → yuè, day → rì), written straight onto `HanziMarker.reading`; all polyphone disambiguation belongs to the PinyinResolver (see `_MARKER_PINYIN` in `frontend/normalize.py`).
- The **ZhAnalyzer** handles only Chinese word segmentation + POS.
- The **PinyinResolver**'s sole effect is filling the `pinyin` field; token types and boundaries are preserved.
- The **MathParser** (adapter + normalizer) emits only a MathML tree.
- The **Backend** consumes IR forward-only: it reads the `children` the Pipeline pre-filled (math frontend → `MathInline`, code → `CodeInline`; see §11.1) and translates them — segmentation and language selection already happened upstream. **One controlled seam**: music `<words>` / embedded lyrics and the Chinese inside chemical-reaction conditions need their embedded prose rendered to braille, so the Backend consumes a callable the `Pipeline` injects into `BackendContext.options` implementing the `InlineTextTranslator` protocol (read via `BackendContext.inline_text_translator()`, key constant `INLINE_TEXT_TRANSLATOR_KEY`). That is dependency injection, so the Backend stays importable and unit-testable on its own; with nothing injected, the handler emits a warning plus a placeholder marker.
- The **Renderer**'s only job is encoding cells into bytes.

Keeping each component to its own job is what lets any one of them be swapped or rewritten in isolation.

---

## 15. Summary

`brailix` compiles a source document into braille in five moves: the frontend recognizes and structures the input; the IR holds that meaning in a unified form; the backend applies profile-driven braille rules; BrailleIR records the result as a traceable cell sequence; and the renderer encodes it as Unicode, BRF, or a laid-out page.

- Chinese is handled by segmentation, pinyin, and polyphone disambiguation.
- Numbers and dates stay structured and travel on their own track.
- Math and music each parse into a tree IR (MathML, MusicXML), and the backend dispatches by tag through a contextual state machine.
- The braille standard is a swappable profile.
- The output is traceable, proofreadable, and format-swappable.

The whole design holds to one test: **every layer can be replaced or tested on its own.**
