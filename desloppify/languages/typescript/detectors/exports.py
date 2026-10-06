"""Dead exports detection via Knip."""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.source import find_ts_and_tsx_files
from desloppify.base.output.terminal import colorize, print_table
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.knip_adapter import detect_with_knip_result

_EXPORT_STATEMENT_RE = re.compile(r"^\s*export\b", re.MULTILINE)
_KNIP_REMEDIATION = {
    "knip_not_installed": "Install Knip in the project (npm i -D knip) and rerun scan.",
    "no_package_json": "Scan a directory inside a Node package (with package.json).",
}


def _count_exports(path: Path) -> int:
    """Approximate the export population so dead-export scores are proportional."""
    total = 0
    for filepath in find_ts_and_tsx_files(path):
        try:
            text = Path(resolve_path(filepath)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(_EXPORT_STATEMENT_RE.findall(text))
    return total


def detect_dead_exports_result(
    path: Path,
) -> tuple[list[dict], int, DetectorCoverageStatus | None]:
    """Return (dead_export_entries, total_exports, coverage) using Knip."""
    entries, reason = detect_with_knip_result(path)
    if entries is None:
        coverage = DetectorCoverageStatus(
            detector="exports",
            status="reduced",
            confidence=0.0,
            summary=f"Dead-export detection skipped: Knip did not run ({reason})",
            impact="Unused exports are not reported for this scan.",
            remediation=_KNIP_REMEDIATION.get(
                reason or "", "Check that `npx knip` runs in this project and rerun scan."
            ),
            tool="knip",
            reason=reason or "knip_failed",
        )
        return [], 0, coverage
    return entries, max(len(entries), _count_exports(path)), None


def detect_dead_exports(path: Path) -> tuple[list[dict], int]:
    """Return (dead_export_entries, total_exports) using Knip."""
    entries, total, _coverage = detect_dead_exports_result(path)
    return entries, total


def cmd_exports(args: argparse.Namespace) -> None:
    print(colorize("Scanning exports via Knip...", "dim"), file=sys.stderr)
    entries, _ = detect_dead_exports(Path(args.path))
    if args.json:
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return

    if not entries:
        print(colorize("No dead exports found.", "green"))
        return

    by_file: dict[str, list] = defaultdict(list)
    for e in entries:
        by_file[e["file"]].append(e)

    print(colorize(f"\nDead exports: {len(entries)} across {len(by_file)} files\n", "bold"))

    sorted_files = sorted(by_file.items(), key=lambda x: -len(x[1]))
    rows = []
    for filepath, file_entries in sorted_files[: args.top]:
        names = ", ".join(e["name"] for e in file_entries[:5])
        if len(file_entries) > 5:
            names += f", ... (+{len(file_entries) - 5})"
        rows.append([rel(filepath), str(len(file_entries)), names])
    print_table(["File", "Count", "Exports"], rows, [55, 6, 50])
