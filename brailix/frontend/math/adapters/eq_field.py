"""Word EQ field adapter — converts legacy ``EQ`` field codes to MathML.

Word 95/97-era equations are stored in ``.docx`` as field codes
inside ``<w:instrText>`` runs, e.g. ``eq \\f(1,2)`` or
``eq \\b\\lc\\{(\\a\\vs4\\al\\co1(sin x，x≥0，,x＋2，x<0，))``. They
predate OMML and the modern equation editor, but are still very
common in Chinese teaching materials and old worksheets.

Coverage: all 10 switches documented in the Microsoft EQ field
reference (``\\a \\b \\d \\f \\i \\l \\o \\r \\s \\x``). Output is a
single-line MathML string; unknown / malformed input is wrapped in
``<merror>`` via :func:`~brailix.frontend.math.utils.merror_wrap`
so the rest of the pipeline keeps moving.

The adapter is pure-stdlib (only :mod:`xml.etree.ElementTree`); the
docx input layer pulls the raw ``eq ...`` string out and hands it
over.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass

from brailix.core.context import MathContext
from brailix.frontend.math.adapters._atoms import tokenize_math_text
from brailix.frontend.math.utils import (
    _MATHML_NS,
    merror_wrap,
    mrow_wrap,
    mtext,
)

# Word *general field* switches that can trail an EQ instruction but are
# document-field formatting, not EQ math syntax: ``\* MERGEFORMAT`` /
# ``\* CHARFORMAT`` / ``\* Upper`` (the ``\*`` format switch + a keyword,
# inserted by Word's field dialog by default), ``\!`` (lock result), and
# the ``\#`` / ``\@`` numeric / date picture switches with a quoted arg.
# Stripped before tokenizing so they don't leak in as a stray identifier
# (``MERGEFORMAT`` → <mi>) or operator (``\!`` → factorial <mo>). The EQ
# *math* switches are all ``\<letter>`` (\f \r \s \b \a \i \x \o \d \l …),
# so matching only \*, \!, \#, \@ never touches them.
_GENERAL_FIELD_SWITCH_RE = re.compile(
    r"""\\(?:
          \*\s*[A-Za-z]+        # \* MERGEFORMAT / \* Upper / ...
        | [#@]\s*"[^"]*"        # \# "0.00" / \@ "M/d/yyyy"
        | !                     # \! lock-result
    )""",
    re.VERBOSE,
)


# ---------------------------------------------------------------------------
# AST
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Text:
    """A run of literal characters — split into mi/mn/mo at emit time."""
    text: str


@dataclass(slots=True)
class _Frac:
    num: list[Node]
    den: list[Node]


@dataclass(slots=True)
class _Brackets:
    left: str           # may be empty (no left bracket)
    right: str          # may be empty (no right bracket — used for cases)
    content: list[Node]


@dataclass(slots=True)
class _Array:
    cols: int
    align: str          # 'l', 'c', 'r'
    cells: list[list[Node]]   # row-major list of cell node-lists


@dataclass(slots=True)
class _Radical:
    index: list[Node] | None  # None = √ (no degree)
    radicand: list[Node]


@dataclass(slots=True)
class _Nary:
    op: str             # ∫ ∑ ∏ or custom char
    lower: list[Node]
    upper: list[Node]
    integrand: list[Node]
    limits_above: bool  # True for sum/product; False for integral


@dataclass(slots=True)
class _Script:
    kind: str           # 'sup' or 'sub'
    content: list[Node]


@dataclass(slots=True)
class _Box:
    sides: frozenset[str]   # subset of {'top','bottom','left','right'}; empty = all four
    content: list[Node]


@dataclass(slots=True)
class _Overstrike:
    items: list[list[Node]]
    align: str          # 'l', 'c', 'r'


@dataclass(slots=True)
class _Displace:
    points: int         # signed (positive = forward, negative = backward)


@dataclass(slots=True)
class _List:
    items: list[list[Node]]


@dataclass(slots=True)
class _Unknown:
    name: str
    args: list[list[Node]]


Node = (
    _Text | _Frac | _Brackets | _Array | _Radical | _Nary
    | _Script | _Box | _Overstrike | _Displace | _List | _Unknown
)


# ---------------------------------------------------------------------------
# Adapter entry point
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class EqFieldMathSourceAdapter:
    """Convert a Word ``eq`` field instruction text into MathML.

    The ``formula`` argument is the raw ``instrText`` content, with or
    without the leading ``eq ``. Internal whitespace is preserved; the
    parser only treats unescaped ``\\``, ``(``, ``)``, ``,`` and ``;``
    as syntactic markers.
    """

    source: str = "eq_field"

    def to_mathml(self, formula: str | bytes, ctx: MathContext | None = None) -> str:
        if isinstance(formula, bytes):
            try:
                formula = formula.decode("utf-8")
            except UnicodeDecodeError:
                return merror_wrap(repr(formula), reason="non-utf8 bytes")
        text = formula.strip()
        if not text:
            return merror_wrap("", reason="empty input")
        # Strip the ``eq`` prefix (case-insensitive, may be absent).
        m = re.match(r"(?is)eq\b\s*", text)
        if m:
            text = text[m.end():]
        # Drop Word general field switches (\* MERGEFORMAT, \!, \# / \@
        # pictures) before tokenizing — see _GENERAL_FIELD_SWITCH_RE.
        text = _GENERAL_FIELD_SWITCH_RE.sub("", text).strip()
        if not text:
            # A field that is only the ``eq`` prefix and/or general field
            # switches (e.g. ``eq \* MERGEFORMAT``) becomes empty here, not
            # at the early guard above — soft-fail like any empty input.
            return merror_wrap("", reason="empty input")
        try:
            tokens = _tokenize(text)
            parser = _Parser(tokens)
            nodes = parser.parse_sequence(stop_on=())
        except _EqParseError as e:
            return merror_wrap(text, reason=f"eq parse error: {e}")
        except Exception as e:  # noqa: BLE001 — keep adapter soft-failing
            return merror_wrap(text, reason=f"eq convert error: {e}")
        math = ET.Element("math", {"xmlns": _MATHML_NS})
        try:
            children = _emit_sequence(nodes)
        except Exception as e:  # noqa: BLE001
            return merror_wrap(text, reason=f"eq emit error: {e}")
        for c in children:
            math.append(c)
        return ET.tostring(math, encoding="unicode")


def _load() -> EqFieldMathSourceAdapter:
    return EqFieldMathSourceAdapter()


# ---------------------------------------------------------------------------
# Tokenizer
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class _Tok:
    type: str           # 'switch' | 'esc' | 'char' | 'lparen' | 'rparen' | 'comma' | 'semi'
    value: str


class _EqParseError(Exception):
    pass


def _tokenize(s: str) -> list[_Tok]:
    """Lex the EQ field body.

    Special characters: ``\\``, ``(``, ``)``, ``,``, ``;``. Everything
    else is a single-char ``char`` token. ``\\`` + letter(s) becomes a
    ``switch`` token whose value is the lowercase switch name (we treat
    switches case-insensitively per Word). ``\\`` + non-letter becomes
    an ``esc`` token carrying the next character literally (so
    ``\\,`` ``\\(`` ``\\\\`` ``\\{`` all survive into the parser).
    """
    out: list[_Tok] = []
    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if ch == "\\":
            j = i + 1
            if j < n and s[j].isalpha():
                k = j
                while k < n and s[k].isalpha():
                    k += 1
                out.append(_Tok("switch", s[j:k].lower()))
                i = k
            elif j < n:
                out.append(_Tok("esc", s[j]))
                i = j + 1
            else:
                # Trailing backslash — treat as literal text.
                out.append(_Tok("char", "\\"))
                i += 1
        elif ch == "(":
            out.append(_Tok("lparen", "("))
            i += 1
        elif ch == ")":
            out.append(_Tok("rparen", ")"))
            i += 1
        elif ch == ",":
            out.append(_Tok("comma", ","))
            i += 1
        elif ch == ";":
            out.append(_Tok("semi", ";"))
            i += 1
        else:
            out.append(_Tok("char", ch))
            i += 1
    return out


# ---------------------------------------------------------------------------
# Parser
# ---------------------------------------------------------------------------


# Cap parser recursion so a pathologically deep field (\f(\f(\f(...)))) bails
# out to flat text at this depth instead of overflowing Python's stack — the
# broad except in `to_mathml` would otherwise catch the RecursionError and
# collapse the WHOLE formula to a single <merror>. Capping the parser also
# bounds the AST depth, hence the emitter's recursion. 64 matches the MTEF
# adapter's cap and is far beyond any real Word equation.
_MAX_PARSE_DEPTH = 64


class _Parser:
    """Recursive-descent EQ field parser → AST node list."""

    def __init__(self, tokens: list[_Tok]) -> None:
        self.toks = tokens
        self.pos = 0
        self._depth = 0

    # --- low-level helpers --------------------------------------------------

    def _peek(self, offset: int = 0) -> _Tok | None:
        idx = self.pos + offset
        if 0 <= idx < len(self.toks):
            return self.toks[idx]
        return None

    def _consume(self) -> _Tok:
        t = self.toks[self.pos]
        self.pos += 1
        return t

    def _skip_ws(self) -> None:
        while True:
            t = self._peek()
            if t is None or t.type != "char" or not t.value.isspace():
                return
            self._consume()

    def _read_int(self) -> int | None:
        """Consume contiguous digit ``char`` tokens; return the int or None."""
        digits: list[str] = []
        while True:
            t = self._peek()
            if t is None or t.type != "char" or not t.value.isdigit():
                break
            digits.append(self._consume().value)
        if not digits:
            return None
        return int("".join(digits))

    def _read_bracket_char(self) -> str:
        """Read the bracket character that follows ``\\lc``/``\\rc``/``\\bc``.

        Word's EQ field syntax escapes the bracket with a backslash:
        ``\\lc\\{`` means the left character is ``{``. We accept either
        an ``esc`` token (the well-formed case) or a single raw ``char``
        token (graceful fallback for ``\\lc{`` typos).
        """
        self._skip_ws()
        t = self._peek()
        if t is None:
            return ""
        if t.type == "esc":
            self._consume()
            return t.value
        if t.type == "char":
            self._consume()
            return t.value
        # Punctuation tokens — return their literal value too.
        if t.type in ("lparen", "rparen", "comma", "semi"):
            self._consume()
            return t.value
        return ""

    def _peek_option_switch(self) -> str | None:
        """Name of a leading ``[options]`` switch, without consuming it.

        Returns the switch's name (``"co"`` / ``"lc"`` / ...) when the next
        token — after skipping whitespace — is a ``switch``, else ``None``.
        Every construct handler shares the same ``[options]`` preamble: it
        loops ``while (opt := p._peek_option_switch()) is not None`` and
        consumes only the options it recognises, ``break``-ing on the first
        it does not so that token is left for the outer parser (the
        historical behaviour — an unknown switch becomes a sibling node, not
        an error). Peeking (not consuming) here is what preserves that.
        """
        self._skip_ws()
        t = self._peek()
        if t is None or t.type != "switch":
            return None
        return t.value

    # --- argument list ------------------------------------------------------

    def _consume_balanced(self) -> str:
        """Consume tokens through the ``)`` matching an already-consumed
        ``(``, returning their reconstructed literal text.

        Used to bail out of pathologically deep nesting without recursing:
        the deepest subtree degrades to flat text instead of overflowing
        Python's stack (which the broad ``except`` in :meth:`to_mathml`
        would otherwise turn into a single ``<merror>`` for the whole
        formula). Iterative — never recurses itself.
        """
        depth = 1
        chunks: list[str] = []
        while depth > 0:
            t = self._peek()
            if t is None:
                break
            self._consume()
            if t.type == "lparen":
                depth += 1
                chunks.append("(")
            elif t.type == "rparen":
                depth -= 1
                if depth == 0:
                    break
                chunks.append(")")
            elif t.type == "switch":
                chunks.append("\\" + t.value)
            else:
                chunks.append(t.value)
        return "".join(chunks)

    def _parse_arg_list(self) -> list[list[Node]]:
        """Parse ``(arg, arg, ...)``. Returns a list of arg-node-lists.

        Returns an empty list if the next token is not ``(`` — callers
        treat that as "switch takes no parenthesized args".
        """
        self._skip_ws()
        t = self._peek()
        if t is None or t.type != "lparen":
            return []
        self._consume()  # (
        if self._depth >= _MAX_PARSE_DEPTH:
            # Too deep: stop recursing. Consume the balanced (...) as flat
            # text so the deepest subtree degrades to text rather than
            # overflowing the stack and collapsing the whole formula. No
            # real Word equation nests anywhere near this far.
            return [[_Text(self._consume_balanced())]]
        self._depth += 1
        try:
            args: list[list[Node]] = []
            # Empty arg list ``()`` produces a single empty arg.
            while True:
                arg = self.parse_sequence(stop_on=("comma", "semi", "rparen"))
                args.append(arg)
                t = self._peek()
                if t is None:
                    # EOF — soft fail, treat as if rparen.
                    break
                if t.type in ("comma", "semi"):
                    self._consume()
                    continue
                if t.type == "rparen":
                    self._consume()
                    break
                # Defensive — shouldn't happen since parse_sequence stops here.
                break
        finally:
            self._depth -= 1
        return args

    # --- top-level / sequence parser ---------------------------------------

    def parse_sequence(self, stop_on: tuple[str, ...]) -> list[Node]:
        """Parse nodes until EOF or a token in ``stop_on``."""
        out: list[Node] = []
        while True:
            t = self._peek()
            if t is None:
                return out
            if t.type in stop_on:
                return out
            if t.type == "switch":
                self._consume()
                node = self._dispatch_switch(t.value)
                if node is not None:
                    out.append(node)
                continue
            if t.type == "esc":
                self._consume()
                out.append(_Text(t.value))
                continue
            if t.type == "char":
                self._consume()
                out.append(_Text(t.value))
                continue
            if t.type == "lparen":
                # A bare ``(`` outside any switch context — keep as text.
                self._consume()
                out.append(_Text("("))
                continue
            if t.type == "rparen":
                # Unmatched closer — emit as text to stay forgiving.
                self._consume()
                out.append(_Text(")"))
                continue
            if t.type in ("comma", "semi"):
                # Outside an arg list these are just text.
                self._consume()
                out.append(_Text(t.value))
                continue
            # Unknown token type — skip.
            self._consume()

    def _dispatch_switch(self, name: str) -> Node | None:
        """Route ``\\name`` to its handler. Unknown switches are wrapped."""
        handler = _SWITCH_HANDLERS.get(name)
        if handler is None:
            args = self._parse_arg_list()
            return _Unknown(name, args)
        return handler(self)


# ---------------------------------------------------------------------------
# Per-switch handlers
# ---------------------------------------------------------------------------


def _handle_frac(p: _Parser) -> Node:
    """``\\f(num,den)`` — fraction."""
    args = p._parse_arg_list()
    num = args[0] if len(args) > 0 else []
    den = args[1] if len(args) > 1 else []
    return _Frac(num=num, den=den)


def _handle_radical(p: _Parser) -> Node:
    """``\\r(rad)`` = √rad ; ``\\r(n, rad)`` = ⁿ√rad."""
    args = p._parse_arg_list()
    if len(args) == 0:
        return _Radical(index=None, radicand=[])
    if len(args) == 1:
        return _Radical(index=None, radicand=args[0])
    return _Radical(index=args[0], radicand=args[1])


def _handle_brackets(p: _Parser) -> Node:
    """``\\b [\\lc\\X] [\\rc\\Y] [\\bc\\X] (expr)`` — bracket builder.

    Semantics:
      * No options                        → ``(`` and ``)``.
      * ``\\lc\\X`` only                  → left = X, right = empty
                                            (lets cases-style ``\\b\\lc\\{(...)``
                                            give a single left brace).
      * ``\\rc\\Y`` only                  → left = empty, right = Y.
      * ``\\bc\\X``                       → left = right = X.
      * ``\\lc\\X`` + ``\\rc\\Y``         → both, as given.
    """
    has_lc = has_rc = False
    left = right = ""
    while (opt := p._peek_option_switch()) is not None:
        if opt == "lc":
            p._consume()
            left = p._read_bracket_char()
            has_lc = True
        elif opt == "rc":
            p._consume()
            right = p._read_bracket_char()
            has_rc = True
        elif opt == "bc":
            p._consume()
            ch = p._read_bracket_char()
            left = right = ch
            has_lc = has_rc = True
        else:
            break
    if not has_lc and not has_rc:
        left, right = "(", ")"
    args = p._parse_arg_list()
    content = args[0] if args else []
    return _Brackets(left=left, right=right, content=content)


def _handle_array(p: _Parser) -> Node:
    """``\\a [options] (cell1, cell2, ...)`` — array, row-major.

    Options:
      * ``\\co N``  columns (default 1)
      * ``\\vs N``  vertical space (ignored — visual hint)
      * ``\\hs N``  horizontal space (ignored)
      * ``\\al`` / ``\\ac`` / ``\\ar``  alignment (default center)
    """
    cols = 1
    align = "c"
    while (opt := p._peek_option_switch()) is not None:
        if opt == "co":
            p._consume()
            val = p._read_int()
            if val is not None and val > 0:
                cols = val
        elif opt in ("vs", "hs"):
            p._consume()
            p._read_int()
        elif opt == "al":
            p._consume()
            align = "l"
        elif opt == "ac":
            p._consume()
            align = "c"
        elif opt == "ar":
            p._consume()
            align = "r"
        else:
            break
    cells = p._parse_arg_list()
    return _Array(cols=cols, align=align, cells=cells)


def _handle_integral(p: _Parser) -> Node:
    """``\\i [options] (lower, upper, integrand)`` — n-ary operator.

    Options:
      * ``\\su``       summation (Σ) — limits typeset above/below.
      * ``\\pr``       product   (Π) — limits typeset above/below.
      * ``\\in``       inline mode (cosmetic — ignored).
      * ``\\fc\\X``    fixed-size operator character X.
      * ``\\vc\\X``    variable-size operator character X.

    Default (no kind switch) is integral ``∫`` with sub/superscript
    limit position.
    """
    op = "∫"
    limits_above = False
    while (opt := p._peek_option_switch()) is not None:
        if opt == "su":
            p._consume()
            op = "∑"
            limits_above = True
        elif opt == "pr":
            p._consume()
            op = "∏"
            limits_above = True
        elif opt == "in":
            p._consume()
        elif opt in ("fc", "vc"):
            p._consume()
            ch = p._read_bracket_char()
            if ch:
                op = ch
        else:
            break
    args = p._parse_arg_list()
    lower = args[0] if len(args) > 0 else []
    upper = args[1] if len(args) > 1 else []
    integrand = args[2] if len(args) > 2 else []
    return _Nary(op=op, lower=lower, upper=upper, integrand=integrand,
                 limits_above=limits_above)


def _handle_script(p: _Parser) -> Node:
    """``\\s \\up N (expr)`` or ``\\s \\do N (expr)`` — script.

    Word's EQ field uses ``\\up`` for "raise by N points" (rendered as
    superscript) and ``\\do`` for "lower" (subscript). The point value
    affects only visual height — we treat any non-zero N the same.
    Options ``\\ai`` / ``\\di`` (add space above/below the line) take
    the same optional point count and do not alter the kind.
    """
    kind = "sup"  # default if no direction switch given
    while (opt := p._peek_option_switch()) is not None:
        if opt == "up":
            p._consume()
            p._read_int()
            kind = "sup"
        elif opt == "do":
            p._consume()
            p._read_int()
            kind = "sub"
        elif opt in ("ai", "di"):
            p._consume()
            p._read_int()
        else:
            break
    args = p._parse_arg_list()
    content = args[0] if args else []
    return _Script(kind=kind, content=content)


def _handle_box(p: _Parser) -> Node:
    """``\\x [\\to] [\\bo] [\\le] [\\ri] (expr)`` — box / borders.

    No side switches means draw all four sides; any subset selects only
    those sides.
    """
    sides: set[str] = set()
    while (opt := p._peek_option_switch()) is not None:
        if opt == "to":
            p._consume()
            sides.add("top")
        elif opt == "bo":
            p._consume()
            sides.add("bottom")
        elif opt == "le":
            p._consume()
            sides.add("left")
        elif opt == "ri":
            p._consume()
            sides.add("right")
        else:
            break
    args = p._parse_arg_list()
    content = args[0] if args else []
    return _Box(sides=frozenset(sides), content=content)


def _handle_overstrike(p: _Parser) -> Node:
    """``\\o [\\al|\\ac|\\ar] (item1, item2, ...)`` — characters
    overlaid at the same position. Used for cancellation marks,
    composite glyphs, accents.
    """
    align = "c"
    while (opt := p._peek_option_switch()) is not None:
        if opt == "al":
            p._consume()
            align = "l"
        elif opt == "ac":
            p._consume()
            align = "c"
        elif opt == "ar":
            p._consume()
            align = "r"
        else:
            break
    items = p._parse_arg_list()
    return _Overstrike(items=items, align=align)


def _handle_displace(p: _Parser) -> Node:
    """``\\d [\\fo N | \\ba N] [\\li]`` — horizontal displacement.

    ``\\li`` (draw a line) is accepted but not represented in MathML —
    visual-only constructs don't translate to braille semantically.
    Takes no parenthesized argument.
    """
    points = 0
    while (opt := p._peek_option_switch()) is not None:
        if opt == "fo":
            p._consume()
            n = p._read_int()
            points = n if n is not None else 0
        elif opt == "ba":
            p._consume()
            n = p._read_int()
            points = -(n if n is not None else 0)
        elif opt == "li":
            p._consume()
        else:
            break
    return _Displace(points=points)


def _handle_list(p: _Parser) -> Node:
    """``\\l (item1, item2, ...)`` — grouping. Items are concatenated
    in source order; the wrapper exists only so other Word features
    (FORMULA references) can address the group as one. For MathML we
    flatten to ``<mrow>``.
    """
    items = p._parse_arg_list()
    return _List(items=items)


_SWITCH_HANDLERS: dict[str, Callable[[_Parser], Node]] = {
    "a": _handle_array,
    "b": _handle_brackets,
    "d": _handle_displace,
    "f": _handle_frac,
    "i": _handle_integral,
    "l": _handle_list,
    "o": _handle_overstrike,
    "r": _handle_radical,
    "s": _handle_script,
    "x": _handle_box,
}


# ---------------------------------------------------------------------------
# MathML emitter
# ---------------------------------------------------------------------------


def _collapse_text(nodes: list[Node]) -> list[Node]:
    """Merge consecutive ``_Text`` nodes so ``2x+1`` lexes as one run.

    The parser emits one ``_Text`` per source character — that keeps the
    tokenizer simple but makes ``_tokenize_text`` produce one MathML
    atom per character. Merging first lets ``2x+1`` become
    ``<mn>2</mn><mi>x</mi><mo>+</mo><mn>1</mn>``.
    """
    out: list[Node] = []
    buf: list[str] = []
    for node in nodes:
        if isinstance(node, _Text):
            buf.append(node.text)
            continue
        if buf:
            out.append(_Text("".join(buf)))
            buf = []
        out.append(node)
    if buf:
        out.append(_Text("".join(buf)))
    return out


def _emit_sequence(nodes: list[Node]) -> list[ET.Element]:
    """Convert a flat node list to MathML atoms."""
    out: list[ET.Element] = []
    for node in _collapse_text(nodes):
        out.extend(_emit(node))
    return out


def _emit_mrow(nodes: list[Node]) -> ET.Element:
    """Wrap ``nodes`` in an ``<mrow>`` (or unwrap if exactly one child)."""
    return mrow_wrap(_emit_sequence(nodes))


def _emit(node: Node) -> list[ET.Element]:
    if isinstance(node, _Text):
        # EQ-field syntax uses ``,`` as the argument separator (``\f(1,2)``
        # is two operands), so a comma must never group into a number.
        return tokenize_math_text(node.text, comma_in_number=False)
    if isinstance(node, _Frac):
        mfrac = ET.Element("mfrac")
        mfrac.append(_emit_mrow(node.num))
        mfrac.append(_emit_mrow(node.den))
        return [mfrac]
    if isinstance(node, _Brackets):
        mrow = ET.Element("mrow")
        if node.left:
            op = ET.Element("mo", {"fence": "true"})
            op.text = node.left
            mrow.append(op)
        for c in _emit_sequence(node.content):
            mrow.append(c)
        if node.right:
            op = ET.Element("mo", {"fence": "true"})
            op.text = node.right
            mrow.append(op)
        return [mrow]
    if isinstance(node, _Array):
        return [_emit_array(node)]
    if isinstance(node, _Radical):
        if node.index is None:
            msqrt = ET.Element("msqrt")
            msqrt.append(_emit_mrow(node.radicand))
            return [msqrt]
        mroot = ET.Element("mroot")
        mroot.append(_emit_mrow(node.radicand))
        mroot.append(_emit_mrow(node.index))
        return [mroot]
    if isinstance(node, _Nary):
        return [_emit_nary(node)]
    if isinstance(node, _Script):
        tag = "msup" if node.kind == "sup" else "msub"
        elem = ET.Element(tag)
        # Empty base — the script attaches to whatever comes before it
        # in the surrounding text, which the braille backend reads
        # sequentially.
        elem.append(ET.Element("mrow"))
        elem.append(_emit_mrow(node.content))
        return [elem]
    if isinstance(node, _Box):
        notation = (
            "box"
            if not node.sides or node.sides == frozenset({"top", "bottom", "left", "right"})
            else " ".join(sorted(node.sides))
        )
        elem = ET.Element("menclose", {"notation": notation})
        elem.append(_emit_mrow(node.content))
        return [elem]
    if isinstance(node, _Overstrike):
        return [_emit_overstrike(node)]
    if isinstance(node, _Displace):
        if node.points == 0:
            return []
        elem = ET.Element("mspace", {"width": f"{node.points}pt"})
        return [elem]
    if isinstance(node, _List):
        # Flatten — items are simply concatenated.
        out: list[ET.Element] = []
        for item in node.items:
            out.extend(_emit_sequence(item))
        if len(out) == 1:
            return out
        mrow = ET.Element("mrow")
        for c in out:
            mrow.append(c)
        return [mrow]
    if isinstance(node, _Unknown):
        # Emit a placeholder so the user sees that something was
        # dropped instead of silently missing content.
        return [mtext("\\" + node.name)]
    raise _EqParseError(f"unhandled node: {type(node).__name__}")


def _emit_array(node: _Array) -> ET.Element:
    """Lay cells out row-major into an ``<mtable>``."""
    mtable = ET.Element("mtable")
    cols = max(1, node.cols)
    # Map our align letter to MathML's columnalign vocabulary.
    align_word = {"l": "left", "c": "center", "r": "right"}.get(node.align, "center")
    mtable.set("columnalign", align_word)
    cells = node.cells
    for row_start in range(0, len(cells), cols):
        row_cells = cells[row_start:row_start + cols]
        mtr = ET.Element("mtr")
        for cell in row_cells:
            mtd = ET.Element("mtd")
            for c in _emit_sequence(cell):
                mtd.append(c)
            mtr.append(mtd)
        mtable.append(mtr)
    return mtable


def _emit_nary(node: _Nary) -> ET.Element:
    """Build ``<munderover>`` or ``<msubsup>`` + integrand."""
    op = ET.Element("mo")
    op.text = node.op
    has_lower = bool(node.lower)
    has_upper = bool(node.upper)
    if not has_lower and not has_upper:
        scripted: ET.Element = op
    elif has_lower and has_upper:
        tag = "munderover" if node.limits_above else "msubsup"
        scripted = ET.Element(tag)
        scripted.append(op)
        scripted.append(_emit_mrow(node.lower))
        scripted.append(_emit_mrow(node.upper))
    elif has_lower:
        tag = "munder" if node.limits_above else "msub"
        scripted = ET.Element(tag)
        scripted.append(op)
        scripted.append(_emit_mrow(node.lower))
    else:  # has_upper only
        tag = "mover" if node.limits_above else "msup"
        scripted = ET.Element(tag)
        scripted.append(op)
        scripted.append(_emit_mrow(node.upper))
    mrow = ET.Element("mrow")
    mrow.append(scripted)
    for c in _emit_sequence(node.integrand):
        mrow.append(c)
    return mrow


def _emit_overstrike(node: _Overstrike) -> ET.Element:
    """Approximate overstrike as ``<mover>`` for two items, else
    ``<menclose notation="updiagonalstrike">`` of the first item
    (matches the common "cancel a term" usage), with the remaining
    items appended after — never throw away content even when MathML
    has no precise primitive."""
    items = [item for item in node.items if item]
    if len(items) == 0:
        return ET.Element("mrow")
    if len(items) == 1:
        return _emit_mrow(items[0])
    if len(items) == 2:
        mover = ET.Element("mover", {"accent": "false"})
        mover.append(_emit_mrow(items[0]))
        mover.append(_emit_mrow(items[1]))
        return mover
    # 3+ items — stack via successive mover so all content survives.
    base = _emit_mrow(items[0])
    for overlay in items[1:]:
        mover = ET.Element("mover", {"accent": "false"})
        mover.append(base)
        mover.append(_emit_mrow(overlay))
        base = mover
    return base
