# Command-line interface

Installing brailix puts a `brailix` command on your `PATH`. It compiles text, Markdown, Word, and MusicXML into braille from a terminal, as a thin wrapper over the [`Pipeline`](api.md) you would otherwise call from Python. Everything the command can do is also reachable as `python -m brailix`, which is handy when the script directory is not on your `PATH`.

```bash
brailix "我在重庆。"                  # Unicode braille to stdout
python -m brailix "我在重庆。"        # the same thing, module form
```

## Choosing the input

The text to translate comes from one of three places, tried in this order: the positional argument, the `--file` option, then standard input. If you pass a positional string it always wins; if you pass `--file` the file is read; otherwise the command reads piped standard input.

```bash
brailix "123"                       # a positional string
brailix --file lesson.md            # a file, dispatched by its suffix
echo "正文 $x^2$。" | brailix         # piped standard input
```

A file is dispatched by its suffix exactly the way [`Pipeline.translate_file`](api.md) dispatches it: `.md` / `.markdown` as Markdown, `.docx` / `.docm` as Word, `.musicxml` / `.mxl` as a score, `.mid` / `.midi` / `.abc` as score sources converted to MusicXML, and anything else as plain text. Word and the score source formats need their optional extra installed (`brailix[docx]`, `brailix[midi]`, `brailix[abc]`); the command reports which one to add if it is missing.

A positional string or piped input is treated as plain text by default. Use `--in-format` to read it as Markdown or MusicXML instead:

```bash
echo "# 标题" | brailix --in-format markdown
brailix --in-format musicxml --file score-fragment.txt
```

Piped input is decoded as UTF-8 regardless of the console code page, so Chinese and Japanese survive a pipe on every platform. Word and score files cannot be piped — they need a real path — so pass those with `--file`.

## Choosing the output

Two independent choices control the output: the **renderer** (`--to`) decides how each braille cell is encoded, and the **layout options** decide whether the result is wrapped and paginated.

```bash
brailix "123"                       # Unicode braille (default)
brailix "123" --to brf              # NABCC bytes, for an embosser
brailix "123" --to cells            # a JSON array of cell data
brailix "abc def ghij" --width 32   # wrap Unicode braille at 32 cells
```

`--to` accepts any renderer the build provides (`brailix --list-renderers`):

| Renderer | Output | Use |
|---|---|---|
| `unicode` | a string of Unicode braille (default) | reading, copy-paste into an editor |
| `brf` | NABCC ASCII bytes | sending to an embosser or saving a `.brf` |
| `cells` | a JSON document of cell data (dots, role, source span) | feeding another tool |
| `layout` | laid-out Unicode braille | a page-ready transcript |

The layout options turn on line-wrapping, per-block indentation, and pagination:

- `--width N` wraps each line at `N` cells.
- `--page-height N` starts a new page every `N` lines.
- `--page-numbers` prints a page number on each page (it needs `--page-height`).

Passing any layout option turns the layout pass on for whichever encoding you chose, so `--to brf --width 40 --page-height 25` produces page-ready embosser bytes. `--to layout` is a shorthand for laid-out Unicode braille at the default width. The `cells` renderer is structural data and cannot be laid out.

By default the result goes to standard output. Use `--output` to write a file; text renderers are written as UTF-8 and BRF as binary, so the bytes are correct either way.

```bash
brailix --file lesson.md --to brf --width 40 --page-height 25 --output lesson.brf
```

## Translation options

The braille profile and the Chinese engines are selected by name, exactly as in the [`Pipeline`](api.md) constructor:

| Option | Meaning | Default |
|---|---|---|
| `--profile NAME` | braille standard plus its tables | `cn_current` |
| `--analyzer NAME` | word-segmentation engine | `auto` |
| `--resolver NAME` | pinyin resolver | `auto` |
| `--mode MODE` | diagnostic strictness: `strict` / `normal` / `lenient` | `normal` |

`auto` picks the best engine you have installed and falls back to a dependency-free path, so a bare install translates without any extra. Install heavier engines for accuracy (`brailix[hanlp,g2pw]`); a name is valid as soon as it is listed by the discovery flags below, even before its package is present (selecting one whose package is missing reports which extra to install). For Japanese, choose the `ja_current` profile; the analyzer name then selects a Japanese engine (`janome` / `fugashi` / `sudachi`, or `kana` for the pure-kana path).

```bash
brailix "重庆" --analyzer hanlp --resolver g2pw
brailix --profile ja_current "私は本を読む"
brailix --profile cn_ncb --file lesson.md
```

## Diagnostics

Translation warnings (an unreadable character, a low-confidence reading) are printed to standard error, one `[CODE] message` per line, so they never mix into the braille on standard output. Use `--quiet` to suppress them. `--mode strict` turns the first warning into an error and exits non-zero, which suits an automated publishing check.

## Discovery

These flags print what the installed build supports and exit:

```bash
brailix --list-profiles      # cn_current, cn_ncb, ja_current
brailix --list-analyzers     # Chinese and Japanese engines, grouped
brailix --list-resolvers     # pinyin resolvers
brailix --list-renderers     # output renderers
brailix --version
```

The lists come straight from the core registries, so they always match what `--profile`, `--analyzer`, `--resolver`, and `--to` will accept.

## Exit codes

- `0` — success.
- `1` — a translation or input error (a missing file, an unreadable document, a missing extra, an unknown engine). A short message is printed to standard error; there is no traceback.
- `2` — a usage error (an unknown option or value, or an invalid combination such as `--to cells --width 40`).

## See also

- [Getting started](getting-started.md) — the same translations from Python.
- [API reference](api.md) — the `Pipeline` and result objects the command wraps.
