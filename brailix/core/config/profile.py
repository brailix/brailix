"""The :class:`BrailleProfile` dataclass.

This module holds nothing but the data type and its lookup methods.
The actual loading machinery lives in :mod:`brailix.core.config.loader`,
which constructs and returns ``BrailleProfile`` instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from brailix.core.config._helpers import (
    _feature_keys_to_try,
    _feature_lookup,
)
from brailix.core.config.zh_ncb_tables import NcbExceptions
from brailix.core.defaults import DEFAULT_LANGUAGE


@dataclass(slots=True)
class BrailleProfile:
    """Resolved profile: all table files are read and parsed."""

    name: str
    language: str = DEFAULT_LANGUAGE
    cell: str = "six_dot"
    features: dict[str, Any] = field(default_factory=dict)

    initials: dict[str, tuple[int, ...]] = field(default_factory=dict)
    finals: dict[str, tuple[int, ...]] = field(default_factory=dict)
    tones: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # Punctuation values are *cell sequences* — one entry may map to
    # several cells (e.g. the Chinese full stop 。 = ⠐⠆ is two cells). Same shape as
    # ``math_symbols`` so the backend treats them uniformly.
    punctuation: dict[str, tuple[tuple[int, ...], ...]] = field(default_factory=dict)
    punctuation_spacing: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    digits: dict[str, tuple[int, ...]] = field(default_factory=dict)
    number_sign: tuple[int, ...] = ()
    decimal_point: tuple[int, ...] = ()
    thousands_sep: tuple[int, ...] = ()
    # connector (⠤): the cell the backend renders for a :class:`Connector` —
    # the joiner inside a letter+hanzi compound word (x轴 / T恤). Single
    # cell; empty () when a profile doesn't declare one (the Connector
    # then degrades to a blank cell). Both shipped zh profiles set ⠤
    # (dots 3-6) via ``tables.connector``.
    connector: tuple[int, ...] = ()
    # Letter+hanzi compound lexicon (scheme-neutral zh language data via
    # ``tables.zh.compounds``): surfaces like ``x轴`` that take a connector
    # instead of a blank cell at a letter↔hanzi boundary. Empty when undeclared.
    zh_compounds: frozenset[str] = frozenset()
    math_symbols: dict[str, tuple[tuple[int, ...], ...]] = field(default_factory=dict)
    math_functions: dict[str, tuple[tuple[int, ...], ...]] = field(default_factory=dict)
    # Math structures keyed by dotted names (``fraction.bar`` etc.).
    math_structures: dict[str, tuple[tuple[int, ...], ...]] = field(default_factory=dict)
    # Antoine "lower" digit cells, used for atomic digit/digit fractions.
    math_digits_lower: dict[str, tuple[int, ...]] = field(default_factory=dict)
    # per-symbol (space_before, space_after) flags; missing entries default to (False, False).
    math_symbol_spacing: dict[str, tuple[bool, bool]] = field(default_factory=dict)
    # Per-symbol role: one of "op"/"rel"/"delim"/"punct"/"shape"/"big_op".
    # Lookup defaults to ``None``.
    math_symbol_roles: dict[str, str] = field(default_factory=dict)
    # Contextual accent-mark kind for chars that render as a vector mark
    # (over-arrow / short bar) when they sit in an accent over/under position:
    # → / ← (\vec, \overrightarrow, \overleftarrow) -> "arrow"; ¯ / ― / ‾
    # (\bar, \overline) -> "bar". Independent of the global role — → stays
    # role=rel for ordinary relation use. The cells come from
    # ``math_structures`` (accent.mark.<kind>.{single,double}); this map
    # only names the kind, so the backend can pick single vs double by the
    # base letter count.
    math_symbol_accent_marks: dict[str, str] = field(default_factory=dict)
    # Symbols that take the 46-dot script prefix when subscripted/superscripted
    # (e.g. ∫ and ∮ per cn_current).
    math_symbol_script_prefix_flags: dict[str, bool] = field(default_factory=dict)
    # Symbols whose cell sequence is provisional (a guess by maintainers,
    # not validated against an authoritative rule reference). Proofread
    # tools should highlight these. Backend treats them as ordinary symbols.
    math_symbol_provisional_flags: dict[str, bool] = field(default_factory=dict)
    # Symbols that take a category marker (``structures.indicator.<name>``)
    # in front of their cells: maps the symbol char → the marker name
    # ("symbol" ⠫ / "operation" ⠰ / "negation" ⠈). The backend emits the
    # marker, so these symbols' cells stay bare — the same pathway a
    # function name uses, instead of baking the marker into the table.
    math_symbol_indicator_flags: dict[str, str] = field(default_factory=dict)
    # Function flags: which functions behave as big-ops, which take the
    # 46-dot script prefix.
    math_function_big_op_flags: dict[str, bool] = field(default_factory=dict)
    math_function_script_prefix_flags: dict[str, bool] = field(default_factory=dict)
    # Neutral letter tables (no context prefix). Shared between the math
    # backend (which prepends a script-class prefix from math.structures)
    # and the future LatinBraille backend (which applies its own rules).
    # Two sub-keys: "lower" and "upper", each maps char → dot tuple.
    latin_letters: dict[str, dict[str, tuple[int, ...]]] = field(default_factory=dict)
    greek_letters: dict[str, dict[str, tuple[int, ...]]] = field(default_factory=dict)
    # NCB (National Common Braille / GF0019-2018) — all standard-specific data lives
    # in a single :class:`NcbExceptions` container, loaded from one
    # ``tables.zh.exceptions`` resource (every NCB-specific difference
    # consolidated in one place).  cn_current leaves it None and the
    # backend's NCB call sites all no-op.
    zh_exceptions: NcbExceptions | None = None
    # Music (BANA 2015 Music Braille Code) — nested by topic:
    # ``music["notes"]["whole_or_16th_C"]`` -> ``((1,3,4,5,6),)``.
    # An empty cell (BANA prints two cell groups with an internal space)
    # is stored as ``()`` inside the cell sequence — e.g. a 4-cell entry
    # whose third cell is empty becomes ``((1,4),(1,4),(),(1,4))``.
    # Subdirectories (instruments/, vocal/) are flattened with a single-
    # level prefix in the topic key: ``music["instruments.keyboard"]``,
    # ``music["vocal.music_lines"]``, ...
    music: dict[str, dict[str, tuple[tuple[int, ...], ...]]] = field(default_factory=dict)
    # Declarative, non-cells rule sections per music topic (e.g.
    # ``chord_symbols`` -> ``kind_spec`` -> chord-kind emit recipes),
    # loaded from ``_``-prefixed sections the cells loader skips.
    music_specs: dict[str, dict[str, Any]] = field(default_factory=dict)
    # Per-language braille tables (ARCHITECTURE §7.6 generic language
    # slot): subtag -> table name -> entry -> cell sequence, e.g.
    # ``lang_tables["ja"]["kana"]["カ"] == ((1, 6),)``. New languages put
    # their cell tables here (read via :meth:`lang_table`) instead of
    # welding per-language fields onto this dataclass the way the legacy
    # zh tables (initials / finals / tones) did. zh isn't migrated yet.
    lang_tables: dict[
        str, dict[str, dict[str, tuple[tuple[int, ...], ...]]]
    ] = field(default_factory=dict)
    # Per-instance lazy cache for letter() results. Excluded from
    # ``__eq__`` / ``__repr__``: it's runtime-populated memoization, so two
    # profiles built from identical tables must stay equal (and hashable as
    # config-cache keys) even after one has had ``letter()`` called on it.
    _letter_cache: dict[str, tuple[tuple[int, ...], ...] | None] = field(
        default_factory=dict, compare=False, repr=False
    )

    # -- Features -----------------------------------------------------

    def feature(self, key: str, default: Any = None) -> Any:
        """Look up a feature by name.

        Supports both new dotted form (``"math.simplify_fraction"``)
        and legacy flat form (``"math_simplify_fraction"``). Legacy
        keys are routed to the dotted lookup via
        :data:`_FEATURE_FLAT_ALIASES`. Missing keys return ``default``.

        Live changes to ``self.features`` (e.g. via ``monkeypatch.setitem``
        in tests) are honoured — we re-walk the dict on every call
        rather than caching the flat lookup once at load time.
        """
        # Try the literal key first against the live features dict —
        # both as a flat key and as a dotted path. Then try its
        # alias counterpart.
        sentinel = object()
        for k in _feature_keys_to_try(key):
            value = _feature_lookup(self.features, k, sentinel)
            if value is not sentinel:
                return value
        return default

    # -- Per-language tables (§7.6 generic slot) ----------------------

    def lang_table(self, name: str) -> dict[str, tuple[tuple[int, ...], ...]]:
        """Per-language cell table ``name`` (e.g. ``"kana"``) for this
        profile's language subtag, or ``{}`` if absent.

        The generic counterpart to the welded zh tables: a Japanese
        backend reads ``profile.lang_table("kana")`` the way the Chinese
        backend reads ``profile.finals``. Keyed by the subtag before the
        hyphen in :attr:`language` (``ja-JP`` -> ``ja``)."""
        lang = self.language.split("-")[0]
        return self.lang_tables.get(lang, {}).get(name, {})

    # -- Math symbol lookups -----------------------------------------

    def math_symbol(self, ch: str) -> tuple[tuple[int, ...], ...] | None:
        return self.math_symbols.get(ch)

    def math_symbol_spaces(self, ch: str) -> tuple[bool, bool]:
        """Return (space_before, space_after) flags for a math symbol.
        Missing/unknown symbols default to (False, False)."""
        return self.math_symbol_spacing.get(ch, (False, False))

    def math_symbol_role(self, ch: str) -> str | None:
        """Return the role of a math symbol — one of ``"op"``, ``"rel"``,
        ``"delim"``, ``"punct"``, ``"shape"``, ``"big_op"``, or ``None``
        if the character isn't in the symbols table."""
        return self.math_symbol_roles.get(ch)

    def math_accent_mark_kind(self, ch: str) -> str | None:
        """Accent-mark kind for ``ch`` (``"arrow"`` / ``"bar"``), or
        ``None`` if the char carries no ``accent_mark`` tag.

        Non-``None`` means: when ``ch`` is an over/under accent, render it
        as a vector mark via ``accent.mark.<kind>.{single,double}`` instead
        of its ordinary symbol cells. This is what lets → / ← double as the
        vector arrow mark in accent context while staying role=rel everywhere
        else."""
        return self.math_symbol_accent_marks.get(ch)

    def math_symbol_script_prefix(self, ch: str) -> bool:
        """Whether this symbol takes a 46-dot prefix in front of its
        sub/superscript indicator. True for ∫ ∮ in cn_current."""
        return self.math_symbol_script_prefix_flags.get(ch, False)

    def math_symbol_provisional(self, ch: str) -> bool:
        """Whether this symbol's cell sequence is provisional (guess /
        placeholder, not authoritatively rule-backed). Lets proofread
        tools surface "double-check this" hints. Default False."""
        return self.math_symbol_provisional_flags.get(ch, False)

    def math_symbol_indicator(self, ch: str) -> str | None:
        """The category-marker name this symbol takes in front of its
        cells, or ``None``. The backend prefixes ``structures.indicator.
        <name>`` — ``"symbol"`` ⠫ (quantifiers ∀∃∇ + shapes), ``"operation"``
        ⠰ (set/logic ∪∩∧∨∖), ``"negation"`` ⠈ (≠≯≮ …). Keeping it a backend
        step means the symbol table never bakes the marker into cells."""
        return self.math_symbol_indicator_flags.get(ch)

    def punctuation_spaces(self, ch: str) -> tuple[bool, bool]:
        """Return (space_before, space_after) flags for a punctuation char.
        Missing/unknown chars default to (False, False)."""
        return self.punctuation_spacing.get(ch, (False, False))

    # -- List markers ---------------------------------------------------
    #
    # Block-level list expansion (``backend.block._expand_list``) used
    # to query the punct table by literal characters ``"·"`` / ``"."``,
    # baking the marker choice into the backend. These accessors move
    # that choice into the profile: the default values match BANA /
    # Current Chinese Braille conventions, and a profile JSON can override either via
    # ``features.list.unordered_marker`` / ``features.list.ordered_marker``
    # — the actual cells still come from the punct table, so a profile
    # that wants ``"-"`` for unordered just needs to ensure ``"-"`` is
    # mapped in ``punct.json``.

    def list_marker_unordered_char(self) -> str:
        """Character used as the unordered-list bullet (BANA: ``·``)."""
        return self.feature("list.unordered_marker", "·")

    def list_marker_ordered_char(self) -> str:
        """Character placed after the digit in ordered lists (``.``)."""
        return self.feature("list.ordered_marker", ".")

    # -- Letter with script-class prefix --------------------------------
    #
    # This API is **not math-specific**. Math identifiers use it, but
    # so do quantity units (``kg``, ``cm``) in the number backend and
    # potentially any future backend that needs to render a single
    # Latin/Greek letter with the profile's context prefix attached.

    def letter(self, ch: str) -> tuple[tuple[int, ...], ...] | None:
        """Look up a single Latin/Greek letter with its script-class
        prefix prepended.

        Reads ``latin_letters`` / ``greek_letters`` for the bare letter
        cell, then prepends the matching ``letter_prefix.{script}_{case}``
        cell from ``math_structures``. Result is cached per character.
        Returns ``None`` when the character isn't in any letter table —
        caller decides on the fallback.
        """
        if ch in self._letter_cache:
            return self._letter_cache[ch]
        result = self._compose_letter(ch)
        self._letter_cache[ch] = result
        return result

    def math_identifier(self, ch: str) -> tuple[tuple[int, ...], ...] | None:
        """Backwards-compatible alias for :meth:`letter`.

        The method was renamed ``math_identifier`` → ``letter`` when the
        letter tables were generalised beyond math (§P4.5 / §R5+). Kept so
        external / legacy callers documented against the old name (and the
        ARCHITECTURE / math-redesign / math-boundaries docs that promise
        the alias) keep working.
        """
        return self.letter(ch)

    def bare_letter(self, ch: str) -> tuple[int, ...] | None:
        """Look up the bare letter cell for ``ch`` — **without** the
        script-class prefix.

        Used by callers that emit the prefix themselves (or skip it
        entirely): the Latin backend emits the prefix only on the first
        letter of a word and uses ``bare_letter`` for every subsequent
        character. Reads the same ``latin_letters`` / ``greek_letters``
        tables as :meth:`letter`. Returns ``None`` when the character
        isn't in any letter table.
        """
        for _key, letters in self._letter_buckets():
            cells = letters.get(ch)
            if cells is not None:
                return cells
        return None

    def letter_class(self, ch: str) -> str | None:
        """Return the script-class bucket key for a letter character —
        ``"latin_lower"`` / ``"latin_upper"`` / ``"greek_lower"`` /
        ``"greek_upper"`` — or ``None`` when the character isn't in any
        letter table.

        This is the key the letter-sign rule (《盲文常用数学符号》二.规则1)
        partitions on: consecutive letters of the same class share one
        ``letter_prefix.{class}`` sign; a class change starts a new sign.
        """
        for key, letters in self._letter_buckets():
            if ch in letters:
                return key
        return None

    def _letter_buckets(
        self,
    ) -> tuple[tuple[str, dict[str, tuple[int, ...]]], ...]:
        """The four (script, case) letter tables, keyed by the
        ``letter_prefix.*`` class name each maps to."""
        return (
            ("latin_lower", self.latin_letters.get("lower", {})),
            ("latin_upper", self.latin_letters.get("upper", {})),
            ("greek_lower", self.greek_letters.get("lower", {})),
            ("greek_upper", self.greek_letters.get("upper", {})),
        )

    def _compose_letter(
        self, ch: str
    ) -> tuple[tuple[int, ...], ...] | None:
        """Build the cell sequence for a single letter character.

        Walks the four (script, case) buckets, prepends the
        corresponding ``letter_prefix.{script}_{case}`` cells when set
        in math_structures, and returns the result. ``None`` if the
        character isn't in any letter table.
        """
        for key, letters in self._letter_buckets():
            dots = letters.get(ch)
            if dots is None:
                continue
            prefix = self.math_structures.get(f"letter_prefix.{key}", ())
            return tuple(prefix) + (dots,)
        return None

    # -- Math function --------------------------------------------------

    def math_function(self, name: str) -> tuple[tuple[int, ...], ...] | None:
        """Look up a function-name abbreviation (sin / cos / log / ...).

        Returns ``None`` if the function isn't in the table — callers
        then fall back to spelling the name letter-by-letter via
        :meth:`letter`.
        """
        return self.math_functions.get(name)

    def math_function_big_op(self, name: str) -> bool:
        """Whether a function name acts as a "big operator" (its
        sub/superscripts go above/below rather than alongside).
        True for lim/max/min/sup/inf in cn_current.
        """
        return self.math_function_big_op_flags.get(name, False)

    def math_function_script_prefix(self, name: str) -> bool:
        """Whether a function takes a 46-dot prefix in front of its
        sub/superscript indicator. True for ``lim`` in cn_current."""
        return self.math_function_script_prefix_flags.get(name, False)

    # -- Math structures -----------------------------------------------

    def math_structure(self, name: str) -> tuple[tuple[int, ...], ...]:
        """Return the cell sequence for a named structural marker.

        Names use dotted form (``"fraction.bar"``, ``"script.sub"``,
        ``"sqrt.open"``, ``"letter_prefix.latin_lower"``, ...). Returns
        an empty tuple if the marker is absent — callers should treat
        that as "skip this marker" rather than crashing.
        """
        return self.math_structures.get(name, ())

    # -- Music (BANA 2015 Music Braille Code) ---------------------------

    def music_cell(
        self, topic: str, entity: str
    ) -> tuple[tuple[int, ...], ...] | None:
        """Look up a BANA music cell sequence by topic + entity name.

        ``topic`` is one of the file names under ``resources/music/``
        (e.g. ``"notes"``, ``"octaves"``, ``"clefs"``); subdirectory
        topics are prefixed (``"instruments.keyboard"``,
        ``"vocal.music_lines"``).

        ``entity`` is the entry name inside the topic (e.g.
        ``"whole_or_16th_C"``, ``"g_clef_treble"``).

        Returns the cell sequence (one or more cells, each a dot tuple),
        or ``None`` if the topic/entity isn't loaded.
        """
        return self.music.get(topic, {}).get(entity)

    def music_topic(
        self, topic: str
    ) -> dict[str, tuple[tuple[int, ...], ...]]:
        """Return all entries in a topic, keyed by entity name. Empty
        dict if the topic isn't loaded — handy for iterating all
        entries of a kind (e.g. every note pitch / time value)."""
        return self.music.get(topic, {})

    def music_spec(self, topic: str, section: str) -> Any:
        """Return a declarative rule section for a music topic, or ``None``.

        Unlike :meth:`music_cell` / :meth:`music_topic` (cell tables),
        spec sections carry rule-data the backend consumes directly — e.g.
        ``music_spec("chord_symbols", "kind_spec")`` returns the MusicXML
        ``<kind>`` -> emit-recipe map (a chord-kind keyed dict of
        ``[type, payload]`` pairs). Sourced from ``_``-prefixed sections
        in the topic JSON (see :func:`_load_music_specs`)."""
        return self.music_specs.get(topic, {}).get(section)
