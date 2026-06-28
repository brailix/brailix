"""Backend layer: IR → BrailleIR.

Each translator (zh, number, punct, math, ...) takes one or more
:class:`InlineNode` instances and emits a list of
:class:`BrailleCell`. The dispatcher (:mod:`brailix.backend.dispatch`)
ties them together by node type. Context-sensitive braille state (the
number-sign latch, math nesting depth, ...) lives on the per-subsystem
state machines that own it, not on :class:`BackendContext`, which
carries only the profile, run mode, block type, and shared warnings.
"""
