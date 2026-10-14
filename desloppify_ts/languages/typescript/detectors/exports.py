"""Dead exports detection via Knip: unused exports, types and enum members,
and duplicate exports."""

import argparse
import json
import re
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from desloppify_ts.base.discovery.file_paths import rel, resolve_path
from desloppify_ts.base.discovery.paths import get_project_root
from desloppify_ts.base.discovery.source import find_ts_and_js_files
from desloppify_ts.base.output.terminal import colorize, print_table
from desloppify_ts.languages._framework.base.types import DetectorCoverageStatus
from desloppify_ts.languages.typescript.detectors.deps.public_api import (
    public_export_names,
)
from desloppify_ts.languages.typescript.detectors.exports_fallback import (
    detect_dead_exports_fallback,
)
from desloppify_ts.languages.typescript.detectors.knip_adapter import (
    detect_with_knip_result,
)
from desloppify_ts.languages.typescript.presets import shadcn_ui_dirs

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
            text = Path(resolve_path(filepath)).read_text(
                encoding="utf-8", errors="replace"
            )
        except OSError:
            continue
        total += len(_EXPORT_STATEMENT_RE.findall(text))
    return total


def _published(entry: dict, public: set[tuple[str, str]]) -> bool:
    """Whether a published package exposes the export (or the enum holding the member)."""
    if entry.get("kind") == "duplicate":
        return False
    name = (
        entry["name"].split(".", 1)[0]
        if entry.get("kind") == "enum_member"
        else entry["name"]
    )
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


def drop_vendored_ui(entries: list[dict]) -> list[dict]:
    """Drop exports of a shadcn/ui kit directory: vendored library API, kept
    whole whether or not the project uses every part yet."""
    prefixes = tuple(f"{d}/" for d in shadcn_ui_dirs())
    if not prefixes:
        return entries
    return [e for e in entries if not e["file"].startswith(prefixes)]


def detect_dead_exports_result(
    path: Path, *, cache: dict[str, Any] | None = None, zone_map=None
) -> tuple[list[dict], int, DetectorCoverageStatus | None]:
    """Return (dead_export_entries, total_exports, coverage) using Knip.

    ``cache`` (the scan's runtime cache) shares the Knip run with the other
    Knip readers. When Knip can't run, a conservative import-graph check
    (``exports_fallback``) reports what it can; its entries carry
    ``"source": "fallback"`` and the coverage stays reduced.
    """
    entries, reason = detect_with_knip_result(path, cache=cache)
    if entries is None:
        fallback = detect_dead_exports_fallback(path, zone_map)
        remediation = _KNIP_REMEDIATION.get(
            reason or "",
            "Check that `npx knip` runs in this project and rerun scan.",
        )
        if fallback is None:
            summary = f"Dead-export detection skipped: Knip did not run ({reason})"
            impact = "Unused exports are not reported for this scan."
        else:
            summary = (
                f"Knip did not run ({reason}); dead exports come from a "
                "conservative import check"
            )
            impact = (
                "Only exports no file imports are reported; unused enum members "
                "and duplicate exports are not, and some dead exports are missed."
            )
        coverage = DetectorCoverageStatus(
            detector="exports",
            status="reduced",
            confidence=0.0 if fallback is None else 0.5,
            summary=summary,
            impact=impact,
            remediation=remediation,
            tool="knip",
            reason=reason or "knip_failed",
        )
        if not fallback:
            return [], 0, coverage
        for entry in fallback:
            entry["source"] = "fallback"
        fallback = drop_vendored_ui(drop_public_api(path, fallback))
        return fallback, max(len(fallback), _count_exports(path)), coverage
    entries = drop_vendored_ui(drop_public_api(path, entries))
    return entries, max(len(entries), _count_exports(path)), None


def detect_dead_exports(path: Path, zone_map=None) -> tuple[list[dict], int]:
    """Return (dead_export_entries, total_exports) using Knip."""
    entries, total, _coverage = detect_dead_exports_result(path, zone_map=zone_map)
    return entries, total


def cmd_exports(args: argparse.Namespace, zone_map=None) -> None:
    print(colorize("Scanning exports via Knip...", "dim"), file=sys.stderr)
    entries, _ = detect_dead_exports(Path(args.path), zone_map)
    if args.json:
        print(json.dumps({"count": len(entries), "entries": entries}, indent=2))
        return

    if not entries:
        print(colorize("No dead exports found.", "green"))
        return

    by_file: dict[str, list] = defaultdict(list)
    for e in entries:
        by_file[e["file"]].append(e)

    print(
        colorize(
            f"\nDead exports: {len(entries)} across {len(by_file)} files\n", "bold"
        )
    )

    sorted_files = sorted(by_file.items(), key=lambda x: -len(x[1]))
    rows = []
    for filepath, file_entries in sorted_files[: args.top]:
        names = ", ".join(e["name"] for e in file_entries[:5])
        if len(file_entries) > 5:
            names += f", ... (+{len(file_entries) - 5})"
        rows.append([rel(filepath), str(len(file_entries)), names])
    print_table(["File", "Count", "Exports"], rows, [55, 6, 50])
