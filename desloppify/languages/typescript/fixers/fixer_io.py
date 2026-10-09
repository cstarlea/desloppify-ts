"""File-grouped write pipeline for TypeScript fixer transforms."""

from __future__ import annotations

import difflib
import logging
import os
import stat
import sys
import tempfile
from pathlib import Path

from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.discovery.file_paths import rel
from desloppify.base.discovery.sfc import apply_view_change, is_sfc, sfc_code
from desloppify.base.output.terminal import colorize
from desloppify.base.discovery.paths import get_project_root
from desloppify.languages._framework.treesitter import is_available as treesitter_available
from desloppify.languages.typescript.syntax.validation import syntax_regression

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
    """Shared file-loop template for fixers.

    ``transform_fn(lines, file_entries)`` returns the new lines and the
    entries it fixed. Each result lists their display names under
    ``removed`` and their ``issue_id``s under ``fixed_issue_ids``.
    """
    by_file = _group_entries(entries, file_key)
    results = []
    skipped_files: list[tuple[str, str]] = []
    if by_file and not treesitter_available():
        print(
            colorize(
                "  Warn: tree-sitter is not installed, so fixer output is not "
                "syntax-checked before writing. Install desloppify-ts[full] to enable it.",
                "yellow",
            ),
            file=sys.stderr,
        )
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
    # Only an all-CRLF file is normalized: restoring "\r\n" everywhere would
    # turn the LF lines of a mixed file into CRLF. Mixed files are edited as
    # they are.
    uses_crlf = "\r\n" in raw and raw.count("\r\n") == raw.count("\n")
    # Transforms see plain "\n" text with no BOM, so line-1 imports match.
    original = raw.removeprefix(_UTF8_BOM)
    if uses_crlf:
        original = original.replace("\r\n", "\n")
    # A component's fixers see its code view, where the markup is blank; the
    # edit is carried back only if it stays inside the script blocks.
    component = sfc_code(original, path) if is_sfc(path) else None
    working = component.view if component is not None else original
    lines = working.splitlines(keepends=True)

    new_lines, fixed = transform_fn(lines, file_entries)
    new_working = "".join(new_lines)
    if new_working == working:
        return None
    if component is None:
        new_content = new_working
    else:
        carried = apply_view_change(component, new_working, path)
        if carried is None:
            print(
                colorize(
                    f"  Skip {rel(filepath)}: the edit reaches outside the component's "
                    "<script> blocks; file left unchanged",
                    "yellow",
                ),
                file=sys.stderr,
            )
            return None
        new_content = carried

    problem = syntax_regression(path, original, new_content)
    if problem:
        print(
            colorize(f"  Skip {rel(filepath)}: {problem}; file left unchanged", "yellow"),
            file=sys.stderr,
        )
        return None

    if not dry_run:
        restored = new_content.replace("\n", "\r\n") if uses_crlf else new_content
        _write_fixer_content(path, (_UTF8_BOM if has_bom else "") + restored)

    lines_removed = len(original.splitlines()) - len(new_content.splitlines())
    result: dict[str, object] = {
        "file": filepath,
        "removed": list(dict.fromkeys(_display_name(item) for item in fixed)),
        "fixed_issue_ids": [
            item["issue_id"]
            for item in fixed
            if isinstance(item, dict) and item.get("issue_id")
        ],
        "lines_removed": lines_removed,
    }
    if dry_run:
        result["diff"] = unified_diff(rel(filepath), original, new_content)
    return result


def _display_name(item: dict | str) -> str:
    if isinstance(item, str):
        return item
    for key in ("name", "tag", "smell_id"):
        value = item.get(key)
        if value:
            return str(value)
    return "fixed"


def unified_diff(label: str, before: str, after: str) -> str:
    """A unified diff of one file's rewrite, for ``--dry-run`` output."""
    return "".join(
        difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile=f"a/{label}",
            tofile=f"b/{label}",
            n=2,
        )
    )


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


__all__ = ["apply_fixer", "unified_diff"]
