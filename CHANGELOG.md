# Changelog

All notable changes to brailix are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-06-04

Initial public release.

### Added

- Braille compiler pipeline: text and documents are compiled through a normalized
  intermediate representation (text → IR → braille), and every output cell is
  traceable back to its source span.
- Chinese braille: segmentation, pinyin, and polyphone disambiguation, with the
  Current Chinese Braille (`cn_current`) and National Common Braille (`cn_ncb`)
  profiles.
- Mathematics: LaTeX / MathML / OMML / ASCIIMath sources are normalized to a
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
