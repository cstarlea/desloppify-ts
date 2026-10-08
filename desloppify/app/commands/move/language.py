"""Move-module loading for the move command."""

from __future__ import annotations

from types import ModuleType


def load_move_module() -> ModuleType:
    """Load the TypeScript move helpers (``languages/typescript/move.py``)."""
    from desloppify.languages.typescript import move

    return move


def resolve_move_verify_hint(move_mod: ModuleType) -> str:
    """Return a move-module verification hint."""
    get_verify_hint = getattr(move_mod, "get_verify_hint", None)
    if callable(get_verify_hint):
        hint = get_verify_hint()
        if isinstance(hint, str):
            return hint.strip()
    return ""


__all__ = [
    "load_move_module",
    "resolve_move_verify_hint",
]
