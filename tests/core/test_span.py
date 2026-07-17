"""Example tests for :class:`brailix.core.span.Span`.

The algebraic contract — construction validity, ``merge`` / ``contains`` /
``overlaps`` relations, shifting, ``merge_spans`` bounding, serialization
round-trips and malformed-payload rejection — is property-tested over
generated inputs in ``test_span_properties.py``. This module keeps only
what the property suite doesn't express.
"""

import pytest

from brailix.core.span import Span


class TestSpanImmutability:
    def test_frozen(self):
        from dataclasses import FrozenInstanceError

        s = Span(0, 5)
        with pytest.raises(FrozenInstanceError):
            s.start = 1  # type: ignore[misc]
