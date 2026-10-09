"""TypeScript/React code smell detection orchestration."""

from __future__ import annotations

import logging
import re
from pathlib import Path

from desloppify.base.discovery.sfc import is_sfc, read_code_text, read_sfc
from desloppify.base.output.fallbacks import log_best_effort_failure
from .detector_flow import (
    _detect_async_no_await,
    _detect_empty_if_chains,
    _detect_error_no_throw,
    _detect_high_cyclomatic_complexity,
    _detect_monster_functions,
    _detect_nested_closures,
    _detect_stub_functions,
)
from .detector_safety import (
    _detect_catch_return_default,
    _detect_dead_useeffects,
    _detect_swallowed_errors,
    _detect_switch_no_default,
    _detect_window_globals,
)
from .detector_statements import _detect_statement_smells
from .detector_types import TYPE_SAFETY_SMELLS, _detect_type_safety
from .helpers import (
    _file_context,
    _regex_line_matches,
)
from .assets import (
    detect_non_ts_asset_smells,
)
from .catalog import (
    SEVERITY_ORDER,
    TS_SMELL_CHECKS,
)
from desloppify.languages.typescript.detectors.io import (
    iter_typescript_sources,
    resolve_typescript_source,
)

logger = logging.getLogger(__name__)

_MULTI_LINE_DETECTORS = (
    _detect_async_no_await,
    _detect_catch_return_default,
    _detect_dead_useeffects,
    _detect_empty_if_chains,
    _detect_error_no_throw,
    _detect_high_cyclomatic_complexity,
    _detect_monster_functions,
    _detect_nested_closures,
    _detect_statement_smells,
    _detect_stub_functions,
    _detect_swallowed_errors,
    _detect_switch_no_default,
    _detect_type_safety,
    _detect_window_globals,
)


def detect_smells(path: Path) -> tuple[list[dict], int]:
    """Detect TypeScript/React smell patterns across project sources."""
    checks = TS_SMELL_CHECKS
    smell_counts: dict[str, list[dict]] = {s["id"]: [] for s in checks}
    files = iter_typescript_sources(path)
    loc: dict[str, int] = {}

    for filepath in files:
        try:
            p = resolve_typescript_source(filepath)
            content = read_code_text(p)
        except (OSError, UnicodeDecodeError) as exc:
            log_best_effort_failure(logger, f"read TypeScript smell candidate {filepath}", exc)
            continue

        ctx = _file_context(filepath, content)
        component = read_sfc(p) if is_sfc(p) else None
        loc[filepath] = component.line_count if component is not None else len(ctx.lines)
        for check in checks:
            if check["pattern"] is None:
                continue
            for i, line in _regex_line_matches(ctx, check["pattern"], check.get("anchor", "code")):
                if check["id"] == "hardcoded_url" and re.match(
                    r"^(?:export\s+)?(?:const|let|var)\s+[A-Z_][A-Z0-9_]*\s*=",
                    line.strip(),
                ):
                    continue
                smell_counts[check["id"]].append(
                    {
                        "file": filepath,
                        "line": i + 1,
                        "content": line.strip()[:100],
                    }
                )

        for detector in _MULTI_LINE_DETECTORS:
            detector(ctx, smell_counts)

    non_ts_files = detect_non_ts_asset_smells(path, smell_counts)

    entries = []
    for check in checks:
        matches = smell_counts[check["id"]]
        if matches:
            entry = {
                "id": check["id"],
                "label": check["label"],
                "severity": check["severity"],
                "count": len(matches),
                "files": len(set(m["file"] for m in matches)),
                "matches": matches,
            }
            if check["id"] in TYPE_SAFETY_SMELLS:
                entry["loc"] = {m["file"]: loc[m["file"]] for m in matches if m["file"] in loc}
            entries.append(entry)
    entries.sort(key=lambda e: (SEVERITY_ORDER.get(e["severity"], 9), -e["count"]))
    return entries, len(files) + non_ts_files


__all__ = ["TS_SMELL_CHECKS", "detect_smells"]
