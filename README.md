# brailix

Pluggable Braille compiler with a normalized intermediate representation.

`brailix` compiles source documents into the two things a reader's hands can
use:

- **Braille** — prose, mathematics and music, emitted as Unicode braille, BRF
  for an embosser, a dot array, or a laid-out page.
- **Tactile graphics** — an SVG or a figure spec, rasterized into an
  embossable BMP, PNG or PDF, or into a preview for a refreshable display.

Both take the same route through a normalized IR: source, semantic IR,
output-domain IR, encoded output. The core package has **zero third-party
parser dependencies** — install only the adapters you need as `pip` extras:

```bash
pip install brailix              # core: plain text, Markdown, MusicXML, SVG tactile graphics
pip install brailix[zh]          # Chinese: segmentation + pinyin (light, offline)
pip install brailix[zh,latex]    # + LaTeX math
pip install brailix[ja]          # Japanese: kana + kanji readings (light, offline)
pip install brailix[hanlp,g2pw]  # accurate Chinese engines (download models)
pip install brailix[docx]        # Word .docx / .docm (incl. MathType / OMML)
pip install brailix[graphics]    # import an existing bitmap as a tactile figure
```

Tactile graphics are part of that zero-dependency core: SVG parsing and
BMP / PNG / PDF writing are standard library, so a figure drawn as SVG or
described as a spec compiles with nothing extra installed. The `graphics`
extra is only needed to bring an existing *bitmap* (PNG, JPEG, ...) in as a
figure, which needs an image decoder.

The `hanlp` and `g2pw` backends download their model weights on first use.
HanLP's go into a local `models/` directory that brailix points it at; g2pW
uses its own library's cache location.

## Command line

Installing brailix puts a `brailix` command on your `PATH` (also available as
`python -m brailix`):

```bash
brailix "我在重庆。" -p cn_current                  # Unicode braille to stdout
brailix --file lesson.md --width 32 -p cn_current  # wrap a Markdown file at 32 cells
brailix "123" --to brf -o out.brf -p cn_current    # NABCC bytes for an embosser
brailix --list-profiles                            # the names -p accepts
```

`-p` / `--profile` names the braille standard to translate into. It is
required — more than one standard ships and the choice is always yours — except
for the `--list-*` flags, which just print what the build supports.

See the [command-line guide](docs/cli.md) for the full reference.

## Music score formats

The `music` subsystem accepts several score sources. Only MusicXML is
free of third-party deps; MIDI and ABC need optional extras:

| Format | Extensions | Install | Libraries |
|---|---|---|---|
| MusicXML | `.musicxml` / `.xml` / `.mxl` | — (built-in) | stdlib `xml.etree` + `zipfile` |
| MIDI | `.mid` / `.midi` | `pip install brailix[midi]` | `mido` (reads MIDI bytes) + `partitura` (→ MusicXML) |
| ABC notation | `.abc` | `pip install brailix[abc]` | `abc-xml-converter` (packaged build of Wim Vree's `abc2xml`) |
| All three | — | `pip install brailix[music]` | combined bundle |

MIDI import goes through `partitura`, whose quantization and
voice-splitting are heuristic. For best results, clean up a MIDI file in
a notation editor (MuseScore, Sibelius, Finale) and export MusicXML
before compiling.

## Documentation

Full docs are in [`docs/`](docs/index.md):

- [Getting started](docs/getting-started.md) — install and translate your first text.
- [Command-line interface](docs/cli.md) — translate from a terminal with the `brailix` command.
- [API reference](https://brailix.github.io/brailix/) — the `Pipeline`, result objects, IR, and renderers; generated from the source.
- [Extending brailix](docs/extending.md) — add an engine, format, renderer, profile, or language.
- [Development](docs/development.md) — set up, run the tests, and the project conventions.
- [Architecture](ARCHITECTURE.md) — the pipeline, the intermediate representations, and the adapter pattern.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

Apache-2.0 — see [LICENSE](LICENSE).
