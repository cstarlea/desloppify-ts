"""Tagged console.log('[Tag]') detection.

Catches:
- Direct tags: console.log('[Tag] ...')
- Emoji-prefixed tags: console.log('🔍 [Tag] ...')
- Template-literal tags: console.log(`${TAG_VAR} ...`) where TAG_VAR = '[Tag]'

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
from desloppify.base.discovery.source import find_ts_and_js_files, read_file_text
from desloppify.base.output.terminal import colorize, print_table
from desloppify.languages.typescript.detectors.contracts import DetectorResult
from desloppify.languages.typescript.syntax.queries import calls
from desloppify.languages.typescript.syntax.scanner import SourceText
from desloppify.languages.typescript.syntax.tree import ParsedSource, parsed_file

logger = logging.getLogger(__name__)


TAG_EXTRACT_RE = re.compile(r"\[([^\]]+)\]")

_LOG_CALLEES = frozenset(f"console.{method}" for method in ("log", "warn", "info", "debug"))
_TAGGED_FIRST_ARG_RE = re.compile(
    r"""^['"`].{0,4}\[|^`\$\{\w*(?:TAG|DEBUG|LOG)\w*\}""", re.IGNORECASE
)

# Without tree-sitter: calls that start in code with the tagged argument on the same line.
# Pattern 1: Direct and emoji-prefixed tags
_PAT1 = re.compile(r"console\.(log|warn|info|debug)\s*\(\s*['\"`].{0,4}\[")
# Pattern 2: Template-literal tag via variable containing TAG/DEBUG/LOG
_PAT2 = re.compile(r"console\.(log|warn|info|debug)\s*\(\s*`\$\{\w*(TAG|DEBUG|LOG)\w*\}", re.IGNORECASE)


def tagged_console_calls(parsed: ParsedSource) -> list:
    """``console.log/warn/info/debug`` calls whose first argument is tagged:
    ``'[Tag] ...'``, ``'🔍 [Tag]'`` or `` `${DEBUG_TAG} ...` ``."""
    return [
        call.node
        for call in calls(parsed, _LOG_CALLEES)
        if call.arguments and _TAGGED_FIRST_ARG_RE.match(parsed.text(call.arguments[0]))
    ]


def detect_logs(path: Path) -> DetectorResult[dict]:
    """Detect tagged logs with explicit population semantics."""
    ts_files = find_ts_and_js_files(path)
    entries = []
    for filepath in ts_files:
        parsed = parsed_file(filepath)
        if parsed is not None:
            entries.extend(_tree_logs(filepath, parsed))
            continue
        content = read_file_text(filepath)
        if content is not None:
            entries.extend(_regex_logs(filepath, content))
    return DetectorResult(entries=entries, population_kind="files", population_size=len(ts_files))


def _tree_logs(filepath: str, parsed: ParsedSource) -> list[dict]:
    found: dict[int, dict] = {}
    source = parsed.source
    for call in tagged_console_calls(parsed):
        row = call.start_point[0]
        if row in found:
            continue
        start = source.rfind(b"\n", 0, call.start_byte) + 1
        end = source.find(b"\n", call.start_byte)
        line = source[start : len(source) if end == -1 else end].decode("utf-8", "replace")
        args = call.child_by_field_name("arguments")
        first = next(a for a in args.named_children if a.type != "comment")
        tag = TAG_EXTRACT_RE.search(parsed.text(first))
        found[row] = {
            "file": filepath,
            "line": row + 1,
            "tag": tag.group(1) if tag else "unknown",
            "content": line.strip(),
        }
    return [found[row] for row in sorted(found)]


def _regex_logs(filepath: str, content: str) -> list[dict]:
    source = SourceText(content)
    entries = []
    for index, line in enumerate(source.lines):
        match = source.search(_PAT1, index) or source.search(_PAT2, index)
        if match is None:
            continue
        tag = TAG_EXTRACT_RE.search(line, match.start())
        entries.append(
            {"file": filepath, "line": index + 1, "tag": tag.group(1) if tag else "unknown", "content": line.strip()}
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
