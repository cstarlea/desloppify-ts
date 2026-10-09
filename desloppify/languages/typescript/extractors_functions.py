"""TypeScript function extraction and normalization helpers."""

from __future__ import annotations

import hashlib
import logging
import re
from pathlib import Path

from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.sfc import read_code_text
from desloppify.engine.detectors.base import FunctionInfo
from desloppify.languages.typescript.syntax.lines import split_lines
from desloppify.languages.typescript.syntax.nodes import PARAMETERS, binding_names
from desloppify.languages.typescript.syntax.queries import (
    FunctionInfo as SyntaxFunctionInfo,
)
from desloppify.languages.typescript.syntax.queries import definitions
from desloppify.languages.typescript.syntax.tree import ParsedSource, parsed_file

logger = logging.getLogger(__name__)


def _extract_ts_params(sig: str) -> list[str]:
    """Extract parameter names from a TS function signature string."""
    depth = 0
    start = -1
    end = -1
    for idx, ch in enumerate(sig):
        if ch == "(":
            if depth == 0:
                start = idx
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                end = idx
                break

    if start < 0 or end < 0:
        m = re.search(r"=\s*(\w+)\s*=>", sig)
        if m:
            return [m.group(1)]
        return []

    param_str = sig[start + 1 : end]
    if not param_str.strip():
        return []

    inner = param_str.strip()
    if inner.startswith("{"):
        brace_end = inner.find("}")
        if brace_end > 0:
            inner_params = inner[1:brace_end]
            return _parse_param_names(inner_params)

    return _parse_param_names(param_str)


def _parse_param_names(param_str: str) -> list[str]:
    """Parse comma-separated param names stripping types/defaults/rest syntax."""
    params = []
    depth = 0
    current: list[str] = []
    for ch in param_str:
        if ch in ("(", "<", "{", "["):
            depth += 1
            current.append(ch)
        elif ch in (")", ">", "}", "]"):
            depth -= 1
            current.append(ch)
        elif ch == "," and depth == 0:
            params.append("".join(current))
            current = []
        else:
            current.append(ch)
    if current:
        params.append("".join(current))

    names = []
    for token in params:
        token = token.strip()
        if not token:
            continue
        if token.startswith("..."):
            token = token[3:]
        name = re.split(r"[?:=]", token)[0].strip()
        if name and name.isidentifier():
            names.append(name)
    return names


def _find_function_end(lines: list[str], start_line: int) -> tuple[bool, int]:
    """Return (found_open_brace, end_line_index) for a function body."""
    brace_depth = 0
    found_open = False
    line_idx = start_line
    while line_idx < len(lines):
        line = lines[line_idx]
        char_idx = 0
        while char_idx < len(line):
            ch = line[char_idx]
            if ch in ('"', "'", "`"):
                quote = ch
                char_idx += 1
                while char_idx < len(line):
                    if line[char_idx] == "\\":
                        char_idx += 2
                        continue
                    if line[char_idx] == quote:
                        break
                    char_idx += 1
            elif ch == "/" and char_idx + 1 < len(line) and line[char_idx + 1] == "/":
                break
            elif ch == "{":
                brace_depth += 1
                found_open = True
            elif ch == "}":
                brace_depth -= 1
            char_idx += 1
        if found_open and brace_depth <= 0:
            break
        line_idx += 1

    return found_open, line_idx


def _extract_signature(lines: list[str], start_line: int, end_line: int) -> str:
    sig_lines = []
    for line_idx in range(start_line, end_line + 1):
        sig_lines.append(lines[line_idx])
        if "{" in lines[line_idx]:
            break
    return "\n".join(sig_lines)


def extract_ts_functions(filepath: str) -> list[FunctionInfo]:
    """Extract function/component bodies from a TS/TSX file.

    Named definitions from the syntax tree (declarations, variable-bound
    functions, class and object-literal members, anonymous default exports);
    functions nested in one are part of its body, and ``obj.member = function``
    implementations are left out.
    Without tree-sitter a line regex finds top-level-looking declarations.
    """
    parsed = parsed_file(filepath)
    if parsed is None:
        return _extract_ts_functions_regex(filepath)
    functions = []
    covered_until = -1
    for definition in definitions(parsed):
        if definition.start < covered_until:
            continue
        info = definition.function
        if _is_member_assignment(info):
            continue
        covered_until = info.span.end_byte
        source = parsed.source
        start = source.rfind(b"\n", 0, definition.start) + 1
        end = source.find(b"\n", covered_until)
        body = source[start : len(source) if end == -1 else end].decode(
            "utf-8", "replace"
        )
        normalized = normalize_ts_body(body)
        if len(normalized.splitlines()) < 3:
            continue
        end_line = info.span.end_line
        functions.append(
            FunctionInfo(
                name=definition.name,
                file=filepath,
                line=definition.line,
                end_line=end_line,
                loc=end_line - definition.line + 1,
                body=body,
                normalized=normalized,
                body_hash=hashlib.md5(
                    normalized.encode(), usedforsecurity=False
                ).hexdigest(),
                params=_param_names(parsed, info),
                default_export=info.default_export,
                object_member=definition.object_member,
            )
        )
    return functions


def _is_member_assignment(info: SyntaxFunctionInfo) -> bool:
    """``obj.method = function () {}``: an implementation of a member, which varies by design."""
    parent = info.node.parent  # type: ignore[attr-defined]
    if parent is None or parent.type != "assignment_expression":
        return False
    target = parent.child_by_field_name("left")
    return target is None or target.type != "identifier"


def _param_names(parsed: ParsedSource, info: SyntaxFunctionInfo) -> list[str]:
    """Parameter names; a destructured parameter contributes the names it binds."""
    names: list[str] = []
    for param in info.params:
        node = param.node
        pattern = (
            node.child_by_field_name("pattern") if node.type in PARAMETERS else node
        )  # type: ignore[attr-defined]
        if pattern is None or pattern.type == "this":
            continue
        if pattern.type in ("object_pattern", "array_pattern"):
            bound = sorted(binding_names(pattern), key=lambda n: n.start_byte)
            names.extend(parsed.text(n) for n in bound)
        else:
            names.append(param.name)
    return names


def _extract_ts_functions_regex(filepath: str) -> list[FunctionInfo]:
    p = (
        Path(filepath)
        if Path(filepath).is_absolute()
        else get_project_root() / filepath
    )
    try:
        content = read_code_text(p)
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug(
            "Skipping unreadable TS file %s in function extraction: %s", filepath, exc
        )
        return []

    lines = split_lines(content)
    functions = []
    fn_re = re.compile(
        r"^(?:export\s+)?(?:"
        r"(?:function\s+(\w+))|"
        r"(?:const\s+(\w+)\s*(?::\s*[^=]+?)?\s*=\s*[^;{]*?=>)|"
        r"(?:const\s+(\w+)\s*(?::\s*[^=]+?)?\s*=\s*function)"
        r")"
    )

    line_idx = 0
    while line_idx < len(lines):
        match = fn_re.match(lines[line_idx].strip())
        if not match:
            line_idx += 1
            continue

        name = match.group(1) or match.group(2) or match.group(3)
        if not name:
            line_idx += 1
            continue

        found_open, end_line = _find_function_end(lines, line_idx)
        if found_open and end_line > line_idx:
            body_lines = lines[line_idx : end_line + 1]
            body = "\n".join(body_lines)
            normalized = normalize_ts_body(body)
            sig = _extract_signature(lines, line_idx, end_line)
            params = _extract_ts_params(sig)

            if len(normalized.splitlines()) >= 3:
                functions.append(
                    FunctionInfo(
                        name=name,
                        file=filepath,
                        line=line_idx + 1,
                        end_line=end_line + 1,
                        loc=end_line - line_idx + 1,
                        body=body,
                        normalized=normalized,
                        body_hash=hashlib.md5(
                            normalized.encode(),
                            usedforsecurity=False,
                        ).hexdigest(),
                        params=params,
                    )
                )
            line_idx = end_line + 1
            continue

        line_idx += 1

    return functions


def normalize_ts_body(body: str) -> str:
    """Normalize a TS/TSX function body for comparison."""
    lines = body.splitlines()
    normalized = []
    for line in lines:
        stripped = line.strip()
        if (
            not stripped
            or stripped.startswith("//")
            or stripped.startswith("/*")
            or stripped.startswith("*")
        ):
            continue
        if "console." in stripped:
            continue
        normalized.append(stripped)
    return "\n".join(normalized)


__all__ = [
    "_extract_ts_params",
    "_parse_param_names",
    "extract_ts_functions",
    "normalize_ts_body",
]
