"""Backend layer: semantic IR → output-domain IR.

Two output domains live here, each with its own product and its own
renderers (ARCHITECTURE#arch-layers):

* **braille** — each translator (zh, ja, number, punct, math, music, ...)
  takes one or more :class:`InlineNode` instances and emits a list of
  :class:`BrailleCell`; the dispatcher
  (:mod:`brailix.backend.dispatch`) ties them together by node type.
* **tactile_raster** — :mod:`brailix.backend.tactile` dispatches a
  normalized SVG tree by element tag and rasterizes it into a
  :class:`~brailix.ir.tactile.TactileRaster` of raise levels, driven by a
  :class:`~brailix.backend.tactile.profile.TactileProfile` instead of a
  :class:`BrailleProfile`. A graphic never becomes braille cells; only its
  ``<text>`` labels do, through an injected translator.

Both are "apply the rules to the semantic IR and produce the thing a
renderer encodes" — the tactile side is a second product domain, not a
layer exception.

Context-sensitive braille state (the number-sign latch, math nesting
depth, ...) lives on the per-subsystem state machines that own it, not on
:class:`BackendContext`, which carries only the profile, run mode, block
type, and shared warnings.
"""
