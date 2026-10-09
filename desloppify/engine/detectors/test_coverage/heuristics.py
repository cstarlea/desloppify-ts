"""Heuristic checks for deciding whether modules need direct tests."""

from __future__ import annotations

import logging

from desloppify.engine.hook_registry import get_lang_hook

from .io import read_coverage_file

logger = logging.getLogger(__name__)


def _load_lang_test_coverage_module(lang_name: str):
    """Load language-specific test coverage helpers from lang hooks."""
    return get_lang_hook(lang_name, "test_coverage") or object()


def _has_testable_logic(filepath: str, lang_name: str) -> bool:
    """Check whether a file contains runtime logic worth testing."""
    read_result = read_coverage_file(filepath, context="testable_logic")
    if not read_result.ok:
        return False
    content = read_result.content

    mod = _load_lang_test_coverage_module(lang_name)
    has_logic = getattr(mod, "has_testable_logic", None)
    if callable(has_logic):
        return bool(has_logic(filepath, content))
    return True


def _has_inline_tests(filepath: str, lang_name: str) -> bool:
    """Check whether a production file embeds inline tests."""
    read_result = read_coverage_file(filepath, context="inline_tests")
    if not read_result.ok:
        return False
    content = read_result.content

    mod = _load_lang_test_coverage_module(lang_name)
    has_inline = getattr(mod, "has_inline_tests", None)
    if callable(has_inline):
        try:
            return bool(has_inline(filepath, content))
        except (TypeError, ValueError):
            logger.debug("inline_tests hook failed for %s", filepath, exc_info=True)
    return False


def _public_entry_files(production_files: set[str], lang_name: str) -> set[str]:
    """Production files that are the package's public API (package.json entries)."""
    mod = _load_lang_test_coverage_module(lang_name)
    hook = getattr(mod, "public_entry_files", None)
    if not callable(hook):
        return set()
    return set(hook(production_files)) & production_files


def _is_runtime_entrypoint(filepath: str, lang_name: str) -> bool:
    """Best-effort runtime entrypoint detection for no-tests classification."""
    read_result = read_coverage_file(filepath, context="runtime_entrypoint")
    if not read_result.ok:
        return False
    content = read_result.content

    mod = _load_lang_test_coverage_module(lang_name)
    hook = getattr(mod, "is_runtime_entrypoint", None)
    if callable(hook):
        try:
            return bool(hook(filepath, content))
        except (TypeError, ValueError):
            logger.debug(
                "runtime_entrypoint hook failed for %s", filepath, exc_info=True
            )
    return False
