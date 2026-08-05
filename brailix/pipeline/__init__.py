"""End-to-end brailix pipeline.

Wires together segmentation, normalization, language-specific
processing (Chinese tokenize + pinyin), math parsing, and the Backend
dispatcher into one :meth:`Pipeline.translate_text` call. Each
frontend subsystem has its own single-callable subsystem entry point
(see :mod:`brailix.frontend`, which is also where the line between
those entry points and the published surface is drawn); this package
is just orchestration plus the optional name-override knobs.

Rendering is **deferred**: :meth:`translate_text` returns a
:class:`TranslationResult` carrying the parsed IR and the braille IR,
but does not run a renderer. Ask for a concrete output by calling
:meth:`TranslationResult.render`.

Typical usage::

    from brailix import Pipeline

    pipe = Pipeline(profile="cn_current")
    result = pipe.translate_text("我在重庆。")
    print(result.render())          # default Unicode braille string
    print(result.render("unicode"))

Package layout
--------------

**This file is a facade and holds nothing else.** What resolves at
``brailix.pipeline`` is exactly :data:`__all__` — the orchestrator, the
module-level graphics entry, the result / value types and the cache-key
digest — because an import path is an address a third party can reach, and
the top-level package takes its own re-exports from here, so this is the one
internal namespace that reads like a published one. It used to carry the
orchestrator's implementation as well, and with it ``Paragraph``, ``Span``,
``DocumentIR``, ``BackendContext`` and two dozen more names it merely *used*:
each of those is supported at ``brailix.ir`` / ``brailix.core``, so reaching
one through here handed a caller the right object from the wrong address —
and made ``brailix.pipeline.Paragraph`` a path somebody could come to depend
on. Splitting the implementation out closes that without dressing the
implementation in underscores.

The pieces live in sibling modules:

* :mod:`brailix.pipeline._pipeline` — the :class:`Pipeline` orchestrator and
  the module-level :func:`translate_graphic`, re-exported here.
* :mod:`brailix.pipeline._results` — the result / value types
  :class:`TranslationResult`, :class:`GraphicResult`,
  :class:`TactilePageResult`, :class:`CompiledBlock`, :data:`TreeSubcache`.
  Re-exported here; these are the API.
* :mod:`brailix.pipeline._helpers` — the module-level standalone helpers
  (:func:`_all_prose_types`, :func:`_ensure_block_span`,
  :func:`_block_surface`, :func:`block_hash`, :func:`cache_lookup`,
  :func:`cache_record`). Only :func:`block_hash`, a documented public digest,
  is pulled up here; the rest are imported from ``_helpers`` directly by the
  code that needs them.
* :mod:`brailix.pipeline._fingerprint` — the compilation-configuration digest
  (:func:`~brailix.pipeline._fingerprint.compilation_fingerprint`) plus the
  runtime-identity folds :attr:`Pipeline.fingerprint` layers on top.
* :mod:`brailix.pipeline._session` — the run-scoped state:
  :class:`~brailix.pipeline._session.CompilationSession` (one translate call's
  collector + contexts + parsed-tree pool) and the
  :class:`~brailix.pipeline._session._InlineTextTranslator` binding.
* :mod:`brailix.pipeline._incremental` — the block-level incremental
  compile primitive, the body behind :meth:`Pipeline.translate_block`
  (reuse-pool threading, cache-key salting, inline-figure rasterisation).
* :mod:`brailix.pipeline._pages` — mixed braille + tactile page
  composition, the body behind :meth:`Pipeline.translate_document_to_pages`.
* :mod:`brailix.pipeline.frontend_driver` — the
  :class:`~brailix.pipeline.frontend_driver.FrontendDriver` collaborator
  (segment → normalize → per-segment routing → inline-math attach → block
  populate). Its math / music / graphic tree parsers are injected there, so a
  test simulates an adapter failure by replacing
  ``pipeline._frontend._parse_math_tree`` (etc.) on the instance rather
  than monkeypatching a ``brailix.pipeline.*`` name.

Note: brailix is the pure compiler — it knows nothing about front-end
concepts like Override / WarningCase / Identity. Callers that want to mutate
the IR between frontend and backend (a proofreading front-end adjusting
pinyin / splitting / merging tokens) pass an ``ir_transformer`` callable to
:meth:`Pipeline.translate_block`; the compiler runs it blindly without caring
what semantics the caller attaches to it.
"""

from __future__ import annotations

from brailix.pipeline._helpers import block_hash
from brailix.pipeline._pipeline import Pipeline, translate_graphic
from brailix.pipeline._results import (
    CompiledBlock,
    GraphicResult,
    TactilePageResult,
    TranslationResult,
    TreeSubcache,
)

# The stable surface of this package — and, since nothing else is bound here,
# the whole of what ``brailix.pipeline`` resolves. Nothing underscore-prefixed
# belongs in it: a name that says "private" while sitting in a list that says
# "public" is a contradiction a third party would resolve in the wrong
# direction. ``tests/test_public_api.py`` pins both halves — this list, and
# that the namespace holds nothing beyond it.
__all__ = [
    "Pipeline",
    "translate_graphic",
    "TranslationResult",
    "GraphicResult",
    "TactilePageResult",
    "CompiledBlock",
    "TreeSubcache",
    "block_hash",
]
