"""Error-handling and global-state TypeScript smell detectors."""

from __future__ import annotations

import re

from desloppify.languages.typescript.syntax.tree import ParsedSource, parse_text

from .detector_core import (
    _CATCH_DEFAULT_FIELD_THRESHOLD,
    _MAX_CATCH_BODY,
    _MAX_SWITCH_BODY_SCAN,
    _SWITCH_CASE_MINIMUM,
    _emit,
)
from .helpers import (
    _content_line_info,
    _extract_block_body,
    _strip_ts_comments,
    _ts_match_is_in_string,
)


def _detect_catch_return_default(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find catch blocks that return default/no-op object literals."""
    catch_re = re.compile(r"catch\s*\([^)]*\)\s*\{")
    for match in catch_re.finditer(ctx.content):
        body = _extract_block_body(ctx.content, match.end() - 1, _MAX_CATCH_BODY)
        if body is None:
            continue

        return_obj = re.search(r"\breturn\s*\{", body)
        if not return_obj:
            continue

        obj_start = body.find("{", return_obj.start())
        obj_content = _extract_block_body(body, obj_start)
        if obj_content is None:
            continue

        noop_count = len(re.findall(r"\(\)\s*=>\s*\{\s*\}", obj_content))
        false_count = len(re.findall(r":\s*(?:false|null|undefined|0|''|\"\")\b", obj_content))
        if noop_count + false_count >= _CATCH_DEFAULT_FIELD_THRESHOLD:
            line_no, snippet = _content_line_info(ctx.content, match.start())
            _emit(smell_counts, "catch_return_default", ctx, line_no, snippet)


_EFFECT_CALLEES = frozenset({"useEffect", "React.useEffect"})
_EFFECT_CALLBACKS = frozenset({"arrow_function", "function_expression", "function"})
_EFFECT_START = re.compile(
    r"(?:React\.)?useEffect\s*\(\s*(?:\(\s*\)\s*=>|function\s*\(\s*\))\s*\{"
)
_DEAD_BODY = re.compile(r"\s*(?:return\s*;?\s*)?")


def _detect_dead_useeffects(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find useEffect calls whose callback body is empty, comment-only or a bare ``return;``.

    On the syntax tree when tree-sitter is available; otherwise a line regex
    that only sees calls starting a line.
    """
    if "useEffect" not in ctx.content:
        return
    parsed = parse_text(ctx.content, ctx.filepath)
    found = _dead_effects_regex(ctx) if parsed is None else _dead_effects_tree(parsed)
    for row, line in found:
        _emit(smell_counts, "dead_useeffect", ctx, row + 1, line.strip()[:100])


def _dead_effects_tree(parsed: ParsedSource) -> list[tuple[int, str]]:
    """(row, line text) of each dead effect; rows count ``\\n`` only, as the fixer's do."""
    found: dict[int, str] = {}
    source = parsed.source
    stack = [parsed.root]
    while stack:
        node = stack.pop()
        stack.extend(node.named_children)
        if node.type != "call_expression":
            continue
        function = node.child_by_field_name("function")
        args = node.child_by_field_name("arguments")
        if function is None or parsed.text(function) not in _EFFECT_CALLEES:
            continue
        values = [] if args is None else [a for a in args.named_children if a.type != "comment"]
        if not values or values[0].type not in _EFFECT_CALLBACKS:
            continue
        body = values[0].child_by_field_name("body")
        if body is None or body.type != "statement_block":
            continue
        statements = [s for s in body.named_children if s.type != "comment"]
        if not statements or (
            len(statements) == 1
            and statements[0].type == "return_statement"
            and not statements[0].named_children
        ):
            start = source.rfind(b"\n", 0, node.start_byte) + 1
            end = source.find(b"\n", node.start_byte)
            line = source[start : len(source) if end == -1 else end]
            found[node.start_point[0]] = line.decode("utf-8", "replace")
    return sorted(found.items())


def _dead_effects_regex(ctx) -> list[tuple[int, str]]:
    found = []
    for line_no, line in enumerate(ctx.lines):
        if line_no in ctx.line_state:
            continue
        match = _EFFECT_START.match(line.strip())
        if not match:
            continue
        text = "\n".join(ctx.lines[line_no : line_no + 30])
        brace_pos = text.find("{", text.find("useEffect"))
        body = _extract_block_body(text, brace_pos)
        if body is not None and _DEAD_BODY.fullmatch(_strip_ts_comments(body)):
            found.append((line_no, line))
    return found


def _detect_swallowed_errors(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find catch blocks whose only content is console.error/warn/log."""
    catch_re = re.compile(r"catch\s*\([^)]*\)\s*\{")
    for match in catch_re.finditer(ctx.content):
        body = _extract_block_body(ctx.content, match.end() - 1, 500)
        if body is None:
            continue

        body_clean = _strip_ts_comments(body).strip()
        if not body_clean:
            continue

        statements = [
            stmt.strip().rstrip(";")
            for stmt in re.split(r"[;\n]", body_clean)
            if stmt.strip()
        ]
        if not statements:
            continue

        all_console = all(
            re.match(r"console\.(error|warn|log)\s*\(", stmt) for stmt in statements
        )
        if all_console:
            line_no, snippet = _content_line_info(ctx.content, match.start())
            _emit(smell_counts, "swallowed_error", ctx, line_no, snippet)


def _detect_switch_no_default(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Flag switch statements that have no default case."""
    switch_re = re.compile(r"\bswitch\s*\([^)]*\)\s*\{")
    for match in switch_re.finditer(ctx.content):
        body = _extract_block_body(ctx.content, match.end() - 1, _MAX_SWITCH_BODY_SCAN)
        if body is None:
            continue

        case_count = len(re.findall(r"\bcase\s+", body))
        if case_count < _SWITCH_CASE_MINIMUM:
            continue
        if re.search(r"\bdefault\s*:", body):
            continue

        line_no, snippet = _content_line_info(ctx.content, match.start())
        _emit(smell_counts, "switch_no_default", ctx, line_no, snippet)


def _detect_window_globals(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find ``window.__*`` assignments used as global escape hatches."""
    window_re = re.compile(
        r"""(?:"""
        r"""\(?\s*window\s+as\s+any\s*\)?\s*\.\s*(?:__\w+)"""
        r"""|window\s*\.\s*(?:__\w+)"""
        r"""|window\s*\[\s*['\"](?:__\w+)['\"]\s*\]"""
        r""")\s*=""",
    )
    for index, line in enumerate(ctx.lines):
        if index in ctx.line_state:
            continue
        match = window_re.search(line)
        if not match:
            continue
        if _ts_match_is_in_string(line, match.start()):
            continue
        _emit(smell_counts, "window_global", ctx, index + 1, line.strip()[:100])


__all__ = [
    "_detect_catch_return_default",
    "_detect_dead_useeffects",
    "_detect_swallowed_errors",
    "_detect_switch_no_default",
    "_detect_window_globals",
]
