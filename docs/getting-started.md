# Getting started

## Requirements

brailix targets **Python 3.13 or newer**. The core package is pure-Python and has no third-party parser dependencies.

## Install

```bash
pip install brailix              # core: plain text, Markdown, MusicXML, SVG tactile graphics
pip install brailix[zh]          # Chinese: segmentation + pinyin (light, offline)
pip install brailix[zh,latex]    # + LaTeX math
pip install brailix[ja]          # Japanese: morphological analysis (kanji readings)
pip install brailix[hanlp,g2pw]  # accurate Chinese engines (download models)
pip install brailix[docx]        # Word .docx / .docm (incl. MathType / OMML)
```

Extras are grouped by language and by tool category — see the [README](../README.md) and [`pyproject.toml`](../pyproject.toml) for the full list (`zh`, `ja`, individual engines, `latex`, `docx`, `midi`, `abc`, `music`, `graphics`, `graphics-svg-raster`, and `all`). The `hanlp` and `g2pw` engines download their model weights on first use — HanLP's into a local `models/` directory that brailix points it at, g2pW's into its own library's cache; the `zh` pack (jieba plus pypinyin) is lightweight and works offline immediately.

## Your first translation

```python
from brailix import Pipeline

pipe = Pipeline(profile="cn_current")
result = pipe.translate_text("我在2026年5月17日去了重庆。")

print(result.render())        # a Unicode braille string
```

`Pipeline.translate_text` runs the whole pipeline and returns a [`TranslationResult`](https://brailix.github.io/brailix/#TranslationResult). Nothing is rendered until you ask for it, so you only pay for the output formats you use.

## Reading the result

```python
result.render()              # default renderer (Unicode braille)
result.render("brf")         # choose a renderer by name: unicode / brf / cells / layout
result.ir                    # the DocumentIR (what the frontend produced)
result.braille_ir            # the BrailleDocument (the cell sequence)
result.warnings              # a WarningCollector with any diagnostics
result.proofread_json()      # a JSON-ready dict mapping source text to braille cells
```

Because every braille cell records the source span it came from, `proofread_json()` gives a downstream tool everything it needs to highlight a cell, jump back to its source, or batch-correct a reading.

## From the command line

Installing brailix also gives you a `brailix` command, so the first translation works from a terminal without writing any Python:

```bash
brailix "我在2026年5月17日去了重庆。" -p cn_current      # Unicode braille
brailix --file lesson.md --width 32 -p cn_current       # wrap a Markdown file at 32 cells
brailix "123" --to brf --output out.brf -p cn_current   # NABCC bytes for an embosser
```

`-p` / `--profile` picks the braille standard, exactly as the `Pipeline` constructor does, and the command line requires it for the same reason: the choice belongs to you, not to a default. `brailix --list-profiles` prints the names.

Input can be a positional string, a `--file` (dispatched by suffix), or piped standard input — one of the three, not two at once; output can be any renderer, optionally wrapped and paginated. The full reference is in the [command-line guide](cli.md).

## Translating documents and files

For Markdown, Word, or MusicXML sources, parse them into a document first or let the Pipeline do it for you:

```python
# A file, dispatched by suffix (.md, .docx, .musicxml, ...).
result = pipe.translate_file("lesson.md")

# Or build the DocumentIR yourself and translate it. The parser tags each
# block with the language and profile it was read for, so both are required.
from brailix.input import parse_markdown
doc = parse_markdown(
    "# 标题\n\n正文 $x^2$。",
    language="zh-CN",
    profile="cn_current",
)
result = pipe.translate_document(doc)
```

Word `.docx` / `.docm` support (including MathType and OMML formulae) needs the `docx` extra. See the [API reference](https://brailix.github.io/brailix/) for every entry point.

## Tactile graphics

Braille is not the only thing brailix writes. `translate_graphic` compiles a drawing into a **tactile raster** — a grid of raise levels carrying its own physical page size — which renders to a `.bmp` for an embosser, a `.png` or `.pdf` for a sighted reference, or a Unicode-braille preview you can read on a refreshable display:

```python
from brailix import translate_graphic

svg = (
    '<svg xmlns="http://www.w3.org/2000/svg" width="50mm" height="50mm" '
    'viewBox="0 0 100 100"><circle cx="50" cy="50" r="40"/></svg>'
)
figure = translate_graphic(svg, tactile_profile="generic")

figure.render("bmp")              # embossable bytes
figure.render("tactile_preview")  # a U+2800 readback of the page
```

A figure needs no braille standard, which is why this is a module-level function rather than a `Pipeline` method: `tactile_profile` names the page geometry (size, resolution, the minimum feature a device can raise) the way `profile` names the braille standard, and the result renders through the same registry the braille renderers live in. Pass `braille_profile` as well and any `<text>` label inside the drawing is translated into braille dots on the page.

Nothing above needs an extra: parsing SVG and writing BMP, PNG, and PDF are all standard library. What does need one is reading a picture *in* — `brailix[graphics]` decodes a raster image (PNG, JPEG, ...) into raise levels, and `brailix[graphics-svg-raster]` renders a complex external SVG (gradients, filters, `clipPath`) that the built-in geometry rasterizer does not cover.

## Choosing engines

The Chinese word-segmentation engine and the pinyin resolver are selected by name in the `Pipeline` constructor; the default `"auto"` picks the best engine you have installed:

```python
pipe = Pipeline(
    profile="cn_current",
    analyzer="hanlp",     # auto / char / jieba / thulac / hanlp
    resolver="g2pw",      # auto / null / pypinyin / g2pm / g2pw
)
```

If you install only the `zh` pack, `auto` resolves to jieba plus pypinyin. Installing `hanlp` and `g2pw` upgrades accuracy at the cost of a one-time model download.

## Japanese

Japanese uses the `ja_current` profile. Pure kana works with nothing extra installed; reading kanji needs a morphological analyzer — the reading drives the braille, the way pinyin does for Chinese.

```python
pipe = Pipeline(profile="ja_current")          # auto-selects an installed analyzer
print(pipe.translate_text("私は本を読む").render())
```

```bash
pip install brailix[ja]        # janome — light, pure-Python, bundles its dictionary
pip install brailix[fugashi]   # MeCab + UniDic — best pronunciation-form readings
pip install brailix[sudachi]   # SudachiPy
```

The analyzer is selected by name like the Chinese one (`analyzer="janome"` / `"fugashi"` / `"sudachi"`, or `"kana"` for the dependency-free pure-kana path). It fills each word's pronunciation-form reading — long vowels become the prolonged-sound mark, and the topic / object particles read correctly; the backend writes the kana cells, and word-spacing (分かち書き) is inserted from the analyzer's part-of-speech tags.

## Profiles and run modes

A **profile** is a braille standard plus its resource tables. Three ship today: `cn_current` (Current Chinese Braille), `cn_ncb` (National Common Braille), and `ja_current` (Japanese kana braille). Select one with the `profile` argument, which `Pipeline` requires — the choice of braille standard is always the caller's, and there is no built-in default. The command line says the same thing with `--profile`, equally required; see the [CLI guide](cli.md).

The **run mode** controls how strictly the pipeline reacts to input it cannot fully handle:

```python
pipe = Pipeline(profile="cn_current", mode="normal")   # strict / normal / lenient
```

- `strict` raises on any unrecognized structure (suited to publishing).
- `normal` recovers as much as possible and records warnings (the default).
- `lenient` emits as much as it can, falling back to unknown tokens (suited to experiments).

## Next steps

- The [command-line guide](cli.md) documents the `brailix` terminal command in full.
- The [API reference](https://brailix.github.io/brailix/) documents every public class and function, generated from the source.
- [Extending brailix](extending.md) shows how to add an engine, a format, a renderer, a profile, or a language.
