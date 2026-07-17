"""Property-based tests for the MathML normalizer.

The normalizer's contract to the backend has three load-bearing clauses:

* **it never raises** — malformed input degrades to an in-band ``<merror>``
  document (the pipeline keeps running, the failure stays visible);
* **it is idempotent** — normalizing its own output changes nothing, so a
  tree that round-trips through serialization (caches, ``.blx`` files,
  editor round-trips) can be re-normalized safely;
* **its output upholds the shapes the backend dispatch assumes** — no
  namespace-qualified tags, no presentational wrappers, no invisible
  operators, no redundant singleton ``<mrow>``, provenance attributes
  intact.

Handler-specific rewrites are example-tested in ``test_normalizer.py``;
this module drives generated trees (including arity violations, unknown
tags, mixed content and junk strings) through the full pass stack.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET

import pytest

pytest.importorskip("hypothesis")

from hypothesis import given, settings
from hypothesis import strategies as st

from brailix.core._xml import safe_fromstring
from brailix.frontend.math.normalizer import normalize
from brailix.frontend.math.utils import _MATHML_NS

_SPAN_ATTR = "data-bk-span"

# Unicode invisible operators (function application / times / separator /
# plus) — the normalizer drops a bare <mo> holding one.
_INVISIBLE = {"⁡", "⁢", "⁣", "⁤"}


def _elem(
    tag: str,
    text: str | None = None,
    attrib: dict[str, str] | None = None,
    children: tuple[ET.Element, ...] | list[ET.Element] = (),
) -> ET.Element:
    e = ET.Element(tag, dict(attrib or {}))
    if text is not None:
        e.text = text
    for c in children:
        e.append(c)
    return e


_ident_texts = st.sampled_from(["x", "y", "α", "AB", "f"])
_num_texts = st.sampled_from(["0", "7", "12", "3.5", "1,000"])
_op_texts = st.sampled_from(["+", "-", "=", "<", ",", "∘", "⁡", "⁢", "⁣"])
_text_texts = st.sampled_from(["sin", "if", " ", "lim", "当"])


@st.composite
def _leaves(draw: st.DrawFn) -> ET.Element:
    kind = draw(
        st.sampled_from(["mi", "mn", "mo", "mtext", "mspace", "mphantom", "mfoo"])
    )
    attrib: dict[str, str] = {}
    if kind in ("mi", "mn") and draw(st.booleans()):
        attrib[_SPAN_ATTR] = draw(st.sampled_from(["0,1", "3,7"]))
    if kind == "mi":
        return _elem("mi", draw(_ident_texts), attrib)
    if kind == "mn":
        return _elem("mn", draw(_num_texts), attrib)
    if kind == "mo":
        return _elem("mo", draw(_op_texts))
    if kind == "mtext":
        return _elem("mtext", draw(_text_texts))
    if kind == "mspace":
        return _elem(
            "mspace",
            attrib=draw(
                st.sampled_from([{}, {"width": "1em"}, {"linebreak": "newline"}])
            ),
        )
    if kind == "mphantom":
        return _elem("mphantom")
    # An element no MathML dialect defines — must flow through untouched
    # (rejecting it is the backend's decision, in-band).
    return _elem("mfoo", draw(st.one_of(st.none(), _ident_texts)))


def _containers(children: st.SearchStrategy[ET.Element]) -> st.SearchStrategy[ET.Element]:
    pair = st.tuples(children, children)
    return st.one_of(
        # mrow occasionally carries mixed-content text — invalid-ish MathML
        # the normalizer must survive (and must NOT collapse away).
        st.builds(
            lambda ks, txt: _elem("mrow", txt, None, ks),
            st.lists(children, max_size=3),
            st.sampled_from([None, " ", "ab"]),
        ),
        # Deliberate arity violations (a 1- or 3-child mfrac) ride along.
        st.builds(lambda ks: _elem("mfrac", None, None, ks), st.lists(children, min_size=1, max_size=3)),
        st.builds(lambda p: _elem("msup", None, None, p), pair),
        st.builds(lambda p: _elem("msub", None, None, p), pair),
        st.builds(lambda ks: _elem("msqrt", None, None, ks), st.lists(children, min_size=1, max_size=2)),
        st.builds(
            lambda ks: _elem("mstyle", None, {"displaystyle": "true"}, ks),
            st.lists(children, min_size=1, max_size=2),
        ),
        st.builds(
            lambda ks: _elem("mpadded", None, {"width": "2em"}, ks),
            st.lists(children, min_size=1, max_size=2),
        ),
        st.builds(lambda ks: _elem("merror", None, None, ks), st.lists(children, max_size=1)),
    )


_subtrees = st.recursive(_leaves(), _containers, max_leaves=10)


@st.composite
def math_documents(draw: st.DrawFn) -> str:
    kids = draw(st.lists(_subtrees, max_size=3))
    with_ns = draw(st.booleans())
    root = _elem("math", None, {"xmlns": _MATHML_NS} if with_ns else None, kids)
    return ET.tostring(root, encoding="unicode")


def _tree_equal(a: ET.Element, b: ET.Element) -> bool:
    return (
        a.tag == b.tag
        and dict(a.attrib) == dict(b.attrib)
        and (a.text or "") == (b.text or "")
        and (a.tail or "") == (b.tail or "")
        and len(a) == len(b)
        and all(_tree_equal(ca, cb) for ca, cb in zip(a, b, strict=True))
    )


class TestNormalizeTotality:
    @given(doc=math_documents())
    def test_never_raises_on_generated_mathml(self, doc: str) -> None:
        root = normalize(doc)
        assert isinstance(root, ET.Element)

    @settings(max_examples=150)
    @given(junk=st.text(max_size=60))
    def test_never_raises_on_junk_and_reports_in_band(self, junk: str) -> None:
        # The soft-failure contract: whatever the input — control characters,
        # half-open tags, plain prose — the caller gets a tree, and a parse
        # failure is reported IN the tree (<merror>), never as an exception.
        root = normalize(junk)
        assert isinstance(root, ET.Element)
        try:
            safe_fromstring(junk)
            parsed = True
        except Exception:
            parsed = False
        if not parsed:
            assert root.tag == "math"
            assert any(el.tag == "merror" for el in root.iter())

    @given(doc=math_documents())
    def test_deterministic(self, doc: str) -> None:
        assert _tree_equal(normalize(doc), normalize(doc))


class TestNormalizeIdempotence:
    @given(doc=math_documents())
    def test_normalizing_own_output_is_identity(self, doc: str) -> None:
        # Caches and .blx round-trips re-serialize normalized trees; feeding
        # one back through the normalizer must be a no-op, or a document
        # would silently change on every open/save cycle.
        once = normalize(doc)
        twice = normalize(ET.tostring(once, encoding="unicode"))
        assert _tree_equal(once, twice)


class TestNormalizedShape:
    @given(doc=math_documents())
    def test_output_upholds_backend_assumptions(self, doc: str) -> None:
        root = normalize(doc)
        for el in root.iter():
            # Namespace stripped — backend dispatch matches bare local names.
            assert not el.tag.startswith("{")
            # Presentational wrappers neutralised or removed.
            assert el.tag not in ("mstyle", "mpadded", "mphantom")
            if el.tag == "mspace":
                assert el.get("linebreak") == "newline"
            # Invisible operators dropped.
            if el.tag == "mo" and len(el) == 0:
                assert (el.text or "").strip() not in _INVISIBLE
            # Redundant singleton wrappers collapsed.
            if el.tag == "mrow":
                assert not (len(el) == 1 and not (el.text and el.text.strip()))
            # The latex2mathml degree idiom is rewritten to a baseline °.
            if el.tag == "msup" and len(el) == 2:
                assert not (
                    el[0].tag == "mn"
                    and el[1].tag == "mo"
                    and (el[1].text or "").strip() == "∘"
                )

    def test_thousands_comma_requires_digit_runs_on_both_sides(self) -> None:
        # The data-bk-tight tagging keys on _is_digit_run_mn for BOTH
        # neighbours: an identifier on the left must NOT make ``x,000``
        # read as one tight quantity. (Mutation testing: an and→or
        # precedence flip in the digit-run predicate survived the shape
        # properties, which don't check tagging semantics.)
        tagged = normalize("<math><mn>1</mn><mo>,</mo><mn>000</mn></math>")
        assert tagged[1].get("data-bk-tight") == "1"
        untagged = normalize("<math><mi>x</mi><mo>,</mo><mn>000</mn></math>")
        assert untagged[1].get("data-bk-tight") is None

    @given(kids=st.lists(_subtrees, max_size=3))
    def test_provenance_attributes_survive(self, kids: list[ET.Element]) -> None:
        # data-bk-* spans on content leaves are the sub-element provenance
        # channel; normalization must carry every one of them through.
        root = _elem("math", None, None, kids)
        expected = sorted(
            (el.tag, el.text or "", el.get(_SPAN_ATTR) or "")
            for el in root.iter()
            if el.tag in ("mi", "mn") and el.get(_SPAN_ATTR)
        )
        normalized = normalize(ET.tostring(root, encoding="unicode"))
        got = sorted(
            (el.tag, el.text or "", el.get(_SPAN_ATTR) or "")
            for el in normalized.iter()
            if el.tag in ("mi", "mn") and el.get(_SPAN_ATTR)
        )
        assert got == expected
