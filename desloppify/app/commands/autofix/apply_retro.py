"""Retro/checklist and git-safety helpers for autofix apply flow."""

from __future__ import annotations

import shutil
import subprocess  # nosec B404
import sys
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from desloppify.base.output.terminal import colorize
from desloppify.languages.framework import FixResult

if TYPE_CHECKING:
    from desloppify.languages.framework import LangRun


def _resolve_fixer_results(
    state: dict, results: list[dict], entries: list[dict], fixer_name: str
) -> list[str]:
    """Mark issues fixed when every detected entry behind them was fixed.

    Fixers report the ``issue_id`` of each entry they fixed. Some issues
    group several entries (all ``[TAG]`` logs in a file, all matches of a
    smell in a file), so one skipped entry keeps its issue open.
    """
    work_items = state.get("work_items") or state.get("issues", {})
    state["work_items"] = work_items
    state["issues"] = work_items
    detected = Counter(e["issue_id"] for e in entries if e.get("issue_id"))
    fixed = Counter(
        issue_id for result in results for issue_id in result.get("fixed_issue_ids", [])
    )
    resolved_ids = []
    for issue_id, count in fixed.items():
        if count < detected.get(issue_id, count):
            continue
        issue = work_items.get(issue_id)
        if issue is None or issue.get("status") != "open":
            continue
        issue["status"] = "fixed"
        issue["note"] = f"auto-fixed by desloppify autofix {fixer_name}"
        resolved_ids.append(issue_id)
    return resolved_ids


def _warn_uncommitted_changes() -> None:
    try:
        git_path = shutil.which("git")
        if not git_path:
            return
        result = subprocess.run(
            [git_path, "status", "--porcelain"],
            capture_output=True,
            text=True,
            timeout=5,
        )  # nosec B603
        if result.stdout.strip():
            print(colorize("\n  ⚠ You have uncommitted changes. Consider running:", "yellow"))
            print(
                colorize(
                    "    git add -A && git commit -m 'pre-fix checkpoint' && git push",
                    "yellow",
                )
            )
            print(
                colorize(
                    "    This ensures you can revert if the fixer produces unexpected results.\n",
                    "dim",
                )
            )
    except (OSError, subprocess.TimeoutExpired):
        return


def _cascade_unused_import_cleanup(
    path: Path,
    state: dict,
    _prev_score: float,
    dry_run: bool,
    *,
    lang: LangRun | None = None,
) -> None:
    if not lang or "unused-imports" not in getattr(lang, "fixers", {}):
        print(colorize("  Cascade: no unused-imports fixer for this language", "dim"))
        return

    fixer = lang.fixers["unused-imports"]
    if fixer.unsafe:
        print(
            colorize(
                "  Cascade: skipped; the unused-imports fixer is marked unsafe. "
                "Remove now-unused imports by hand.",
                "dim",
            )
        )
        return
    print(colorize("\n  Running cascading import cleanup...", "dim"), file=sys.stderr)
    entries = fixer.detect(path)
    if not entries:
        print(colorize("  Cascade: no orphaned imports found", "dim"))
        return

    raw_results = fixer.fix(entries, dry_run=dry_run)
    results = raw_results.entries if isinstance(raw_results, FixResult) else raw_results

    if not results:
        print(colorize("  Cascade: no orphaned imports found", "dim"))
        return

    removed_count = sum(len(result["removed"]) if "removed" in result else 1 for result in results)
    removed_lines = sum(result.get("lines_removed", 0) for result in results)
    print(
        colorize(
            f"  Cascade: removed {removed_count} now-orphaned imports "
            f"from {len(results)} files ({removed_lines} lines)",
            "green",
        )
    )
    resolved = _resolve_fixer_results(state, results, entries, "cascade-unused-imports")
    if resolved:
        print(f"  Cascade: auto-resolved {len(resolved)} import issues")


_SKIP_REASON_LABELS = {
    "rest_element": "has ...rest (removing changes rest contents)",
    "array_destructuring": "array destructuring (positional — can't remove)",
    "function_param": "function/callback parameter (use `autofix unused-params` to prefix with _)",
    "side_effects": "initializer, default, condition or effect deps may have side effects",
    "would_empty_pattern": "every destructured name is unused, but the initializer may have side effects",
    "written_elsewhere": "name appears elsewhere in its scope (written later, or shadowed)",
    "loop_variable": "declared in a for-loop header",
    "type_parameter": "type parameter (removing it breaks explicit type arguments)",
    "asi_hazard": "removal would join neighbouring lines that have no semicolons",
    "not_a_parameter": "not a parameter (use `autofix unused-vars`)",
    "parameter_property": "constructor parameter property (the name is also a class field)",
    "used_in_signature": "named elsewhere in the signature (type predicate, asserts, typeof)",
    "name_taken": "the _-prefixed name is already used in that function",
    "not_standalone": "part of a larger expression or an unbraced if/loop/arrow body",
    "logger_wrapper": "log is the body of a logging helper",
    "not_empty": "an if branch or effect callback has statements or comments",
    "not_found": "not found at the reported position (stale scan?)",
    "needs_treesitter": "needs tree-sitter (install desloppify-ts[full])",
    "other": "other patterns (needs manual review)",
}


def _print_fix_retro(
    fixer_name: str,
    detected: int,
    fixed: int,
    resolved: int,
    skip_reasons: dict[str, int] | None = None,
):
    skipped = detected - fixed
    print(colorize("\n  ── Post-fix check ──", "dim"))
    print(
        colorize(
            f"  Fixed {fixed}/{detected} ({skipped} skipped, {resolved} issues resolved)",
            "dim",
        )
    )
    if skip_reasons and skipped > 0:
        print(colorize(f"\n  Skip reasons ({skipped} total):", "dim"))
        for reason, count in sorted(skip_reasons.items(), key=lambda item: -item[1]):
            print(
                colorize(
                    f"    {count:4d}  {_SKIP_REASON_LABELS.get(reason, reason)}", "dim"
                )
            )
        print()

    checklist = [
        "Run your language typecheck/build command — does it still build?",
        "Spot-check a few changed files — do the edits look correct?",
    ]
    if skipped > 0 and not skip_reasons:
        checklist.append(
            f"{skipped} items were skipped. Should the fixer handle more patterns?"
        )
    checklist += [
        "Run `desloppify scan` to update state and refresh issues.",
        "Are there cascading effects? (e.g., removing vars may orphan imports)",
        "`git diff --stat` — review before committing. Anything surprising?",
    ]
    print(colorize("  Checklist:", "dim"))
    for index, item in enumerate(checklist, 1):
        print(colorize(f"  {index}. {item}", "dim"))


__all__ = [
    "_cascade_unused_import_cleanup",
    "_print_fix_retro",
    "_resolve_fixer_results",
    "_warn_uncommitted_changes",
]
