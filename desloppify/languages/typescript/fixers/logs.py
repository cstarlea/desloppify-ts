"""Debug-log fixer that removes tagged ``console`` calls by syntax-tree range.

Each detected log is matched to the ``console.log/warn/info/debug`` call with a
tagged first argument (``'[Tag] ...'``, ``'🔍 [Tag]'``, `` `${DEBUG_TAG} ...` ``)
that starts on its line. The call is removed only when it is a statement of
its own in a block, with a trailing ``//`` comment, and its arguments can't
run code.

Skipped:
- calls inside a larger expression, or as the unbraced body of ``if``/``else``
  or a loop (removing them would change control flow or leave ``if (x)``
  dangling), or as an arrow function's expression body;
- arguments that call functions (other than a few pure built-ins such as
  ``JSON.stringify``), assign, await, spread or ``delete``. Property reads and
  string conversion are assumed side-effect free;
- logs that are the body of a logging helper (a function named ``log``,
  ``debug``, ``warn``…), where the log is the point;
- removals that would let a semicolon-less neighbour continue the previous
  statement.

Up to three ``//`` comment lines directly above a removed log go with it when
they mention debug, temp, log or trace. Nothing else changes: blocks left
empty, other comments and tag constants that become unused stay. The unused-vars fixer removes those
constants once a scan reports them.

Needs tree-sitter: without it the fixer changes nothing.
"""

from __future__ import annotations

import re
import sys
from collections import defaultdict

from desloppify.base.output.terminal import colorize
from desloppify.languages._framework.base.types import FixResult
from desloppify.languages.typescript.detectors.logs import tagged_console_calls
from desloppify.languages.typescript.syntax.tree import (
    ParsedSource,
    get_parser,
    parse_text,
)

from .edits import apply_edits, whole_statement_range
from .fixer_io import apply_fixer
from desloppify.languages.typescript.syntax.nodes import FUNCTIONS, STATEMENT_PARENTS, asi_hazards, node_key, reads_only

# A `//` comment directly above a removed log that only explains the log.
_DEBUG_COMMENT_RE = re.compile(r"\b(?:debug|temp|log|logging|trace)\b", re.IGNORECASE)
_LOGGER_NAMES = frozenset(
    {"log", "logger", "info", "warn", "warning", "error", "debug", "trace", "fatal", "notice"}
)



def fix_debug_logs(entries: list[dict], *, dry_run: bool = False) -> FixResult:
    """Remove tagged debug logs reported by the ``logs`` detector."""
    if entries and get_parser("tsx") is None:
        print(
            colorize(
                "  Skip: the debug-logs fixer needs tree-sitter (install desloppify[full]).",
                "yellow",
            ),
            file=sys.stderr,
        )
        return FixResult(entries=[], skip_reasons={"needs_treesitter": len(entries)})

    skip_reasons: dict[str, int] = defaultdict(int)
    logs_per_file: dict[str, int] = defaultdict(int)
    for entry in entries:
        logs_per_file[entry.get("file", "")] += 1

    def transform(lines: list[str], file_entries: list[dict]) -> tuple[list[str], list[dict]]:
        path = str(file_entries[0].get("file", "")) if file_entries else ""
        parsed = parse_text("".join(lines), path)
        if parsed is None:
            return lines, []
        new_source, fixed, skipped = remove_debug_logs(parsed, file_entries)
        for reason in skipped:
            skip_reasons[reason] += 1
        if not fixed:
            return lines, []
        return new_source.decode("utf-8").splitlines(keepends=True), fixed

    results = apply_fixer(entries, transform, dry_run=dry_run)
    for result in results:
        result["tags"] = result["removed"]
        result["log_count"] = logs_per_file[result["file"]]
    return FixResult(entries=results, skip_reasons=dict(skip_reasons))


def remove_debug_logs(
    parsed: ParsedSource, file_entries: list[dict]
) -> tuple[bytes, list[dict], list[str]]:
    """Return the edited source, the fixed entries and a skip reason per skipped entry."""
    calls_by_line: dict[int, list] = defaultdict(list)
    for call in tagged_console_calls(parsed):
        calls_by_line[parsed.line(call)].append(call)

    planned: dict[tuple, object] = {}  # statement key -> statement
    entry_statements: list[tuple[dict, list[tuple]]] = []
    skipped: list[str] = []
    for entry in file_entries:
        line = entry.get("line")
        calls = calls_by_line.get(line, []) if isinstance(line, int) else []
        if not calls:
            skipped.append("not_found")
            continue
        reason = None
        keys = []
        for call in calls:
            statement, reason = _removable_statement(parsed, call)
            if statement is None:
                break
            keys.append(node_key(statement))
            planned[node_key(statement)] = statement
        if reason is not None:
            skipped.append(reason)
            continue
        entry_statements.append((entry, keys))

    kept = set(asi_hazards(parsed.source, planned))
    fixed: list[dict] = []
    edits: list[tuple[int, int]] = []
    for entry, keys in entry_statements:
        if kept.intersection(keys):
            skipped.append("asi_hazard")
            continue
        fixed.append(entry)
        edits.extend(_log_range(parsed.source, planned[key]) for key in keys)
    return apply_edits(parsed.source, edits), fixed, skipped


def _log_range(source: bytes, statement) -> tuple[int, int]:
    """The statement's range, plus up to three debug comment lines directly above."""
    start, end = whole_statement_range(source, statement)
    if start and source[start - 1 : start] != b"\n":
        return start, end  # shares its line with other code
    comment = statement.prev_sibling
    for _ in range(3):
        if comment is None or comment.type != "comment":
            break
        text = source[comment.start_byte : comment.end_byte]
        line_start = source.rfind(b"\n", 0, comment.start_byte) + 1
        if (
            not text.startswith(b"//")
            or not _DEBUG_COMMENT_RE.search(text.decode("utf-8", "replace"))
            or source[line_start : comment.start_byte].strip()
            or source[comment.end_byte : start].strip()
            or source[comment.end_byte : start].count(b"\n") != 1
        ):
            break
        start = line_start
        comment = comment.prev_sibling
    return start, end


def _removable_statement(parsed: ParsedSource, call):
    """The statement to delete for ``call``, or None and why not."""
    statement = call.parent
    if statement is None or statement.type != "expression_statement":
        return None, "not_standalone"
    if [c for c in statement.named_children if c.type != "comment"] != [call]:
        return None, "not_standalone"
    if statement.parent is None or statement.parent.type not in STATEMENT_PARENTS:
        return None, "not_standalone"
    args = call.child_by_field_name("arguments")
    if args is not None and not all(
        reads_only(parsed, arg) for arg in args.named_children if arg.type != "comment"
    ):
        return None, "side_effects"
    if _in_logger_wrapper(parsed, statement):
        return None, "logger_wrapper"
    return statement, None


def _in_logger_wrapper(parsed: ParsedSource, statement) -> bool:
    """Whether the statement sits in a function named like a logger."""
    function = statement.parent
    while function is not None and function.type not in FUNCTIONS:
        function = function.parent
    if function is None:
        return False
    name = function.child_by_field_name("name")
    if name is None and function.parent is not None:
        holder = function.parent
        if holder.type == "variable_declarator":
            name = holder.child_by_field_name("name")
        elif holder.type == "pair":
            name = holder.child_by_field_name("key")
        elif holder.type == "assignment_expression":
            left = holder.child_by_field_name("left")
            if left is not None and left.type == "member_expression":
                left = left.child_by_field_name("property")
            name = left
    return name is not None and parsed.text(name).strip("'\"").lower() in _LOGGER_NAMES


__all__ = ["fix_debug_logs", "remove_debug_logs"]
