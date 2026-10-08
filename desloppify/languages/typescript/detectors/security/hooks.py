"""TypeScript hooks for the cross-language security scan (``engine/detectors/security``)."""

from __future__ import annotations

from pathlib import Path

from desloppify.languages.typescript.syntax.scanner import SourceText


def line_views(content: str, path: Path) -> tuple[list[str], list[str], list[str]]:
    """A file's lines, with comments and literals (JSX text too) blanked, and with
    only comments blanked; each list lines up with the first."""
    source = SourceText(content, path)
    return source.lines, source.code_lines, source.uncommented_lines


__all__ = ["line_views"]
