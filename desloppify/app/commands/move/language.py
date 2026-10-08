"""Move-module loading for the move command."""

from __future__ import annotations

from types import ModuleType

from desloppify.app.commands.helpers.dynamic_loaders import (
    load_language_move_module as load_dynamic_language_move_module,
)


def load_lang_move_module(lang_name: str) -> ModuleType:
    """Load language-specific move helpers from ``lang/<name>/move.py``.

    Falls back to the shared scaffold move module when a language does not
    provide its own ``move.py``.
    """
    return load_dynamic_language_move_module(lang_name)


def resolve_move_verify_hint(move_mod: ModuleType) -> str:
    """Return a move-module verification hint."""
    get_verify_hint = getattr(move_mod, "get_verify_hint", None)
    if callable(get_verify_hint):
        hint = get_verify_hint()
        if isinstance(hint, str):
            return hint.strip()
    return ""


__all__ = [
    "load_lang_move_module",
    "resolve_move_verify_hint",
]
