"""Error-handling and global-state TypeScript smell detectors."""

from __future__ import annotations

import re

from desloppify.languages.typescript.syntax.nodes import FUNCTIONS
from desloppify.languages.typescript.syntax.queries import (
    calls,
    descendants,
    function_info,
    statements,
)
from desloppify.languages.typescript.syntax.tree import ParsedSource, parsed_file

from .detector_core import (
    _CATCH_DEFAULT_FIELD_THRESHOLD,
    _MAX_CATCH_BODY,
    _MAX_SWITCH_BODY_SCAN,
    _SWITCH_CASE_MINIMUM,
    _emit,
    _node_line,
    _parsed,
)
from .helpers import (
    _content_line_info,
    _extract_block_body,
    _strip_ts_comments,
)

_CATCH_RE = re.compile(r"catch\s*\([^)]*\)\s*\{")
_DEFAULT_VALUES = frozenset({"false", "null", "undefined", "0", "''", '""'})
_CONSOLE_LOGGERS = frozenset({"console.error", "console.warn", "console.log"})


def _catch_clauses(parsed: ParsedSource) -> list:
    return list(descendants(parsed.root, ("catch_clause",)))


def _detect_catch_return_default(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find catch blocks that return default/no-op object literals."""
    parsed = _parsed(ctx)
    if parsed is None:
        _catch_return_default_regex(ctx, smell_counts)
        return
    for clause in _catch_clauses(parsed):
        body = clause.child_by_field_name("body")
        if body is not None and any(
            _default_fields(parsed, value) >= _CATCH_DEFAULT_FIELD_THRESHOLD
            for value in _returned_objects(body)
        ):
            _emit(
                smell_counts, "catch_return_default", ctx, *_node_line(parsed, clause)
            )


def _returned_objects(body):
    """The object literals a block returns, nested functions left out."""
    stack = [body]
    while stack:
        node = stack.pop()
        if node.type == "return_statement":
            value = node.named_children[0] if node.named_children else None
            while (
                value is not None
                and value.type == "parenthesized_expression"
                and value.named_children
            ):
                value = value.named_children[0]
            if value is not None and value.type == "object":
                yield value
            continue
        stack.extend(
            child for child in node.named_children if child.type not in FUNCTIONS
        )


def _default_fields(parsed: ParsedSource, obj) -> int:
    """Fields set to a no-op ``() => {}`` or to ``false``/``null``/``undefined``/``0``/``''``."""
    count = 0
    for node in descendants(obj, ("pair", "arrow_function")):
        if node.type == "arrow_function":
            params = node.child_by_field_name("parameters")
            body = node.child_by_field_name("body")
            if (
                params is not None
                and not params.named_children
                and body is not None
                and body.type == "statement_block"
                and not body.named_children
            ):
                count += 1
        else:
            value = node.child_by_field_name("value")
            if value is not None and parsed.text(value) in _DEFAULT_VALUES:
                count += 1
    return count


def _catch_return_default_regex(ctx, smell_counts: dict[str, list[dict]]) -> None:
    for match in _CATCH_RE.finditer(ctx.source.code):
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
        false_count = len(
            re.findall(r":\s*(?:false|null|undefined|0|''|\"\")\b", obj_content)
        )
        if noop_count + false_count >= _CATCH_DEFAULT_FIELD_THRESHOLD:
            line_no, snippet = _content_line_info(ctx.content, match.start())
            _emit(smell_counts, "catch_return_default", ctx, line_no, snippet)


_EFFECT_CALLEES = frozenset({"useEffect", "React.useEffect"})
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
    parsed = parsed_file(ctx.filepath)
    found = _dead_effects_regex(ctx) if parsed is None else _dead_effects_tree(parsed)
    for row, line in found:
        _emit(smell_counts, "dead_useeffect", ctx, row + 1, line.strip()[:100])


def _dead_effects_tree(parsed: ParsedSource) -> list[tuple[int, str]]:
    """(0-based line, line text) of each dead effect."""
    found: dict[int, str] = {}
    for call in calls(parsed, _EFFECT_CALLEES):
        callback = function_info(parsed, call.arguments[0]) if call.arguments else None
        if (
            callback is None
            or callback.kind not in ("arrow", "expression")
            or callback.is_generator
            or callback.expression_body
            or callback.body is None
        ):
            continue
        body = statements(callback.body_node)
        if not body or (
            len(body) == 1
            and body[0].type == "return_statement"
            and not body[0].named_children
        ):
            found[call.line - 1] = parsed.line_text(call.node)
    return sorted(found.items())


def _dead_effects_regex(ctx) -> list[tuple[int, str]]:
    found = []
    for line_no, _match in ctx.source.line_matches(r"^\s*" + _EFFECT_START.pattern):
        line = ctx.lines[line_no]
        text = "\n".join(ctx.lines[line_no : line_no + 30])
        brace_pos = text.find("{", text.find("useEffect"))
        body = _extract_block_body(text, brace_pos)
        if body is not None and _DEAD_BODY.fullmatch(_strip_ts_comments(body)):
            found.append((line_no, line))
    return found


def _detect_swallowed_errors(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find catch blocks whose only content is console.error/warn/log."""
    parsed = _parsed(ctx)
    if parsed is None:
        _swallowed_errors_regex(ctx, smell_counts)
        return
    for clause in _catch_clauses(parsed):
        body = clause.child_by_field_name("body")
        found = statements(body) if body is not None else []
        if found and all(_is_console_log(parsed, statement) for statement in found):
            _emit(smell_counts, "swallowed_error", ctx, *_node_line(parsed, clause))


def _is_console_log(parsed: ParsedSource, statement) -> bool:
    if statement.type != "expression_statement" or not statement.named_children:
        return False
    call = statement.named_children[0]
    function = (
        call.child_by_field_name("function") if call.type == "call_expression" else None
    )
    return function is not None and parsed.text(function) in _CONSOLE_LOGGERS


def _swallowed_errors_regex(ctx, smell_counts: dict[str, list[dict]]) -> None:
    for match in _CATCH_RE.finditer(ctx.source.code):
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
    """Flag switch statements with at least two cases and no default case."""
    parsed = _parsed(ctx)
    if parsed is None:
        _switch_no_default_regex(ctx, smell_counts)
        return
    for switch in descendants(parsed.root, ("switch_statement",)):
        body = switch.child_by_field_name("body")
        if body is None:
            continue
        kinds = [child.type for child in body.named_children]
        if (
            kinds.count("switch_case") >= _SWITCH_CASE_MINIMUM
            and "switch_default" not in kinds
        ):
            _emit(smell_counts, "switch_no_default", ctx, *_node_line(parsed, switch))


def _switch_no_default_regex(ctx, smell_counts: dict[str, list[dict]]) -> None:
    switch_re = re.compile(r"\bswitch\s*\([^)]*\)\s*\{")
    for match in switch_re.finditer(ctx.source.code):
        body = _extract_block_body(
            ctx.source.code, match.end() - 1, _MAX_SWITCH_BODY_SCAN
        )
        if body is None:
            continue

        case_count = len(re.findall(r"\bcase\s+", body))
        if case_count < _SWITCH_CASE_MINIMUM:
            continue
        if re.search(r"\bdefault\s*:", body):
            continue

        line_no, snippet = _content_line_info(ctx.content, match.start())
        _emit(smell_counts, "switch_no_default", ctx, line_no, snippet)


_WINDOW_GLOBAL_RE = re.compile(
    r"""(?:"""
    r"""\(?\s*window\s+as\s+any\s*\)?\s*\.\s*(?:__\w+)"""
    r"""|window\s*\.\s*(?:__\w+)"""
    r"""|window\s*\[\s*['\"](?:__\w+)['\"]\s*\]"""
    r""")\s*=""",
)


def _detect_window_globals(ctx, smell_counts: dict[str, list[dict]]) -> None:
    """Find ``window.__*`` assignments used as global escape hatches."""
    for index, _match in ctx.source.line_matches(_WINDOW_GLOBAL_RE):
        _emit(
            smell_counts,
            "window_global",
            ctx,
            index + 1,
            ctx.lines[index].strip()[:100],
        )


__all__ = [
    "_detect_catch_return_default",
    "_detect_dead_useeffects",
    "_detect_swallowed_errors",
    "_detect_switch_no_default",
    "_detect_window_globals",
]
