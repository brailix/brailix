# Development

This page covers setting up a development environment, running the checks, and the conventions the codebase follows. For how to *use* brailix see [Getting started](getting-started.md); for how to *extend* it see [Extending brailix](extending.md); to report a bug or propose a change see [Contributing](../CONTRIBUTING.md).

## Set up

brailix targets **Python 3.13 or newer**.

```bash
# With uv (recommended):
uv sync                        # dev tools + the adapters the tests use
uv run pytest                  # test suite
uv run ruff check              # lint
uv run mypy brailix            # type check

# Or with pip:
python -m venv .venv && . .venv/bin/activate
pip install -e ".[zh,latex]" pytest pytest-cov hypothesis ruff mypy
pytest && ruff check && mypy brailix
```

`uv sync` installs the `dev` dependency group, which includes the tokenizer, pinyin, and LaTeX adapters the test suite exercises. Tests that need an adapter you have not installed skip themselves rather than fail, so a partial install still runs most of the suite.

## The test suite

The tests mirror the layered design — each layer is tested on its own so a failure points at one place:

- **Frontend** tests check type recognition, segmentation, pinyin, and the state machines, independently of the backend.
- **Math parser** tests check that a source formula normalizes to the expected MathML tree.
- **Backend** tests feed a fixed IR and assert a fixed braille IR, independently of which segmentation model is installed (so model drift can never move the assertions).
- **Pipeline / golden** tests check end-to-end output against human-reviewed samples under `tests/golden/`.
- **Schema** tests validate the JSON boundaries against the declared draft-07 schemas in `tests/schemas/` — every shipped profile, every serialized IR payload, every golden data file, and the `proofread_json()` wire format. The schemas own external *structure* only; semantic rules (referenced table files exist, entities resolve) stay in the config validator, so each rule lives in one place.
- **Property** tests (`tests/*/test_*_properties.py`, [Hypothesis](https://hypothesis.readthedocs.io/)) pin the architecture invariants over *generated* inputs instead of enumerated examples: the span algebra and the exact-slice contract, cell-to-source traceability, the `PinyinResolver` adapter contract, MathML normalizer idempotence and its never-raises soft-failure contract, run-mode consistency (strict raises exactly where normal warns; lenient only relabels), incremental recompilation equivalence to a fresh compile, and layout cell conservation. A failure prints a shrunken counterexample plus a `@reproduce_failure` blob to replay it; CI runs these derandomized for reproducibility, local runs explore fresh seeds. New adapters get the contract suites for free — a new pinyin resolver, for example, is covered by registering it.

The **golden** suite is the end-to-end safety net. When a rule change moves golden output, **review the diff by hand** — never blanket-accept it. The golden data lives as plain JSON (`tests/golden/data/`), so cases can be added or changed without writing Python; see that directory's README for the format.

The public import surface is pinned by `tests/test_public_api.py`. If you rename or drop a re-export, that test fails loudly — update it deliberately, since downstream code imports from the facade.

## Conventions

- **No hardcoding, low coupling.** Prefer a registry plus an adapter and a normalized mediator over an `if/else` that dispatches on a concrete type. New tools plug in at a seam; they don't edit the orchestrator. This is the single most important rule — the [Architecture](../ARCHITECTURE.md) document explains why.
- **Respect the component responsibilities.** The frontend classifies, the backend applies the rules, the renderer only encodes bytes, and braille state does not leak across block boundaries. Breaking one of these turns the next change into a rewrite.
- **Every change needs tests.** Add or update tests in the layer you touched, and run the golden suite for anything that affects output.
- **Match the surrounding code.** Comments and docstrings are in English; `ruff` enforces a line length of 100; type annotations are checked with `mypy`.
- **Keep the core dependency-free.** The `brailix` package itself imports no third-party parser. Anything heavier rides on an optional extra and loads lazily through a registry.

## Where things live

```
brailix/
  pipeline/     end-to-end entry (translate_text / translate_document / translate_block)
  core/         shared types, contexts, errors, config loading, registries, protocols
  input/        document input adapters (plain / markdown / docx / music_xml)
  frontend/     text -> structured IR (segment, normalize, zh, ja, math, music)
  ir/           DocumentIR / InlineIR / BrailleIR
  backend/      IR -> BrailleIR (dispatch + number / latin / punct / zh / ja / math / music)
  renderer/     BrailleIR -> output (unicode / brf / cells / layout)
  profiles/     braille standards (cn_current, cn_ncb, ja_current)
  resources/    braille rule tables (shared at the top; region/scheme-specific below)
tests/          backend / core / frontend / golden / input / integration / ir / renderer / resources
```

See [Architecture](../ARCHITECTURE.md) for the directory tree in full and the design behind each layer.
