"""Parse a numeric-tone pinyin syllable into (initial, final, tone).

The Chinese braille backend looks up cells by **phonological**
initial + final, not by orthographic spelling. So we need to undo
the cosmetic ``w``/``y`` rules that pinyin uses for syllables that
start with a glide vowel:

* ``wu`` → no-initial + ``u``
* ``wa`` → no-initial + ``ua``
* ``yi`` → no-initial + ``i``
* ``yan`` → no-initial + ``ian``
* ``yu`` → no-initial + ``ü``
* ``yue`` → no-initial + ``üe``

Plus the standard pinyin contractions:

* ``ju`` ``qu`` ``xu`` (and derivatives) → ``j``/``q``/``x`` + ``ü...``
* ``iu`` → ``iou``
* ``ui`` → ``uei``
* ``un`` → ``uen``

The parser is intentionally a pure function — no profile or
context — so it can be tested exhaustively.
"""

from __future__ import annotations

from dataclasses import dataclass

# Longest-first so "zh" matches before "z", etc.
_INITIALS_LONGEST_FIRST: tuple[str, ...] = (
    "zh", "ch", "sh",
    "b", "p", "m", "f",
    "d", "t", "n", "l",
    "g", "k", "h",
    "j", "q", "x",
    "r",
    "z", "c", "s",
)

# Initials whose orthographic ``i`` is **syllabic** (the syllable carries
# no vowel — the consonant just sustains). In Chinese braille these
# syllables are written as initial + tone only, no final cell.
#
# * retroflex: zhi / chi / shi / ri  → [ʈ͡ʂɻ̩] / [ʈ͡ʂʰɻ̩] / [ʂɻ̩] / [ɻ̩]
# * dental sibilants: zi / ci / si    → [t͡sz̩] / [t͡sʰz̩] / [sz̩]
_SYLLABIC_I_INITIALS: frozenset[str] = frozenset({
    "zh", "ch", "sh", "r",
    "z", "c", "s",
})


@dataclass(frozen=True, slots=True)
class ParsedPinyin:
    """A syllable broken into phonological pieces.

    * ``initial`` is the consonant (``""`` for zero-initial / vowel-onset).
    * ``final`` is the rime in its **phonological** form (``ü`` not ``v``,
      ``iou`` not ``iu``, ``uei`` not ``ui``, etc.).
    * ``tone`` is ``"1"``..``"5"`` or ``""`` if absent. Tone ``"5"`` is the
      neutral tone (light tone).
    """

    initial: str
    final: str
    tone: str

    def has_initial(self) -> bool:
        return bool(self.initial)


def parse_pinyin(syllable: str) -> ParsedPinyin:
    """Decompose one pinyin syllable into (initial, final, tone).

    Accepts both numeric tone (``"wo3"``) and untoned (``"wo"``) input.
    The letter ``v`` is treated as ``ü``. Empty input raises
    :class:`ValueError`.
    """
    if not syllable:
        raise ValueError("empty syllable")

    s = syllable.strip().lower().replace("v", "ü")

    # Trailing tone digit (1..5).
    tone = ""
    if s and s[-1].isdigit():
        if s[-1] in "12345":
            tone = s[-1]
            s = s[:-1]
        else:
            raise ValueError(f"invalid tone digit in {syllable!r}")
    if not s:
        raise ValueError(f"no letters in {syllable!r}")

    # Apply w/y normalization first — handles glide-onset syllables.
    initial, final = _strip_initial(s)
    final = _normalize_final(initial, final)

    # Syllabic-i: zhi/chi/shi/ri/zi/ci/si have no real vowel — drop the
    # cosmetic "i" so the backend emits only the initial cell + tone.
    if initial in _SYLLABIC_I_INITIALS and final == "i":
        final = ""

    return ParsedPinyin(initial=initial, final=final, tone=tone)


def _strip_initial(s: str) -> tuple[str, str]:
    """Return (initial, remaining). Handles w/y as zero-initial markers."""
    if s.startswith(("w", "y")):
        return "", s  # final-normalization will rewrite the spelling
    for init in _INITIALS_LONGEST_FIRST:
        if s.startswith(init):
            return init, s[len(init) :]
    return "", s


def _normalize_final(initial: str, final: str) -> str:
    """Convert orthographic spellings to phonological finals.

    The transformations are:

    * ``w*``     → ``u``-onset family
    * ``y*``     → ``i``-onset (or ``ü``-onset) family
    * ``iu``     → ``iou``
    * ``ui``     → ``uei``
    * ``un``     → ``uen`` (only when there is a consonant initial;
      ``wen`` already mapped to ``uen`` above)
    """
    # w-* and y-* glide normalization (only when the spelling started
    # with that letter — initial="" and final still has the cosmetic
    # leading w/y).
    if not initial:
        if final.startswith("w"):
            rest = final[1:]
            # wu → u  (the syllable [u] in isolation);
            # wa/wo/.../weng → ua/uo/.../ueng.
            if rest == "u" or rest == "":
                final = "u"
            else:
                final = "u" + rest
        elif final.startswith("y"):
            rest = final[1:]
            if rest == "":
                final = "i"
            elif rest == "u" or rest.startswith("u"):
                # yu, yue, yuan, yun → ü, üe, üan, ün
                final = "ü" + rest[1:]
            elif rest.startswith("i"):
                # yi, yin, ying → i, in, ing
                final = rest
            else:
                # ya, ye, yao, you, yan, yang, yong → ia, ie, ...
                final = "i" + rest

    # j/q/x rule: orthographic "u" after these initials actually means ü.
    # Must run BEFORE the un→uen contraction so xun stays ün rather than üen.
    if initial in ("j", "q", "x") and final.startswith("u"):
        final = "ü" + final[1:]

    # iu / ui / un are *all* contracted forms. The phonological final
    # is the longer one — niu = n+iou, gui = g+uei, dun = d+uen, jiu
    # = j+iou, qiu = q+iou, xiu = x+iou. Apply the expansion whenever
    # there's a consonant initial; the jqx branch above has already
    # rewritten "u"-only finals to "ü", so qun's final is already "ün"
    # and never hits the "un → uen" rule.
    if initial:
        if final == "iu":
            final = "iou"
        elif final == "ui":
            final = "uei"
        elif final == "un":
            final = "uen"

    return final
