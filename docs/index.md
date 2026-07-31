# brailix documentation

`brailix` is a pluggable Braille compiler: it turns text, documents, mathematics, and music into braille, and vector graphics into tactile images, through a unified intermediate representation (IR). The core package has no third-party parser dependencies; language and format support installs as optional adapters.

## Guides

- [Getting started](getting-started.md) — install brailix, translate your first text, and read the result.
- [Command-line interface](cli.md) — translate from a terminal with the `brailix` command, no Python required.
- [API reference](https://brailix.github.io/brailix/) — the stable public surface: the `Pipeline`, the result objects, the IR types, and renderers. Generated from the source on every push, so it always describes the code that is actually there.
- [Extending brailix](extending.md) — add a tokenizer, a pinyin engine, a math or music source, an input format, a renderer, a braille profile, or a whole new language.
- [Development](development.md) — set up a development environment, run the test suite, and follow the project's conventions.

## Design and project docs

- [Architecture](../ARCHITECTURE.md) — the compiler pipeline, the intermediate representations, and the adapter pattern the whole codebase is built on.
- [Contributing](../CONTRIBUTING.md) — how to report a bug or propose a change.

## The shape of the library, in one paragraph

A source document enters through an input adapter and becomes a `DocumentIR`. The frontend recognizes what each region is (Chinese prose, numbers, dates, Latin words, math, music, a figure) and produces typed inline IR. The backend then applies a profile's rules to produce an *output-domain IR*, and a renderer encodes that into bytes. There are two output domains, and they diverge only at the backend: prose, math and music become a `BrailleDocument` of braille cells, which the `unicode`, `brf`, `cells` and `layout` renderers encode as Unicode braille, BRF, a dot array, or a laid-out page; a figure's SVG becomes a `TactileRaster` of raise levels, which the `bmp`, `png`, `pdf` and `tactile_preview` renderers encode as an embossable image or a refreshable-display preview. Every braille cell remembers the source span it came from, which is what makes proofreading possible. See [Architecture](../ARCHITECTURE.md) for the full picture.
