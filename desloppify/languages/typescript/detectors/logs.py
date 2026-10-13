"""Tagged debug-log detection: ``console.log/debug/trace('[Tag] ...')``.

Catches:
- Direct tags: console.log('[Tag] ...')
- Emoji-prefixed tags: console.log('🔍 [Tag] ...')
- Template-literal tags: console.log(`${TAG_VAR} ...`) where TAG_VAR = '[Tag]'

Not reported, because the output is intentional:
- ``console.warn/error/info``;
- ANSI colour codes (``'\\x1b[36m'``), which look like a tag but aren't one;
- calls behind a dev or env check (``if (isDev)``,
  ``if (process.env.NODE_ENV !== 'production')``, ``import.meta.env.DEV && ...``,
  or after ``if (isProduction) return``);
- files in the script zone, which are CLIs that print on purpose.

Calls come from the syntax tree, so a log in a comment or string isn't one;
each is reported on the line where the call starts, where the debug-logs
fixer looks for it.
"""

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.base.discovery.sfc import code_text
from desloppify.base.discovery.source import (
    find_component_files,
    find_ts_and_js_files,
    read_file_text,
)
from desloppify.base.output.terminal import colorize, print_table
from desloppify.engine.policy.zones import FileZoneMap, should_skip_issue
from desloppify.languages.typescript._zones import TS_ZONE_RULES
from desloppify.languages.typescript.detectors.contracts import DetectorResult
from desloppify.languages.typescript.syntax.nodes import same
from desloppify.languages.typescript.syntax.queries import calls
from desloppify.languages.typescript.syntax.scanner import SourceText
from desloppify.languages.typescript.syntax.tree import ParsedSource, parsed_file

logger = logging.getLogger(__name__)


TAG_EXTRACT_RE = re.compile(r"\[([^\]]+)\]")
# ANSI escapes as written in source: '\x1b[36m', '\u001b[0m', '\033[1;31m'.
_ANSI_RE = re.compile(
    r"(?:\\x1b|\\u001b|\\u\{1b\}|\\033|\\e|\x1b)\[[0-9;]*[A-Za-z]", re.IGNORECASE
)

DEBUG_LOG_CALLEES = frozenset(
    f"console.{method}" for method in ("log", "debug", "trace")
)
# Every console call that can carry a tag. warn/error/info are intentional
# output; the fixer looks them up only to say why it leaves one alone.
CONSOLE_CALLEES = DEBUG_LOG_CALLEES | {
    f"console.{method}" for method in ("warn", "error", "info")
}
_TAGGED_FIRST_ARG_RE = re.compile(r"""^['"`].{0,4}\[""")
_TAG_VAR_RE = re.compile(r"^`\$\{(\w*(?:TAG|DEBUG|LOG)\w*)\}", re.IGNORECASE)

# Without tree-sitter: calls that start in code with the tagged argument on the same line.
_REGEX_CALL_RE = re.compile(r"console\.(?:log|debug|trace)\s*\(\s*")

# A condition that checks the environment or a dev/debug flag.
_ENV_CHECK_RE = re.compile(
    r"\bprocess\.env\b|\bimport\.meta\.env\b|\b__DEV__\b"
    r"|\b(?:is_?)?(?:dev|development|prod|production|debug)(?:_?(?:mode|env))?\b",
    re.IGNORECASE,
)
_EXITS = frozenset({"return_statement", "throw_statement"})


def log_tag(source: str, first_arg: str) -> str | None:
    """The tag of a log's first argument, or None when it carries none.

    ``'[Tag] ...'`` and ``'🔍 [Tag]'`` give ``Tag``; `` `${DEBUG_TAG} ...` ``
    gives the bracketed value ``source`` assigns to ``DEBUG_TAG``, else the
    variable's name. ANSI colour codes are not tags.
    """
    text = _ANSI_RE.sub("", first_arg)
    if _TAGGED_FIRST_ARG_RE.match(text):
        tag = TAG_EXTRACT_RE.search(text)
        return tag.group(1) if tag else None
    var = _TAG_VAR_RE.match(text)
    if var is None:
        return None
    name = var.group(1)
    value = re.search(
        rf"\b{re.escape(name)}\s*=\s*['\"`][^'\"`\n]{{0,4}}\[([^\]\n]+)\]", source
    )
    return value.group(1) if value else name


def tagged_console_calls(
    parsed: ParsedSource, callees: frozenset[str] = DEBUG_LOG_CALLEES
) -> list:
    """Calls to ``callees`` whose first argument is tagged:
    ``'[Tag] ...'``, ``'🔍 [Tag]'`` or `` `${DEBUG_TAG} ...` ``."""
    source = parsed.source.decode("utf-8", "replace")
    return [
        call.node
        for call in calls(parsed, callees)
        if call.arguments and log_tag(source, parsed.text(call.arguments[0]))
    ]


def in_env_guard(parsed: ParsedSource, node) -> bool:
    """Whether ``node`` only runs behind a dev or env check.

    That is a branch of an ``if`` or ternary, or the right side of ``&&``/``||``,
    whose condition checks the environment, or a statement that follows an
    ``if (<env check>) return/throw`` in an enclosing block.
    """
    child, parent = node, node.parent
    while parent is not None:
        condition = None
        if parent.type in ("if_statement", "ternary_expression"):
            condition = parent.child_by_field_name("condition")
        elif parent.type == "binary_expression":
            operator = parent.child_by_field_name("operator")
            if operator is not None and parsed.text(operator) in ("&&", "||"):
                condition = parent.child_by_field_name("left")
        if (
            condition is not None
            and not same(condition, child)
            and _ENV_CHECK_RE.search(parsed.text(condition))
        ):
            return True
        if parent.type in ("statement_block", "program"):
            sibling = child.prev_named_sibling
            while sibling is not None:
                if _is_env_exit(parsed, sibling):
                    return True
                sibling = sibling.prev_named_sibling
        child, parent = parent, parent.parent
    return False


def _is_env_exit(parsed: ParsedSource, statement) -> bool:
    """``if (<env check>) return/throw ...`` with no ``else``."""
    if statement.type != "if_statement" or statement.child_by_field_name("alternative"):
        return False
    condition = statement.child_by_field_name("condition")
    body = statement.child_by_field_name("consequence")
    if condition is None or body is None:
        return False
    if body.type == "statement_block":
        body_statements = [c for c in body.named_children if c.type != "comment"]
        body = body_statements[-1] if body_statements else None
    return (
        body is not None
        and body.type in _EXITS
        and bool(_ENV_CHECK_RE.search(parsed.text(condition)))
    )


def detect_logs(
    path: Path, zone_map: FileZoneMap | None = None
) -> DetectorResult[dict]:
    """Detect tagged debug logs with explicit population semantics.

    Files whose zone skips ``logs`` (scripts, generated, vendor) are left out;
    without ``zone_map`` the TypeScript zone rules classify them.
    """
    ts_files = [*find_ts_and_js_files(path), *find_component_files(path)]
    if zone_map is None:
        zone_map = FileZoneMap(ts_files, TS_ZONE_RULES, rel_fn=rel)
    entries = []
    for filepath in ts_files:
        if should_skip_issue(zone_map, filepath, "logs"):
            continue
        parsed = parsed_file(filepath)
        if parsed is not None:
            entries.extend(_tree_logs(filepath, parsed))
            continue
        content = read_file_text(filepath)
        if content is not None:
            entries.extend(_regex_logs(filepath, code_text(content, filepath)))
    return DetectorResult(
        entries=entries, population_kind="files", population_size=len(ts_files)
    )


def _tree_logs(filepath: str, parsed: ParsedSource) -> list[dict]:
    source = parsed.source.decode("utf-8", "replace")
    found: dict[int, dict] = {}
    for call in calls(parsed, DEBUG_LOG_CALLEES):
        if not call.arguments or call.line in found:
            continue
        tag = log_tag(source, parsed.text(call.arguments[0]))
        if tag is None or in_env_guard(parsed, call.node):
            continue
        found[call.line] = {
            "file": filepath,
            "line": call.line,
            "tag": tag,
            "content": parsed.line_text(call.node).strip(),
        }
    return [found[line_no] for line_no in sorted(found)]


def _regex_logs(filepath: str, content: str) -> list[dict]:
    source = SourceText(content)
    entries = []
    for index, line in enumerate(source.lines):
        match = source.search(_REGEX_CALL_RE, index)
        if match is None:
            continue
        tag = log_tag(content, line[match.end() :])
        if tag is None:
            continue
        entries.append(
            {
                "file": filepath,
                "line": index + 1,
                "tag": tag,
                "content": line.strip(),
            }
        )
    return entries


def cmd_logs(args: argparse.Namespace) -> None:
    result = detect_logs(Path(args.path))
    entries = result.entries
    if args.json:
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return

    if not entries:
        print(colorize("No tagged console.logs found.", "green"))
        return

    by_file: dict[str, list] = defaultdict(list)
    for e in entries:
        by_file[e["file"]].append(e)
    sorted_files = sorted(by_file.items(), key=lambda x: -len(x[1]))

    by_tag: dict[str, int] = defaultdict(int)
    for e in entries:
        by_tag[e["tag"]] += 1

    print(
        colorize(
            f"\nTagged console.logs: {len(entries)} across {len(by_file)} files\n",
            "bold",
        )
    )

    print(colorize("Top tags:", "cyan"))
    for tag, count in sorted(by_tag.items(), key=lambda x: -x[1])[:10]:
        print(f"  [{tag}] × {count}")
    print()

    print(colorize("Top files:", "cyan"))
    rows = []
    for filepath, file_entries in sorted_files[: args.top]:
        rows.append([rel(filepath), str(len(file_entries))])
    print_table(["File", "Count"], rows, [70, 6])

    if args.fix:
        # The old line-deleting fixer here removed only the first line of
        # multi-line calls; all log removal goes through the autofix gate now.
        print(
            colorize(
                "\n--fix is no longer supported here. Preview with "
                "`desloppify autofix debug-logs --dry-run`.",
                "yellow",
            )
        )
