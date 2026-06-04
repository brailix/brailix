# Contributing to brailix

Thanks for your interest in brailix — a pluggable Braille compiler with a
normalized intermediate representation. This repository is the open-source
**core library**; a separate desktop proofreading application is built on top of
it and is not part of this repo.

## Ways to contribute

- **Report a bug or request a feature**: open an issue. For a bug, include the
  input (text / document / formula), the profile you used, the braille you got,
  and the braille you expected.
- **Improve the code or docs**: open a pull request (see the workflow below).

## Development setup

brailix targets **Python 3.13+** and has no third-party parser dependencies in
its core; adapters install as optional extras.

```bash
git clone <this-repo> && cd brailix

# With uv (recommended):
uv sync                        # dev tools + the adapters the tests use
uv run pytest                  # test suite
uv run ruff check              # lint
uv run mypy brailix            # type check

# Or with pip:
python -m venv .venv && . .venv/bin/activate
pip install -e ".[zh,latex]" pytest pytest-cov ruff mypy
pytest && ruff check && mypy brailix
```

## Design principles

Read `ARCHITECTURE.md` first — it explains the pipeline (text → IR → braille)
and the patterns the codebase is built on. A few rules matter more than the rest:

- **Adapter + normalization mediator.** The library depends on no specific
  third-party tool; each subsystem defines a normalized mediator format
  (`ChineseToken`, MathML, `DocumentIR`, `BrailleIR`) and plugs tools in through
  adapters. Add a new tool as an adapter, not by editing core code.
- **No hardcoding, low coupling.** Prefer a registry / adapter plus a normalized
  mediator over `if/else` dispatch on a concrete type.
- **Respect the component responsibilities** in `ARCHITECTURE.md` §14 (the frontend
  classifies, the backend follows the rules, the renderer only encodes bytes, and
  so on). Breaking one turns the next change into a rewrite.
- **Match the surrounding code**: comments in English, `ruff` line length 100.

## Tests

Every change needs tests. The suite is layered (frontend / math / backend /
pipeline) so each layer runs on its own, and there is a golden suite for
end-to-end output — **review golden diffs by hand**, never blanket-accept them.

## How contributions are released

Releases are prepared from an upstream source tree and published here, so an
accepted change is integrated upstream (with credit) and ships in a following
release rather than as a direct merge into this repository's history. Open a pull
request or an issue and we will take it from there.

## License

By contributing you agree that your contributions are licensed under the
project's [Apache-2.0](LICENSE) license.
