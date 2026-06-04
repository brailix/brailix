"""Shared ElementTree helpers for the frontend normalizers.

The MathML and MusicXML normalizers both parse a vendor string into an
:class:`~xml.etree.ElementTree.Element` tree and then (a) drop XML
namespaces so the backend can match bare local tags and (b) null out
pure-whitespace ``text`` / ``tail`` nodes that confuse element
iteration. Both steps are format-independent, so they live here once
rather than in two near-identical copies.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET


def strip_namespace(elem: ET.Element) -> None:
    """Recursively drop any ``{namespace}local`` Clark-notation prefix
    from every element tag, leaving the bare local name.

    A normalized MathML / MusicXML tree only ever carries its own
    namespace, so the generic strip is equivalent to a prefix-specific
    one for valid input while also tidying any stray foreign-namespaced
    tag a vendor might have left behind.
    """
    if elem.tag.startswith("{"):
        close = elem.tag.find("}")
        if close != -1:
            elem.tag = elem.tag[close + 1:]
    for child in list(elem):
        strip_namespace(child)


def strip_whitespace_text(elem: ET.Element) -> None:
    """Recursively null out pure-whitespace ``text`` / ``tail`` strings,
    which otherwise confuse children iteration in the IR builders."""
    if elem.text is not None and not elem.text.strip():
        elem.text = None
    for child in list(elem):
        if child.tail is not None and not child.tail.strip():
            child.tail = None
        strip_whitespace_text(child)


def local_name(tag: str) -> str:
    """Bare local name of an ElementTree tag, dropping any
    ``{namespace}`` Clark-notation prefix. The single-tag counterpart to
    :func:`strip_namespace` — used where a caller looks up one tag's name
    without rewriting the whole tree (the OMML / docx converters)."""
    if tag.startswith("{"):
        return tag.partition("}")[2]
    return tag
