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
from .parsing import (
    PARSE_INIT_ERRORS,
    REQUIRED_GRAMMARS,
    _get_parser,
    grammar_load_failures,
    note_grammar_failure,
    prepare_grammars,
    reset_grammar_load_failures,
)
from .spec import TYPESCRIPT_SPEC, TreeSitterLangSpec

logger = logging.getLogger(__name__)

_AVAILABLE = False
try:
    import tree_sitter_language_pack  # noqa: F401

    _AVAILABLE = True
except ImportError as exc:
    log_best_effort_failure(logger, "import tree_sitter_language_pack", exc)


def is_installed() -> bool:
    """Return True if tree-sitter-language-pack imports; loads no grammar."""
    return _AVAILABLE


# The tsx grammar's load error, or "" once it has loaded; None until tried.
_TSX_ERROR: str | None = None


def is_available() -> bool:
    """Return True if tree-sitter-language-pack is installed and the tsx grammar loads.

    The language pack fetches grammars at runtime, so an installed pack can
    still have no grammar (offline). A failure is re-recorded on every call so
    the scan that asks reports it as reduced coverage.
    """
    global _TSX_ERROR
    if not _AVAILABLE:
        return False
    if _TSX_ERROR is None:
        try:
            _get_parser("tsx")
        except Exception as exc:
            _TSX_ERROR = f"{type(exc).__name__}: {exc}"
            log_best_effort_failure(logger, "load the tree-sitter tsx grammar", exc)
        else:
            _TSX_ERROR = ""
    if _TSX_ERROR:
        note_grammar_failure("tsx", _TSX_ERROR)
        return False
    return True


__all__ = [
    "PARSE_INIT_ERRORS",
    "REQUIRED_GRAMMARS",
    "TYPESCRIPT_SPEC",
    "TreeSitterLangSpec",
    "disable_parse_cache",
    "enable_parse_cache",
    "grammar_load_failures",
    "is_available",
    "is_installed",
    "is_parse_cache_enabled",
    "prepare_grammars",
    "reset_grammar_load_failures",
]
