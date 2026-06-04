"""Chinese analyzer adapters. Each module exposes a ``_load`` factory
that returns a :class:`~brailix.core.protocols.ChineseAnalyzer`
instance. Modules import heavy dependencies *inside* ``_load`` so the
registry can lazy-load and surface :class:`MissingExtraError` cleanly.
"""
