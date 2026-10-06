"""File-grouped write pipeline for TypeScript fixer transforms."""

from __future__ import annotations

import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.discovery.file_paths import rel
from desloppify.base.output.terminal import colorize
from desloppify.base.discovery.paths import get_project_root

logger = logging.getLogger(__name__)


def _group_entries(entries: list[dict], file_key: str) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {}
    for entry in entries:
        filepath = entry.get(file_key)
        if not isinstance(filepath, str) or not filepath:
            continue
        grouped.setdefault(filepath, []).append(entry)
    return grouped


def apply_fixer(
    entries: list[dict], transform_fn, *, dry_run: bool = False, file_key: str = "file"
) -> list[dict]:
    """Shared file-loop template for fixers."""
    by_file = _group_entries(entries, file_key)
    results = []
    skipped_files: list[tuple[str, str]] = []
    for filepath, file_entries in sorted(by_file.items()):
        try:
            changed = _process_fixer_file(
                filepath,
                file_entries,
                transform_fn=transform_fn,
                dry_run=dry_run,
            )
            if changed is not None:
                results.append(changed)
        except (OSError, UnicodeDecodeError) as ex:
            skipped_files.append((filepath, str(ex)))
            print(colorize(f"  Skip {rel(filepath)}: {ex}", "yellow"), file=sys.stderr)

    if skipped_files:
        log_best_effort_failure(
            logger,
            f"apply TypeScript fixer across {len(skipped_files)} skipped file(s)",
            OSError(
                "; ".join(f"{path}: {reason}" for path, reason in skipped_files[:5])
            ),
        )

    return results


_UTF8_BOM = "\ufeff"


def _process_fixer_file(
    filepath: str,
    file_entries: list[dict],
    *,
    transform_fn,
    dry_run: bool,
) -> dict[str, object] | None:
    path = Path(filepath) if Path(filepath).is_absolute() else get_project_root() / filepath
    raw = path.read_bytes().decode("utf-8")  # undecodable files are skipped
    has_bom = raw.startswith(_UTF8_BOM)
    uses_crlf = "\r\n" in raw
    # Transforms see plain "\n" text with no BOM, so line-1 imports match.
    original = raw.removeprefix(_UTF8_BOM).replace("\r\n", "\n")
    lines = original.splitlines(keepends=True)

    new_lines, removed_names = transform_fn(lines, file_entries)
    new_content = "".join(new_lines)
    if new_content == original:
        return None

    if not dry_run:
        restored = new_content.replace("\n", "\r\n") if uses_crlf else new_content
        _write_fixer_content(path, (_UTF8_BOM if has_bom else "") + restored)

    lines_removed = len(original.splitlines()) - len(new_content.splitlines())
    return {
        "file": filepath,
        "removed": removed_names,
        "lines_removed": lines_removed,
    }


def _write_fixer_content(path: Path, content: str) -> None:
    """Atomically replace a source file, keeping its target, mode and bytes as given."""
    target = path.resolve()  # write through symlinks instead of replacing them
    try:
        mode = stat.S_IMODE(target.stat().st_mode)
        fd, tmp = tempfile.mkstemp(dir=target.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            os.chmod(tmp, mode)
            os.replace(tmp, target)
        except OSError:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise
    except OSError as exc:
        log_best_effort_failure(logger, f"write TypeScript fixer output {path}", exc)
        raise


__all__ = ["apply_fixer"]
