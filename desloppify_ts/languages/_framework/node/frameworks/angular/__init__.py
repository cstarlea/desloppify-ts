"""Angular framework support shared across JS/TS scans."""

from __future__ import annotations

from .scanners import (
    angular_major,
    scan_missing_component_resources,
    scan_standalone_mismatches,
    workspace_entries,
)

__all__ = [
    "angular_major",
    "scan_missing_component_resources",
    "scan_standalone_mismatches",
    "workspace_entries",
]
