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

The public import surface is pinned by `tests/test_public_api.py`, and the check is exact set equality: a re-export that disappears fails the test, and so does a name that appears in `__all__` without being added to the manifest. Both directions are deliberate — publishing a name is a promise of support, so it should take an explicit edit rather than happening by accident.

Two more rules hold across the whole package rather than only its facades. The first is about what a module *offers*: no module binds a name from outside brailix under its plain spelling, so `from brailix.<anything> import Path` does not quietly work at an address that was never ours to promise. In practice that means an import the module uses at runtime is aliased — `import os as _os`, `from dataclasses import dataclass as _dataclass` — while an import that only ever appears in an annotation goes under `if _TYPE_CHECKING:`, where the package's `from __future__ import annotations` leaves it as a string and the name never becomes a binding at all. Two families stay bound rather than moving: `ClassVar` / `InitVar`, because `dataclasses` resolves those string annotations by looking the identifier up in the defining module's globals (move `ClassVar` and a class variable silently becomes a field), and everything in `brailix/ir/`, whose wire-type checking resolves annotations with `typing.get_type_hints`.

The second is about what a module *advertises*: no module's `__all__` may list an underscore-prefixed name. A split package that wants a helper to stay importable at the old path simply keeps binding it (an explicit `from x import _y` never consulted `__all__`); what it must not do is advertise it there, because that says "supported" about the same helper the policy above calls internal and free to move.

The documentation is checked the same way, by `tests/test_user_docs_examples.py`: every `brailix …` command line printed in the README, these guides, or the CLI's own `--help` goes through the real argument parser and its validation, and every call in a Python example is bound against the real signature. So an example that no longer runs — a required option someone forgot, a keyword argument that became mandatory — fails in the suite rather than under a reader who copied it. If you add an example, write it as something that would actually work.

## The API reference

There is no hand-written API page. `scripts/build_docs.py` generates the reference from the docstrings with [pdoc](https://pdoc.dev/), and CI publishes it on every push to `main`:

```bash
uv run --group docs python scripts/build_docs.py --output-dir site
```

It documents exactly the modules the manifest above pins, and pdoc honours `__all__`, so each page shows the supported surface and nothing else. Two consequences worth knowing when you write a docstring:

- **The docstring is the documentation.** A public class or function is described by its own docstring and nowhere else, so that is where a behaviour change gets written down. There is no second copy to fall out of step — which is the point, since the three drifts that prompted this were all cases where the docstring was right and the hand-written page was wrong.
- **reStructuredText cross-references are converted, not rendered raw.** The codebase writes references as `` :class:`~brailix.ir.document.DocumentIR` ``; the builder rewrites those into backticked identifiers that pdoc links. Keep using the roles — they say which *kind* of thing is being referenced, and the conversion is tested.

## Conventions

- **No hardcoding, low coupling.** Prefer a registry plus an adapter and a normalized mediator over an `if/else` that dispatches on a concrete type. New tools plug in at a seam; they don't edit the orchestrator. This is the single most important rule — the [Architecture](../ARCHITECTURE.md) document explains why.
- **Respect the component responsibilities.** The frontend classifies, the backend applies the rules, the renderer only encodes bytes, and braille state does not leak across block boundaries. Breaking one of these turns the next change into a rewrite.
- **Every change needs tests.** Add or update tests in the layer you touched, and run the golden suite for anything that affects output.
- **Match the surrounding code.** Comments and docstrings are in English; `ruff` enforces a line length of 100; type annotations are checked with `mypy`.
- **Keep the core dependency-free.** The `brailix` package itself imports no third-party parser. Anything heavier rides on an optional extra and loads lazily through a registry.

## Where things live

```
brailix/
  pipeline/     end-to-end entry (translate_text / translate_document / translate_block /
                translate_document_to_pages / translate_graphic)
  core/         shared types, contexts, errors, config loading, registries, protocols
  input/        document input adapters (plain / markdown / docx / music_xml)
  frontend/     source -> structured IR (segment, normalize, zh, ja, math, music, graphics)
  ir/           DocumentIR / InlineIR / BrailleIR / TactileRaster
  backend/      IR -> output-domain IR (dispatch + number / latin / punct / zh / ja / math /
                music; tactile/ rasterizes a graphic tree and composes mixed pages)
  renderer/     output-domain IR -> bytes (unicode / brf / cells / layout for braille;
                bmp / png / pdf / tactile_preview for a tactile raster)
  profiles/     braille standards (cn_current, cn_ncb, ja_current)
  resources/    braille rule tables (shared at the top; region/scheme-specific below)
tests/          backend / core / frontend / golden / input / integration / ir / renderer / resources
```

See [Architecture](../ARCHITECTURE.md) for the directory tree in full and the design behind each layer.
