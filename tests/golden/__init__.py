"""Golden test suite for the brailix Pipeline.

These tests lock the current end-to-end Unicode-braille output of
``Pipeline().translate_text(...).render()`` cell-by-cell. Any future
change that alters the rendered string will fail one of these tests
on purpose — that is the entire point of a golden suite.

To regenerate the expected braille value for a single input::

    python -c "from brailix import Pipeline; \
                p = Pipeline(); \
                print(repr(p.translate_text(SRC).render()))"
"""
