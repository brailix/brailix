"""Tactile-graphics frontend: any graphic source → normalized SVG.

SVG is the graphics vertical's normalized intermediate format — the
direct analogue of MathML for math and MusicXML for music. A source
adapter turns one source format (raw SVG, geometry primitives, a raster
image, a chart spec, ...) into an SVG string; the :mod:`.normalizer`
parses and tidies it into an :class:`xml.etree.ElementTree.Element` tree,
and **that tree is the IR** — there is no separate vector model. The
tactile backend (:mod:`brailix.backend.tactile`) then walks the tree by
element tag, exactly as the math / music backends walk MathML / MusicXML.

Adapters self-register in the sibling :mod:`.registry` module so the
registry stays populated on a bare install. See
``ARCHITECTURE.md`` for the full data flow.
"""
