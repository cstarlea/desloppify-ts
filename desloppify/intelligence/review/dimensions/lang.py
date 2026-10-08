"""Language-derived review dimensions and guidance helpers."""

from __future__ import annotations

import logging

from desloppify.languages.framework import DEFAULT_LANG, get_lang

logger = logging.getLogger(__name__)

_LOAD_ERRORS = (ValueError, TypeError, AttributeError, ImportError)


def _collect_holistic_dims_by_lang() -> dict[str, list[str]]:
    """Holistic dimension defaults from the TypeScript plugin, keyed by language."""
    try:
        dims = list(get_lang(DEFAULT_LANG).holistic_review_dimensions or [])
    except _LOAD_ERRORS as exc:
        logger.debug("Skipping holistic dimensions: %s", exc)
        dims = []
    return {DEFAULT_LANG: dims} if dims else {}


HOLISTIC_DIMENSIONS_BY_LANG: dict[str, list[str]] = _collect_holistic_dims_by_lang()


def _collect_lang_guidance() -> dict[str, dict[str, object]]:
    """Review guidance from the TypeScript plugin, keyed by language."""
    try:
        guide = get_lang(DEFAULT_LANG).review_guidance or {}
    except _LOAD_ERRORS as exc:
        logger.debug("Skipping review guidance: %s", exc)
        guide = {}
    return {DEFAULT_LANG: guide} if guide else {}


LANG_GUIDANCE: dict[str, dict[str, object]] = _collect_lang_guidance()


def get_lang_guidance(lang_name: str) -> dict[str, object]:
    """Return language-specific review guidance from plugin configuration."""
    return LANG_GUIDANCE.get(lang_name, {})
