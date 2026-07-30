"""Build the API reference from the docstrings, with pdoc.

There is no hand-written API page. The names, the signatures and the prose
all come from the source, because a second copy is a copy that goes stale:
the three drifts that prompted this — ABC documented as converting at read
time, a three-part cache key that had grown a fourth part, and a
``BrailleCell.unicode`` field that never existed — were each a case where
the docstring was right and the hand-written page was wrong.

Two things this script has to do that plain ``pdoc`` does not.

**Sphinx roles.** brailix's docstrings are written in reStructuredText's
cross-reference style (``:class:`~brailix.ir.document.DocumentIR```) —
around 1500 of them. pdoc renders docstrings as Markdown and leaves those
untouched, even under ``--docformat restructuredtext``, so the reference
would be littered with raw markup. Read aloud by a screen reader that is
"colon class colon backtick DocumentIR backtick" in the middle of nearly
every sentence. :func:`sphinx_roles_to_markdown` rewrites them into
backticked identifiers, which pdoc then turns into real links wherever it
can resolve them. The docstrings themselves stay reST — the roles carry
information (which *kind* of thing is being referenced) and are worth
keeping in the source.

**Which modules.** The reference covers exactly the documented facade, the
same list ``tests/test_public_api.py`` pins ``__all__`` against. pdoc
honours ``__all__``, so each page shows the supported surface and nothing
else — the internal modules stay reachable in the source and absent from
the reference, which is what "internal" is supposed to mean.

Usage::

    python scripts/build_docs.py --output-dir site
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

# The documented public surface — keep in step with the manifest in
# ``tests/test_public_api.py``, which is what pins the promise; a page
# generated from a different list would document a surface nobody promised.
FACADE_MODULES = (
    "brailix",
    "brailix.core",
    "brailix.core.models",
    "brailix.frontend",
    "brailix.input",
    "brailix.ir",
    "brailix.renderer",
)

# Every cross-reference role the docstrings use. ``~`` means "show only the
# last component", Sphinx's own shorthand for keeping prose readable; we
# honour it, because a fully-qualified name spelled out mid-sentence is
# exactly what it exists to avoid.
_ROLE = re.compile(
    r":(?:class|meth|func|data|attr|mod|exc|obj|const):`\s*(~?)([^`]+?)\s*`"
)

# A reST literal-block marker: "usage::" introduces an indented block. The
# indented block is already a code block in Markdown, so only the doubled
# colon needs to go.
_LITERAL_BLOCK = re.compile(r"::(\s*\n)")


def sphinx_roles_to_markdown(text: str) -> str:
    """Rewrite reST cross-reference roles into Markdown code spans.

    ``:class:`~a.b.C``` becomes ```C```, ``:meth:`Pipeline.translate_text```
    becomes ```Pipeline.translate_text```. pdoc links a backticked
    identifier when it can resolve it, so the reference keeps its
    navigation; an unresolvable one degrades to plain code, which still
    reads correctly.
    """

    def _sub(m: re.Match[str]) -> str:
        short, target = m.group(1), m.group(2)
        if short:
            target = target.rsplit(".", 1)[-1]
        return f"`{target}`"

    text = _ROLE.sub(_sub, text)
    return _LITERAL_BLOCK.sub(r":\1", text)


def install_docstring_filter() -> None:
    """Make pdoc run every docstring through :func:`sphinx_roles_to_markdown`.

    pdoc has no documented hook for preprocessing a docstring, so this wraps
    ``pdoc.docstrings.convert`` — the single function every docstring passes
    through on its way to Markdown. Wrapping rather than replacing keeps
    whatever else that function does.
    """
    import pdoc.docstrings

    original = pdoc.docstrings.convert

    def convert(docstring: str, docformat: str, source_file: object) -> str:
        return original(
            sphinx_roles_to_markdown(docstring), docformat, source_file
        )

    pdoc.docstrings.convert = convert


def facade_specs() -> list[str]:
    """The module specs to hand pdoc: the facade, and nothing under it.

    Naming a package makes pdoc walk into it, so ``brailix.frontend`` drags
    in the submodules its ``__all__`` names (``brailix.frontend.segment``,
    ``...normalize``) and the reference would document their internals —
    exactly the "reachable, not supported" code the facade exists to keep
    out. Rather than hand-maintain an exclusion list that goes stale the
    next time an ``__all__`` changes, expand the specs and exclude whatever
    came along uninvited, then say what was excluded.
    """
    import pdoc.extract

    expanded = list(pdoc.extract.walk_specs(FACADE_MODULES))
    extra = [m for m in expanded if m not in FACADE_MODULES]
    if extra:
        print(f"excluding non-facade modules pdoc pulled in: {', '.join(extra)}")
    return [*FACADE_MODULES, *(f"!{m}" for m in extra)]


def build(output_dir: Path) -> None:
    import pdoc

    install_docstring_filter()
    pdoc.render.configure(
        docformat="restructuredtext",
        favicon=None,
        footer_text="brailix — this reference is generated from the source",
        search=True,
    )
    pdoc.pdoc(*facade_specs(), output_directory=output_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("site"),
        help="where to write the generated HTML (default: ./site)",
    )
    args = parser.parse_args(argv)
    build(args.output_dir)
    print(f"API reference written to {args.output_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
