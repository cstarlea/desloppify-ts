"""Tagged console.log('[Tag]') detection.

Catches:
- Direct tags: console.log('[Tag] ...')
- Emoji-prefixed tags: console.log('🔍 [Tag] ...')
- Template-literal tags: console.log(`${TAG_VAR} ...`) where TAG_VAR = '[Tag]'
"""

import argparse
import json
import logging
import re
from collections import defaultdict
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.base.discovery.source import find_ts_and_tsx_files
from desloppify.base.output.terminal import colorize, print_table
from desloppify.base.search.grep import grep_files
from desloppify.languages.typescript.detectors.contracts import DetectorResult

logger = logging.getLogger(__name__)


TAG_EXTRACT_RE = re.compile(r"\[([^\]]+)\]")

# Pattern 1: Direct and emoji-prefixed tags
_PAT1 = r"console\.(log|warn|info|debug)\s*\(\s*['\"`].{0,4}\["
# Pattern 2: Template-literal tag via variable containing TAG/DEBUG/LOG
_PAT2 = r"console\.(log|warn|info|debug)\s*\(\s*`\$\{\w*(TAG|DEBUG|LOG)\w*\}"


def detect_logs(path: Path) -> DetectorResult[dict]:
    """Detect tagged logs with explicit population semantics."""
    ts_files = find_ts_and_tsx_files(path)
    total_files = len(ts_files)

    hits1 = grep_files(_PAT1, ts_files)
    hits2 = grep_files(_PAT2, ts_files, flags=re.IGNORECASE)

    seen: set[tuple[str, int]] = set()
    entries = []
    for filepath, lineno, content in hits1 + hits2:
        key = (filepath, lineno)
        if key in seen:
            continue
        seen.add(key)
        tag_match = TAG_EXTRACT_RE.search(content)
        tag = tag_match.group(1) if tag_match else "unknown"
        entries.append(
            {"file": filepath, "line": lineno, "tag": tag, "content": content.strip()}
        )

    return DetectorResult(entries=entries, population_kind="files", population_size=total_files)


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
