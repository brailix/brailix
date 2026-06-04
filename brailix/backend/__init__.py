"""Backend layer: IR → BrailleIR.

Each translator (zh, number, punct, math, ...) takes one or more
:class:`InlineNode` instances and emits a list of
:class:`BrailleCell`. The :mod:`dispatcher` ties them together by
node type, and :class:`BackendContext` carries the per-run state
(profile, number-sign latch, math depth, ...).
"""
