"""Terminal rendering helpers for `desloppify autofix --dry-run`."""

from __future__ import annotations

import logging
from pathlib import Path

from desloppify.base.discovery.file_paths import rel
from desloppify.base.output.terminal import colorize

_logger = logging.getLogger(__name__)


_MAX_DIFF_FILES = 5
_MAX_DIFF_LINES = 40


def show_fix_dry_run_samples(entries: list[dict], results: list[dict]) -> None:
    """Print the diffs (or sampled context) that fix --dry-run would apply."""
    print(colorize("\n  ── Sample changes (before → after) ──", "cyan"))
    for result in results[:_MAX_DIFF_FILES]:
        diff = result.get("diff")
        if isinstance(diff, str) and diff:
            _print_diff(diff)
        else:
            _print_fix_file_sample(result, entries)
    if len(results) > _MAX_DIFF_FILES:
        print(colorize(f"\n  ... and {len(results) - _MAX_DIFF_FILES} more files", "dim"))
    removed_count = sum(len(r["removed"]) if "removed" in r else 1 for r in results)
    if len(entries) > removed_count:
        print(colorize(f"\n  Note: {len(entries) - removed_count} of {len(entries)} entries were skipped (complex patterns, rest elements, etc.)", "dim"))
    print()


def _print_diff(diff: str) -> None:
    lines = diff.splitlines()
    print()
    for line in lines[:_MAX_DIFF_LINES]:
        if line.startswith(("---", "+++")):
            print(colorize(f"    {line}", "cyan"))
        elif line.startswith("@@"):
            print(colorize(f"    {line}", "dim"))
        elif line.startswith("-"):
            print(colorize(f"    {line}", "red"))
        elif line.startswith("+"):
            print(colorize(f"    {line}", "green"))
        else:
            print(f"    {line}")
    if len(lines) > _MAX_DIFF_LINES:
        print(colorize(f"    ... {len(lines) - _MAX_DIFF_LINES} more diff lines", "dim"))


def _print_fix_file_sample(result: dict, entries: list[dict]) -> None:
    filepath, removed_set = result["file"], set(result.get("removed", []))
    try:
        path = Path(filepath) if Path(filepath).is_absolute() else Path(".") / filepath
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError) as exc:
        _logger.debug("dry-run sample skipped for %s: %s", filepath, exc)
        return

    file_entries = [entry for entry in entries if entry["file"] == filepath and entry.get("name", "") in removed_set]
    shown = 0
    for entry in file_entries[:2]:
        line_idx = entry.get("line", entry.get("detail", {}).get("line", 0)) - 1
        if line_idx < 0 or line_idx >= len(lines):
            continue
        if shown == 0:
            print(colorize(f"\n  {rel(filepath)}:", "cyan"))
        name = entry.get("name", entry.get("summary", "?"))
        ctx_s, ctx_e = max(0, line_idx - 1), min(len(lines), line_idx + 2)
        print(colorize(f"    {name} (line {line_idx + 1}):", "dim"))
        for idx in range(ctx_s, ctx_e):
            marker = colorize("  →", "red") if idx == line_idx else "   "
            print(f"    {marker} {idx+1:4d}  {lines[idx][:90]}")
        shown += 1
