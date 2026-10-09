"""DetectorPhase factory for external tools."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.base.types import DetectorPhase
from desloppify.languages._framework.tools.parsers import PARSERS
from desloppify.languages._framework.tools.runner import (
    ToolRunResult,
    run_tool_result,
)


def _record_tool_failure_coverage(
    lang: Any,
    *,
    detector: str,
    label: str,
    result: ToolRunResult,
) -> None:
    """Attach reduced-coverage metadata when external tool runs fail."""
    if result.status != "error":
        return

    record = {
        "detector": detector,
        "status": "reduced",
        "confidence": 0.0,
        "summary": f"{label} tooling unavailable ({result.error_kind or 'error'})",
        "impact": "Detector results may be under-reported for this scan.",
        "remediation": "Install/fix the tool command and rerun scan.",
        "tool": label,
        "reason": result.error_kind or "tool_error",
    }
    detector_coverage = getattr(lang, "detector_coverage", None)
    if isinstance(detector_coverage, dict):
        detector_coverage[detector] = dict(record)

    coverage_warnings = getattr(lang, "coverage_warnings", None)
    if isinstance(coverage_warnings, list):
        if not any(
            isinstance(entry, dict) and entry.get("detector") == detector
            for entry in coverage_warnings
        ):
            coverage_warnings.append(dict(record))


def make_tool_phase(
    label: str,
    cmd: str,
    fmt: str,
    smell_id: str,
    tier: int,
    *,
    confidence: str = "medium",
) -> DetectorPhase:
    """Create a DetectorPhase that runs an external tool and parses output."""
    parser = PARSERS[fmt]

    def run(path: Path, lang: Any) -> tuple[list[dict[str, Any]], dict[str, int]]:
        run_result = run_tool_result(cmd, path, parser)
        if run_result.status == "error":
            _record_tool_failure_coverage(
                lang,
                detector=smell_id,
                label=label,
                result=run_result,
            )
            return [], {}
        entries = list(run_result.entries)
        meta = run_result.meta if isinstance(run_result.meta, dict) else {}
        meta_potential = meta.get("potential")
        potential = meta_potential if isinstance(meta_potential, int) else 0

        if run_result.status == "empty":
            return [], ({smell_id: potential} if potential > 0 else {})

        if not entries:
            return [], ({smell_id: potential} if potential > 0 else {})
        issues = [
            make_issue(
                smell_id,
                entry["file"],
                str(entry.get("id") or f"{smell_id}::{entry['line']}"),
                tier=tier,
                confidence=str(entry.get("confidence") or confidence),
                summary=str(entry.get("summary") or entry["message"]),
                detail=entry.get("detail")
                if isinstance(entry.get("detail"), dict)
                else None,
            )
            for entry in entries
        ]
        return issues, {smell_id: potential if potential > 0 else len(entries)}

    return DetectorPhase(label, run)


__all__ = ["make_tool_phase"]
