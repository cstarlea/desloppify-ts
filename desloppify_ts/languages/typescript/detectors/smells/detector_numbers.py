"""The magic-number smell: a 4+ digit literal used in logic."""

from __future__ import annotations

import re

from desloppify_ts.languages.typescript.syntax.nodes import FUNCTIONS
from desloppify_ts.languages.typescript.syntax.queries import descendants
from desloppify_ts.languages.typescript.syntax.tree import ParsedSource

from .detector_core import _emit, _parsed

_MAGIC_RE = re.compile(r"(===?|!==?|>=?|<=?|[+\-*/])\s*(\d{4,})")
_ARITHMETIC = frozenset("+-*/")
_FRACTION_RE = re.compile(r"[.eE]")
_MULTIPLY_NEXT_RE = re.compile(r"\s*[*/]")
_CONSTANT_NAME_RE = re.compile(r"[A-Z][A-Z0-9_]*")
_CONSTANT_LINE_RE = re.compile(
    r"^\s*(?:export\s+)?(?:declare\s+)?const\s+[A-Z][A-Z0-9_]*\s*(?::[^=]*)?="
)
# Milliseconds, seconds and bytes per unit, and the usual products of them
# (``60 * 60 * 1000``, ``1024 * 1024``) written out.
_UNIT_FACTORS = frozenset(
    {
        1000,
        1024,
        3600,
        60_000,
        86_400,
        604_800,
        3_600_000,
        86_400_000,
        604_800_000,
        1_048_576,
        1_073_741_824,
    }
)


def _detect_magic_numbers(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find 4+ digit literals compared against or used in arithmetic, once per line.

    Skips the initialiser of a ``const UPPER_SNAKE`` (the number already has a
    name) and unit factors in arithmetic (``x * 1000``, ``64 * 1024``,
    ``Date.now() - 1000``); a unit factor in a comparison still counts.
    """
    constant_lines = _constant_lines(ctx)
    for index, code in enumerate(ctx.source.code_lines):
        if index + 1 in constant_lines or not any(
            not _is_unit_arithmetic(code, match) for match in _MAGIC_RE.finditer(code)
        ):
            continue
        _emit(
            smell_counts, "magic_number", ctx, index + 1, ctx.lines[index].strip()[:100]
        )


def _is_unit_arithmetic(code: str, match: re.Match[str]) -> bool:
    if int(match.group(2)) not in _UNIT_FACTORS or _FRACTION_RE.match(
        code, match.end()
    ):
        return False
    return match.group(1) in _ARITHMETIC or bool(
        _MULTIPLY_NEXT_RE.match(code, match.end())
    )


def _constant_lines(ctx) -> set[int]:
    """1-based lines inside the initialiser of a ``const UPPER_SNAKE``, function bodies left out.

    Without tree-sitter: the lines that open such a declaration.
    """
    parsed = _parsed(ctx)
    if parsed is None:
        return {
            index + 1
            for index, code in enumerate(ctx.source.code_lines)
            if _CONSTANT_LINE_RE.match(code)
        }
    lines: set[int] = set()
    for value in _constant_values(parsed):
        lines.update(range(parsed.line(value), parsed.end_line(value) + 1))
        for function in descendants(value, FUNCTIONS):
            lines.difference_update(
                range(parsed.line(function), parsed.end_line(function) + 1)
            )
    return lines


def _constant_values(parsed: ParsedSource):
    for declaration in descendants(parsed.root, ("lexical_declaration",)):
        if not declaration.children or declaration.children[0].type != "const":
            continue
        for declarator in declaration.named_children:
            if declarator.type != "variable_declarator":
                continue
            name = declarator.child_by_field_name("name")
            value = declarator.child_by_field_name("value")
            if (
                name is not None
                and value is not None
                and name.type == "identifier"
                and _CONSTANT_NAME_RE.fullmatch(parsed.text(name))
            ):
                yield value


__all__ = ["_detect_magic_numbers"]
