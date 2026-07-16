# Changelog

All notable changes to brailix are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Command-line interface: a `brailix` command (also `python -m brailix`) compiles
  text, Markdown, Word, and MusicXML to braille from a terminal. Input is a
  positional string, a `--file` (dispatched by suffix), or piped standard input;
  choose the renderer with `--to` (`unicode` / `brf` / `cells` / `layout`), wrap and
  paginate with `--width` / `--page-height` / `--page-numbers`, select the profile
  and engines (`--profile` / `--analyzer` / `--resolver` / `--mode`), and discover
  what's installed with `--list-profiles` / `--list-analyzers` / `--list-resolvers` /
  `--list-renderers`. The accepted values come from the core registries.
- Japanese kana braille: a kana/kanji segmenter, morphological analysis for kanji
  readings (janome / fugashi / sudachi adapters) with 文節 word-spacing, and the
  `ja_current` profile. Pure kana needs no extra; kanji readings use an analyzer
  the way pinyin drives Chinese.
- MIDI (`.mid` / `.midi`) and ABC (`.abc`) score files can be compiled directly:
  `translate_file` / `parse_file` now recognise these suffixes and convert them to
  MusicXML through the `midi` / `abc` adapters before translating. Needs the
  `midi` / `abc` extra installed.
- Input size limits: `parse_file` / `Pipeline.parse_file` / `translate_file` now
  take an `InputLimits` budget and reject an over-size file with a `stat()` check
  *before* reading it, so an untrusted upload can't exhaust memory the moment it is
  loaded. The default is generous (a local caller never trips it); a service
  tightens it, and `InputLimits.unlimited()` opts out. Exported at the top level as
  `InputLimits` / `InputTooLargeError`.
- Traceability check: `BrailleDocument.validate_traceability()` reports every braille
  cell that carries no `source_span`, turning the "each cell maps back to its source"
  contract — the basis of click-a-cell → jump-to-source proofreading — into a reusable
  check on the IR instead of a convention only the test suite re-asserted by hand. It
  reports; it never raises, so hand-built or deserialized documents stay compatible.

### Changed

- Soft-failure boundaries no longer mask programming errors: a genuine code defect
  (`AttributeError` / `NameError` / `AssertionError`) inside a math / music /
  graphic adapter now propagates instead of being disguised as an "unreadable
  input" warning behind a green pipeline. Malformed input still degrades gracefully
  as before — only latent bugs surface. The `.mxl` reader's catch is narrowed to
  the specific zip / decompression error types accordingly.

### Removed

- Dropped the non-functional `pkuseg`, `asciimath`, and `markdown` extras: each
  declared a dependency that no adapter ever loaded, so installing it had no
  effect. The built-in Markdown reader is pure-stdlib and needs no extra; an
  ASCIIMath or pkuseg adapter can still be added later under the same name.

### Fixed

- A MusicXML score whose `<divisions>` or `<duration>` carries a non-decimal
  numeral (for example a superscript or circled digit from a malformed export) no
  longer collapses the whole score to an error placeholder. Note-type inference
  skips the unusable value and the rest of the score compiles.
- The block cache key is now safe on its own: `block_hash` (and
  `CompiledBlock.source_hash`) fold in the block's structure, so a same-text
  heading and paragraph, an ordered vs unordered list, or two differently-shaped
  tables no longer collide — a cache keyed on the digest could previously serve one
  block's braille for another.
- `translate_block` no longer reuses stale output when a caller edits an
  already-populated block's `text`: it detects that the block's children no longer
  match the current text and recompiles from the authoritative text (an unchanged
  block still skips the frontend).

### Security

- XML entity-declaration bombs can no longer slip past the parser through a non-UTF-8
  encoding. Every XML boundary (MathML, MusicXML, the `.mxl` container, the OOXML in a
  `.docx`) already refused a `<!ENTITY>` declaration — the "billion laughs" expansion
  vector — but the guard scanned only the ASCII byte form, so a UTF-16-encoded document
  slipped a declaration through and the parser expanded it. The scan now also covers the
  UTF-16 (LE and BE) forms, so the guard holds in every encoding the parser will decode.

## [0.1.0] - 2026-06-04

Initial public release.

### Added

- Braille compiler pipeline: text and documents are compiled through a normalized
  intermediate representation (text → IR → braille), and every output cell is
  traceable back to its source span.
- Chinese braille: segmentation, pinyin, and polyphone disambiguation, with the
  Current Chinese Braille (`cn_current`) and National Common Braille (`cn_ncb`)
  profiles.
- Mathematics: LaTeX / MathML / OMML sources are normalized to a
  MathML tree (which serves as the IR) and translated to math braille; chemical
  equations (`\ce{...}`) are supported.
- Music: MusicXML / `.mxl` / MIDI / ABC sources are normalized to a MusicXML tree
  and translated to music braille (BANA 2015).
- Document input: plain text, Markdown, and Word `.docx` / `.docm` (including
  MathType / Equation 3.0 and OMML math extraction).
- Output renderers: Unicode Braille, BRF, and a dot/cell array, plus layout
  (line breaking, indentation, pagination).
- Adapter architecture: the core has no third-party parser dependencies; language
  and format support installs as optional extras (see the README).
- Public API: `Pipeline`, `TranslationResult`, `CompiledBlock`, `TreeSubcache`,
  and `block_hash` (the pinned surface is checked by `tests/test_public_api.py`).
