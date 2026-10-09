"""Function expressions in JS/TS source, read off the literal-blanked text.

Server-framework scanners look at route handlers and middleware: a function
literal passed to ``app.get(...)``/``app.use(...)``, its parameter names and
its body. Offsets index the text given (see ``js_text.code_text``).
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import dataclass

from desloppify.languages._framework.node.js_classes import matching, split_top_level

_SPACE_RE = re.compile(r"\s*")
_FUNCTION_RE = re.compile(
    r"""\s*(?P<async>\basync\s+)?(?:
        \bfunction\b\s*\*?\s*[\w$]*\s*(?P<fparams>\()
      | (?P<aparams>\()
      | (?P<single>[A-Za-z_$][\w$]*)\s*=>
    )""",
    re.VERBOSE,
)
_PARAM_NAME_RE = re.compile(r"\s*(?:\.\.\.)?\s*([A-Za-z_$][\w$]*)")
_RETURN_TYPE_RE = re.compile(r"\s*:\s*[^={;]+?(?==>|\{)")
_STATEMENT_BEFORE = frozenset(";{})")


@dataclass(frozen=True)
class FunctionLiteral:
    start: int
    is_async: bool
    params: tuple[str, ...]  # names; "" for a destructured parameter
    body: tuple[int, int] | None  # inside the braces; None for an expression body


def _param_names(code: str, start: int, end: int) -> tuple[str, ...]:
    names = []
    for a, b in split_top_level(code, start, end):
        match = _PARAM_NAME_RE.match(code, a, b)
        names.append(match.group(1) if match else "")
    return tuple(names)


def function_at(code: str, pos: int) -> FunctionLiteral | None:
    """The function literal starting at *pos* (after whitespace), if one does."""
    match = _FUNCTION_RE.match(code, pos)
    if match is None:
        return None
    is_async = match.group("async") is not None
    if match.group("single") is not None:
        params: tuple[str, ...] = (match.group("single"),)
        after = match.end()
        arrow = True
    else:
        open_at = (
            match.start("fparams") if match.group("fparams") else match.start("aparams")
        )
        close_at = matching(code, open_at)
        if close_at >= len(code):
            return None
        params = _param_names(code, open_at + 1, close_at)
        after = close_at + 1
        returns = _RETURN_TYPE_RE.match(code, after)
        if returns:
            after = returns.end()
        arrow = match.group("fparams") is None
        after = _SPACE_RE.match(code, after).end()
        if arrow:
            if not code.startswith("=>", after):
                return None
            after += 2
    after = _SPACE_RE.match(code, after).end()
    if code.startswith("{", after):
        return FunctionLiteral(
            match.start(), is_async, params, (after + 1, matching(code, after))
        )
    if not arrow:
        return None
    return FunctionLiteral(match.start(), is_async, params, None)


def _previous_char(code: str, pos: int, floor: int) -> str:
    while pos > floor and code[pos - 1].isspace():
        pos -= 1
    return code[pos - 1] if pos > floor else ""


def call_arguments(
    code: str, callee: re.Pattern[str]
) -> Iterator[tuple[re.Match[str], list[tuple[int, int]]]]:
    """Each call whose callee *callee* matches (ending at its ``(``), with argument spans."""
    for match in callee.finditer(code):
        open_at = match.end() - 1
        if code[open_at] != "(":
            continue
        yield match, split_top_level(code, open_at + 1, matching(code, open_at))


def statement_calls(
    code: str, body: tuple[int, int], pattern: re.Pattern[str]
) -> Iterator[re.Match[str]]:
    """Matches of *pattern* in *body* that start a statement (not returned, awaited or assigned)."""
    start, end = body
    for match in pattern.finditer(code, start, end):
        before = _previous_char(code, match.start(), start)
        if not before or before in _STATEMENT_BEFORE:
            yield match


__all__ = ["FunctionLiteral", "call_arguments", "function_at", "statement_calls"]
