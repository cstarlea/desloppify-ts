"""Language-resolution helpers for command modules."""

from __future__ import annotations

from typing import TYPE_CHECKING

from desloppify.base.exception_sets import CommandError
from desloppify.languages import framework as lang_api

if TYPE_CHECKING:
    from desloppify.languages.framework import LangConfig


class LangResolutionError(CommandError):
    """Raised when language resolution fails with a user-facing message.

    Inherits from ``CommandError`` so the CLI top-level uses the standard
    command error path (formatted message + non-zero exit code) instead of
    bypassing the command error hierarchy.
    """

    def __init__(self, message: str) -> None:
        self.message = message
        super().__init__(message, exit_code=1)


def resolve_lang(args: object) -> LangConfig:
    """The TypeScript language config (the only language plugin)."""
    del args
    try:
        return lang_api.default_lang()
    except (ImportError, TypeError, ValueError, AttributeError) as exc:
        raise LangResolutionError(
            f"The TypeScript plugin failed to load: {exc}"
        ) from exc


def resolve_lang_settings(config: dict, lang: LangConfig) -> dict[str, object]:
    """Resolve persisted per-language settings from config.languages.<lang>."""
    if not isinstance(config, dict):
        return lang.normalize_settings({})
    languages = config.get("languages", {})
    if not isinstance(languages, dict):
        return lang.normalize_settings({})
    raw = languages.get(lang.name, {})
    return lang.normalize_settings(raw if isinstance(raw, dict) else {})
