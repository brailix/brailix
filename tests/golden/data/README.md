# Golden test data

The JSON files here hold brailix's "input → expected braille output"
tables. Each file is a related group of cases (Chinese punctuation, math
symbols, fractions, letters, ...).

Cases can be added or removed directly in the JSON — **no Python
required**.

## File list

| File | Contents |
|---|---|
| `zh_text.json` | single characters / Chinese words |
| `latin.json` | English letters, words, acronyms |
| `numbers.json` | integers, decimals, dates, percentages, units |
| `punctuation.json` | Chinese / math punctuation |
| `math_primitives.json` | math leaves: variables, numbers, operators |
| `math_structures.json` | fractions, radicals, sub/superscripts |
| `math_functions.json` | function names (sin, cos, log, arcXXX) |
| `math_accents.json` | accent marks (prime, dot, vector arrow) |
| `math_big_ops.json` | sum ∑, integral ∫, limit lim |
| `math_under_over.json` | under/over marks, hats, etc. |
| `math_unsupported.json` | not-yet-supported elements (matrices) |
| `mixed.json` | mixed Chinese/English, Chinese/number, Chinese+formula |
| `edge_cases.json` | boundary / malformed inputs |
| `warnings.json` | warning-code tests |

## JSON file format

```jsonc
{
  "description": "one-line summary of what this file tests",
  "regen_hint": "command to regenerate the expected output",
  "groups": {
    "<group name>": {
      "description": "what this group tests",
      "cases": [
        {
          "src": "我",         // required: input text
          "expected": "⠕⠄",    // required: expected braille output
          "note": "wo3"         // optional: note, shown on failure
        }
      ]
    }
  }
}
```

## Case fields

Each case (an entry in a `cases` array) supports these fields:

| Field | Type | Meaning |
|---|---|---|
| `src` | string | **Required.** Input text (Chinese / English / LaTeX / mixed) |
| `expected` | string | Expected braille output. **Omit to skip the output check** and only check warnings |
| `note` | string | Optional note, shown in the failure message to help locate the rule |
| `warnings_include` | string[] | Warning codes that must appear, e.g. `["MATH_UNKNOWN_SYMBOL"]` |
| `warnings_exclude` | string[] | Warning codes that must **not** appear |
| `warnings_exclude_prefix` | string | No warning code starting with this prefix may appear, e.g. `"MATH_"` means "no math-error warnings at all" |
| `id` | string | Optional pytest case ID (defaults to `src`) |

## Regenerating `expected`

Each JSON's top-level `regen_hint` field records the command that
computes the braille. For example:

```bash
python -c "from brailix import Pipeline; print(repr(Pipeline().translate_text('我').render()))"
```

Replace `'我'` with your new input; the output is the string to put in
`expected` (drop the quotes).

## Adding a case

1. Pick a fitting file (use `edge_cases.json` if none fits).
2. Find the matching group (or add a new one).
3. Add an entry to the `cases` array: `{"src": "...", "expected": "...", "note": "..."}`.
4. **Strict JSON syntax**: double-quote strings, and **no trailing comma** after the last item.
5. Run the tests: `uv run pytest tests/golden -q`.
6. If you aren't sure what `expected` should be, fill in an empty string `""` first, run the tests, read the `actual=` in the failure message, then paste it back.

## Conventions

- `⠀` is a **blank** braille cell (U+2800), meaning "one empty cell". Every `⠀` you see in these files is a real braille character, not an ordinary space.
- For the individual braille-cell symbols, see the resource files referenced by `brailix/profiles/cn_current.json`.

## Analyzer / resolver used

The test fixtures fix the toolchain to **jieba segmentation + pypinyin
pinyin** (see `tests/golden/conftest.py`). This is close to a "real
user" setup:

- **jieba** splits Chinese into words (`你好` and `世界` each count as one word).
- **pypinyin** gives each character's pinyin (with numeric tones, e.g. `nian2`, `yue4`).

Both packages are extras under `[project.optional-dependencies]`; when
they're absent the whole golden suite auto-skips. Install with:

```bash
uv sync --extra jieba --extra pypinyin --extra latex
```

**Note on spacing**: jieba splits `你好世界` into `[你好, 世界]`, so the
braille output has **no** blank cell inside `你好`, only one between
`你好` and `世界`. If a case looks like it's "missing a space", check
jieba's segmentation before deciding.

Polyphone readings (重庆 / 银行 / 朝阳 / 长安, etc.) come from pypinyin
and are **locked** into `zh_text.json`. If the pinyin engine is ever
swapped, those expected values will need updating.
