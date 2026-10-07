"""Tree-sitter support for the TypeScript plugin.

Install with: ``pip install tree-sitter-language-pack``

- ``parsing``: parser loading, query helpers, grammar load failures
- ``cache``: scan-scoped parse tree cache
- ``spec``: the tsx grammar and function query
- ``cohesion``: responsibility cohesion detector and phase
"""

from __future__ import annotations

import logging

from desloppify.base.output.fallbacks import log_best_effort_failure

from .cache import disable_parse_cache, enable_parse_cache, is_parse_cache_enabled
from .parsing import PARSE_INIT_ERRORS, grammar_load_failures, reset_grammar_load_failures
from .spec import TYPESCRIPT_SPEC, TreeSitterLangSpec

logger = logging.getLogger(__name__)

_AVAILABLE = False
try:
    import tree_sitter_language_pack  # noqa: F401

    _AVAILABLE = True
except ImportError as exc:
    log_best_effort_failure(logger, "import tree_sitter_language_pack", exc)


def is_available() -> bool:
    """Return True if tree-sitter-language-pack is installed."""
    return _AVAILABLE


__all__ = [
    "PARSE_INIT_ERRORS",
    "TYPESCRIPT_SPEC",
    "TreeSitterLangSpec",
    "disable_parse_cache",
    "enable_parse_cache",
    "grammar_load_failures",
    "is_available",
    "is_parse_cache_enabled",
    "reset_grammar_load_failures",
]
