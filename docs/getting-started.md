# Getting started

## Requirements

brailix targets **Python 3.13 or newer**. The core package is pure-Python and has no third-party parser dependencies.

## Install

```bash
pip install brailix              # core: plain text, Markdown, MusicXML
pip install brailix[zh]          # Chinese: segmentation + pinyin (light, offline)
pip install brailix[zh,latex]    # + LaTeX math
pip install brailix[ja]          # Japanese: morphological analysis (kanji readings)
pip install brailix[hanlp,g2pw]  # accurate Chinese engines (download models)
pip install brailix[docx]        # Word .docx / .docm (incl. MathType / OMML)
```

Extras are grouped by language and by tool category — see the [README](../README.md) and [`pyproject.toml`](../pyproject.toml) for the full list (`zh`, `ja`, individual engines, `latex`, `asciimath`, `markdown`, `docx`, `midi`, `abc`, `music`, and `all`). The `hanlp` and `g2pw` engines download their model weights on first use into a local `models/` directory; the `zh` pack (jieba plus pypinyin) is lightweight and works offline immediately.

## Your first translation

```python
from brailix import Pipeline

pipe = Pipeline(profile="cn_current")
result = pipe.translate_text("我在2026年5月17日去了重庆。")

print(result.render())        # a Unicode braille string
```

`Pipeline.translate_text` runs the whole pipeline and returns a [`TranslationResult`](api.md#translationresult). Nothing is rendered until you ask for it, so you only pay for the output formats you use.

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

## Translating documents and files

For Markdown, Word, or MusicXML sources, parse them into a document first or let the Pipeline do it for you:

```python
# A file, dispatched by suffix (.md, .docx, .musicxml, ...).
result = pipe.translate_file("lesson.md")

# Or build the DocumentIR yourself and translate it.
from brailix.input import parse_markdown
doc = parse_markdown("# 标题\n\n正文 $x^2$。")
result = pipe.translate_document(doc)
```

Word `.docx` / `.docm` support (including MathType and OMML formulae) needs the `docx` extra. See the [API reference](api.md) for every entry point.

## Choosing engines

The Chinese segmenter and pinyin resolver are selected by name in the `Pipeline` constructor; the default `"auto"` picks the best engine you have installed:

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

A **profile** is a braille standard plus its resource tables. Three ship today: `cn_current` (Current Chinese Braille, the default), `cn_ncb` (National Common Braille), and `ja_current` (Japanese kana braille). Select one with the `profile` argument.

The **run mode** controls how strictly the pipeline reacts to input it cannot fully handle:

```python
pipe = Pipeline(profile="cn_current", mode="normal")   # strict / normal / lenient
```

- `strict` raises on any unrecognized structure (suited to publishing).
- `normal` recovers as much as possible and records warnings (the default).
- `lenient` emits as much as it can, falling back to unknown tokens (suited to experiments).

## Next steps

- The [API reference](api.md) documents every public class and function.
- [Extending brailix](extending.md) shows how to add an engine, a format, a renderer, a profile, or a language.
