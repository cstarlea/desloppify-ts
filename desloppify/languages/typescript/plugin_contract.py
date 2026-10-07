"""Shared TypeScript plugin contract values.

This module is the single source of truth for configuration values used by
both the language config surface and command wiring.
"""

from __future__ import annotations

# JavaScript files are scanned alongside TypeScript (``allowJs`` projects and
# plain JavaScript projects alike).
TS_EXTENSIONS = [".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"]
TS_EXCLUSIONS = [
    "node_modules",
    ".d.ts",
    ".d.mts",
    ".d.cts",
    ".min.js",
    ".min.mjs",
    ".min.cjs",
]
TS_DEFAULT_SRC = "src"
TS_ENTRY_PATTERNS = [
    "/pages/",
    "/main.tsx",
    "/main.ts",
    "/main.jsx",
    "/main.js",
    "/App.tsx",
    "/App.jsx",
    "vite.config",
    "tailwind.config",
    "postcss.config",
    ".d.ts",
    "/settings.ts",
    "/__tests__/",
    ".test.",
    ".spec.",
    ".stories.",
]
TS_BARREL_NAMES = {"index.ts", "index.tsx", "index.js", "index.jsx"}
TS_LARGE_THRESHOLD = 500
TS_COMPLEXITY_THRESHOLD = 15

__all__ = [
    "TS_BARREL_NAMES",
    "TS_COMPLEXITY_THRESHOLD",
    "TS_DEFAULT_SRC",
    "TS_ENTRY_PATTERNS",
    "TS_EXCLUSIONS",
    "TS_EXTENSIONS",
    "TS_LARGE_THRESHOLD",
]
