"""Decorated class declarations in JS/TS source, read off the text.

Framework scanners (NestJS, Angular) need a class's decorators, its body and
its constructor parameters. This finds them on the text with comments and
literals blanked, so it needs no parser; offsets index the original text.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from desloppify.languages._framework.node.js_text import code_text

_CLASS_RE = re.compile(r"(?<![\w$.])class\s+([A-Za-z_$][\w$]*)")
_CONSTRUCTOR_RE = re.compile(r"\bconstructor\s*\(")
_MODIFIERS = ("abstract", "default", "export", "declare")
_OPEN = {"(": ")", "[": "]", "{": "}"}
_CLOSE = {v: k for k, v in _OPEN.items()}


@dataclass(frozen=True)
class Decorator:
    name: str  # as written, e.g. "Controller" or "Nest.Module"
    args: tuple[int, int] | None  # offsets of the text inside the parentheses


@dataclass(frozen=True)
class ClassDecl:
    name: str
    line: int
    decorators: tuple[Decorator, ...]
    body: tuple[int, int]  # offsets of the text inside the braces

    def has_decorator(self, names: frozenset[str] | set[str]) -> bool:
        return any(d.name.rsplit(".", 1)[-1] in names for d in self.decorators)


def matching(code: str, start: int) -> int:
    """Offset of the bracket closing the one at *start* (len(code) if unclosed)."""
    stack = [_OPEN[code[start]]]
    for i in range(start + 1, len(code)):
        ch = code[i]
        if ch in _OPEN:
            stack.append(_OPEN[ch])
        elif ch in _CLOSE:
            if not stack or ch != stack[-1]:
                return i
            stack.pop()
            if not stack:
                return i
    return len(code)


def _matching_back(code: str, end: int) -> int:
    """Offset of the bracket opening the one at *end* (-1 if unopened)."""
    stack = [_CLOSE[code[end]]]
    for i in range(end - 1, -1, -1):
        ch = code[i]
        if ch in _CLOSE:
            stack.append(_CLOSE[ch])
        elif ch in _OPEN:
            if not stack or ch != stack[-1]:
                return -1
            stack.pop()
            if not stack:
                return i
    return -1


def _skip_space_back(code: str, pos: int) -> int:
    while pos > 0 and code[pos - 1].isspace():
        pos -= 1
    return pos


def _word_before(code: str, pos: int) -> int:
    """Start of the identifier (dots allowed) ending at *pos*."""
    start = pos
    while start > 0 and (code[start - 1].isalnum() or code[start - 1] in "_$."):
        start -= 1
    return start


def _decorators_before(code: str, pos: int) -> tuple[Decorator, ...]:
    pos = _skip_space_back(code, pos)
    while True:
        start = _word_before(code, pos)
        if start == pos or code[start:pos] not in _MODIFIERS:
            break
        pos = _skip_space_back(code, start)
    found: list[Decorator] = []
    while pos > 0:
        args = None
        end = pos
        if code[end - 1] == ")":
            open_at = _matching_back(code, end - 1)
            if open_at < 0:
                break
            args = (open_at + 1, end - 1)
            end = _skip_space_back(code, open_at)
        start = _word_before(code, end)
        if start == end or start == 0 or code[start - 1] != "@":
            break
        found.append(Decorator(code[start:end], args))
        pos = _skip_space_back(code, start - 1)
    return tuple(reversed(found))


def _body_open(code: str, pos: int) -> int:
    """The ``{`` opening a class body, past heritage clauses and generics."""
    angle = 0
    i = pos
    while i < len(code):
        ch = code[i]
        if ch == "<":
            angle += 1
        elif ch == ">" and angle and code[i - 1] != "=":
            angle -= 1
        elif ch in "([":
            i = matching(code, i)
        elif ch == "{":
            if angle == 0:
                return i
            i = matching(code, i)
        elif ch == ";":
            return -1
        i += 1
    return -1


def iter_classes(text: str, code: str | None = None) -> list[ClassDecl]:
    """Every class declaration in *text*, with its decorators."""
    code = code_text(text) if code is None else code
    classes: list[ClassDecl] = []
    for match in _CLASS_RE.finditer(code):
        open_at = _body_open(code, match.end())
        if open_at < 0:
            continue
        classes.append(
            ClassDecl(
                name=match.group(1),
                line=code.count("\n", 0, match.start()) + 1,
                decorators=_decorators_before(code, match.start()),
                body=(open_at + 1, matching(code, open_at)),
            )
        )
    return classes


def split_top_level(code: str, start: int, end: int) -> list[tuple[int, int]]:
    """Spans of the comma-separated items in ``code[start:end]``, brackets respected."""
    items: list[tuple[int, int]] = []
    i = item_start = start
    while i < end:
        ch = code[i]
        if ch in _OPEN:
            i = matching(code, i)
        elif ch == ",":
            items.append((item_start, i))
            item_start = i + 1
        i += 1
    items.append((item_start, end))
    return [(a, b) for a, b in items if code[a:b].strip()]


def constructor_params(code: str, cls: ClassDecl) -> list[tuple[int, int]] | None:
    """Spans of the class's own constructor parameters, or None without one."""
    start, end = cls.body
    for match in _CONSTRUCTOR_RE.finditer(code, start, end):
        depth = 0
        for ch in code[start : match.start()]:
            if ch in "{([":
                depth += 1
            elif ch in "})]":
                depth -= 1
        if depth != 0:
            continue
        open_at = match.end() - 1
        return split_top_level(code, open_at + 1, matching(code, open_at))
    return None


__all__ = [
    "ClassDecl",
    "Decorator",
    "constructor_params",
    "iter_classes",
    "matching",
    "split_top_level",
]
