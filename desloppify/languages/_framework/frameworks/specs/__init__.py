"""Built-in framework specs."""

from __future__ import annotations

# TypeScript and JavaScript module extensions, for entry conventions.
SCRIPT_EXTENSIONS = frozenset({".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"})


def config_names(stem: str) -> tuple[str, ...]:
    """``<stem>.ts``, ``<stem>.js`` and the module-kind variants."""
    return tuple(f"{stem}.{ext}" for ext in ("ts", "mts", "cts", "js", "mjs", "cjs"))


__all__ = ["SCRIPT_EXTENSIONS", "config_names"]
