"""Output parsers for external tools run by framework phases."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path


class ToolParserError(ValueError):
    """Raised when a parser cannot decode tool output for its declared format."""


ToolParseResult = list[dict] | tuple[list[dict], dict]
ToolParser = Callable[[str, Path], ToolParseResult]


# A framework's ToolIntegration names its output format here. Next.js's
# `next lint` was the only one; the lint detector runs the project's linter now.
PARSERS: dict[str, ToolParser] = {}


__all__ = [
    "PARSERS",
    "ToolParserError",
    "ToolParseResult",
    "ToolParser",
]
