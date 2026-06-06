# brailix

Pluggable Braille compiler with a normalized intermediate representation.

`brailix` compiles text into braille through a normalized IR
(text → IR → braille). The core package has **zero third-party parser
dependencies** — install only the adapters you need as `pip` extras:

```bash
pip install brailix              # core: plain text, Markdown, MusicXML
pip install brailix[zh]          # Chinese: segmentation + pinyin (light, offline)
pip install brailix[zh,latex]    # + LaTeX math
pip install brailix[ja]          # Japanese: kana + kanji readings (light, offline)
pip install brailix[hanlp,g2pw]  # accurate Chinese engines (download models)
pip install brailix[docx]        # Word .docx / .docm (incl. MathType / OMML)
```

The `hanlp` and `g2pw` backends download their model weights on first use
(into a local `models/` directory).

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
- [API reference](docs/api.md) — the `Pipeline`, result objects, IR, profiles, and renderers.
- [Extending brailix](docs/extending.md) — add an engine, format, renderer, profile, or language.
- [Development](docs/development.md) — set up, run the tests, and the project conventions.
- [Architecture](ARCHITECTURE.md) — the pipeline, the intermediate representations, and the adapter pattern.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md). Release notes are in
[CHANGELOG.md](CHANGELOG.md).

## License

Apache-2.0 — see [LICENSE](LICENSE).
