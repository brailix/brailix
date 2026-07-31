# Command-line interface

Installing brailix puts a `brailix` command on your `PATH`. It compiles text, Markdown, Word, and MusicXML into braille from a terminal, as a thin wrapper over the [`Pipeline`](https://brailix.github.io/brailix/#Pipeline) you would otherwise call from Python. Everything the command can do is also reachable as `python -m brailix`, which is handy when the script directory is not on your `PATH`.

```bash
brailix "我在重庆。" --profile cn_current        # Unicode braille to stdout
python -m brailix "我在重庆。" -p cn_current     # the same thing, module form
```

`--profile` (`-p`) names the braille standard and is **required** for every translation: brailix ships more than one, and which one a document should be written in is a decision only you can make. `brailix --list-profiles` prints the names, and the discovery flags below run without a profile. The examples on this page all pass one for that reason — leave it off and the command exits with a usage error rather than guessing.

## Choosing the input

The text to translate comes from exactly one of three places: the positional argument, the `--file` option, or standard input. Pass a positional string and it is translated; pass `--file` and the file is read; pass neither and the command reads piped standard input. Passing *both* a positional string and `--file` is a usage error — two inputs, no way to tell which one you meant.

```bash
brailix "123" -p cn_current                  # a positional string
brailix --file lesson.md -p cn_current       # a file, dispatched by its suffix
echo "正文 $x^2$。" | brailix -p cn_current    # piped standard input
```

A file is dispatched by its suffix exactly the way [`Pipeline.translate_file`](https://brailix.github.io/brailix/#Pipeline.translate_file) dispatches it: `.md` / `.markdown` as Markdown, `.docx` / `.docm` as Word, `.musicxml` / `.mxl` as a score, `.mid` / `.midi` as binary score sources converted to MusicXML when the file is read, `.abc` as a text score source kept raw and converted later by the frontend, and anything else as plain text. These formats need their optional extra installed (`brailix[docx]`, `brailix[midi]`, `brailix[abc]`). Word and MIDI report a missing extra as an error while reading; ABC, being converted a step later, reports it as a warning and translates the rest of the document.

A positional string or piped input is treated as plain text by default. Use `--in-format` to read it as Markdown or MusicXML instead:

```bash
echo "# 标题" | brailix --in-format markdown -p cn_current
cat score-fragment.txt | brailix --in-format musicxml -p cn_current
```

`--in-format` applies to the positional string and to standard input. A `--file` is dispatched by its suffix instead, so combining the two is a usage error; pipe the file in, as above, when you need to force a format for a file whose suffix does not say what it holds.

Piped input is decoded as UTF-8 regardless of the console code page, so Chinese and Japanese survive a pipe on every platform. Word and score files cannot be piped — they need a real path — so pass those with `--file`.

## Choosing the output

Two independent choices control the output: the **renderer** (`--to`) decides how each braille cell is encoded, and the **layout options** decide whether the result is wrapped and paginated.

```bash
brailix "123" -p cn_current                      # Unicode braille (default)
brailix "123" --to brf -p cn_current             # NABCC bytes, for an embosser
brailix "123" --to cells -p cn_current           # a JSON array of cell data
brailix "abc def ghij" --width 32 -p cn_current  # wrap Unicode braille at 32 cells
```

`--to` accepts any **braille** renderer the build provides (`brailix --list-renderers`):

| Renderer | Output | Use |
|---|---|---|
| `unicode` | a string of Unicode braille (default) | reading, copy-paste into an editor |
| `brf` | NABCC ASCII bytes | sending to an embosser or saving a `.brf` |
| `cells` | a JSON document of cell data (dots, role, source span) | feeding another tool |
| `layout` | laid-out Unicode braille | a page-ready transcript |

Those four are the whole list, and the emphasis above is the reason: brailix also ships tactile-graphics renderers (`bmp`, `png`, `pdf`, `tactile_preview`), which live in the same registry but encode a raised-dot raster rather than braille cells. This command takes no drawing as input, so it neither offers them nor lists them. Compiling a figure is `translate_graphic()` plus `GraphicResult.render()` from Python — see [Getting started](getting-started.md#tactile-graphics).

The layout options turn on line-wrapping, per-block indentation, and pagination:

- `--width N` wraps each line at `N` cells.
- `--page-height N` starts a new page every `N` lines.
- `--page-numbers` prints a page number on each page (it needs `--page-height`).

Passing any layout option turns the layout pass on for whichever encoding you chose, so `--to brf --width 40 --page-height 25` produces page-ready embosser bytes. `--to layout` is a shorthand for laid-out Unicode braille at the default width. The `cells` renderer is structural data and cannot be laid out.

By default the result goes to standard output. Use `--output` to write a file; text renderers are written as UTF-8 and BRF as binary, so the bytes are correct either way.

```bash
brailix --file lesson.md --to brf --width 40 --page-height 25 --output lesson.brf -p cn_current
```

## Translation options

The braille profile and the language engines are selected by name, exactly as in the [`Pipeline`](https://brailix.github.io/brailix/#Pipeline) constructor:

| Option | Meaning | Default |
|---|---|---|
| `--profile NAME` | braille standard plus its tables | none — required |
| `--analyzer NAME` | word-segmentation engine for the profile's language | `auto` |
| `--resolver NAME` | reading engine for the profile's language, where it has one | `auto` |
| `--mode MODE` | diagnostic strictness: `strict` / `normal` / `lenient` | `normal` |

`auto` picks the best engine you have installed and falls back to a dependency-free path, so a bare install translates without any extra. Install heavier engines for accuracy (`brailix[hanlp,g2pw]`); a name is valid as soon as it is listed by the discovery flags below, even before its package is present (selecting one whose package is missing reports which extra to install). For Japanese, choose the `ja_current` profile; the analyzer name then selects a Japanese engine (`janome` / `fugashi` / `sudachi`, or `kana` for the pure-kana path).

An engine belongs to one language, so `--analyzer` and `--resolver` accept the names the profile's own language offers — `--list-analyzers` shows which those are. A Japanese engine under a Chinese profile is a usage error rather than a run that fails halfway, and so is `--resolver` under a profile whose language has no separate reading engine (a Japanese reading comes out of its analyzer), where the option would otherwise have been accepted and then had no effect on the output.

```bash
brailix "重庆" --analyzer hanlp --resolver g2pw -p cn_current
brailix --profile ja_current "私は本を読む"
brailix --profile cn_ncb --file lesson.md
```

## Diagnostics

Translation warnings (an unreadable character, a low-confidence reading) are printed to standard error, one `[CODE] message` per line, so they never mix into the braille on standard output. Use `--quiet` to suppress them. `--mode strict` turns the first warning into an error and exits non-zero, which suits an automated publishing check.

## Discovery

These flags print what the installed build supports and exit:

```bash
brailix --list-profiles      # cn_current, cn_ncb, ja_current
brailix --list-analyzers     # segmentation engines, grouped by language
brailix --list-resolvers     # reading engines, grouped by language
brailix --list-renderers     # braille renderers, i.e. what --to accepts
brailix --version
```

The lists come straight from the core registries, so they always match what `--profile`, `--analyzer`, `--resolver`, and `--to` will accept. The engine listings are grouped by language and the groups come from the registry too — an engine name means a different thing per language (`auto` picks among the Chinese analyzers for Chinese and the Japanese ones for Japanese), and a language added by registration shows up on its own.

Discovery is also where you look *before* installing anything, so it never fails as a whole: if a language ships behind an optional package that is missing, that one language is reported on standard error with the extra to install, the rest of the listing still prints on standard output, and the exit code stays `0`.

## Exit codes

- `0` — success.
- `1` — a translation or input error (a missing file, an unreadable document, a missing extra, an unknown engine). A short message is printed to standard error; there is no traceback.
- `2` — a usage error: an unknown option or value, a missing `--profile`, or a combination with no single meaning — `--to cells --width 40`, a positional string together with `--file`, or `--in-format` together with `--file`.

## See also

- [Getting started](getting-started.md) — the same translations from Python.
- [API reference](https://brailix.github.io/brailix/) — the `Pipeline` and result objects the command wraps.
