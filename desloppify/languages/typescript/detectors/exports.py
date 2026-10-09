"""Dead exports detection via Knip: unused exports, types and enum members,
and duplicate exports."""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.base.output.terminal import colorize, print_table
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.deps.public_api import public_export_names
from desloppify.languages.typescript.detectors.knip_adapter import detect_with_knip_result

_EXPORT_STATEMENT_RE = re.compile(r"^\s*export\b", re.MULTILINE)
_KNIP_REMEDIATION = {
    "knip_not_installed": "Install Knip in the project (npm i -D knip) and rerun scan.",
    "no_package_json": "Scan a directory inside a Node package (with package.json).",
}


def _count_exports(path: Path) -> int:
    """Approximate the export population so dead-export scores are proportional."""
    total = 0
    for filepath in find_ts_and_js_files(path):
        try:
            text = Path(resolve_path(filepath)).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        total += len(_EXPORT_STATEMENT_RE.findall(text))
    return total


def _published(entry: dict, public: set[tuple[str, str]]) -> bool:
    """Whether a published package exposes the export (or the enum holding the member)."""
    if entry.get("kind") == "duplicate":
        return False
    name = entry["name"].split(".", 1)[0] if entry.get("kind") == "enum_member" else entry["name"]
    return (entry["file"], name) in public


def drop_public_api(path: Path, entries: list[dict]) -> list[dict]:
    """Drop exports a published package's entry points expose, directly or
    through re-exports: they are its public API, used outside the repo.

    Knip without a config of the project's own doesn't map a manifest's
    ``exports`` (often ``dist/``) back to source, so it reports them.
    """
    if not any(entry.get("kind") != "duplicate" for entry in entries):
        return entries
    candidates = [resolve_path(f) for f in find_ts_and_js_files(path)]
    public = public_export_names(candidates, get_project_root())
    if not public:
        return entries
    return [entry for entry in entries if not _published(entry, public)]


def detect_dead_exports_result(
    path: Path, *, cache: dict[str, Any] | None = None
) -> tuple[list[dict], int, DetectorCoverageStatus | None]:
    """Return (dead_export_entries, total_exports, coverage) using Knip.

    ``cache`` (the scan's runtime cache) shares the Knip run with the other
    Knip readers.
    """
    entries, reason = detect_with_knip_result(path, cache=cache)
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
    entries = drop_public_api(path, entries)
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
