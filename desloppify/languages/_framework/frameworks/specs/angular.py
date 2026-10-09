"""Angular framework spec (Node ecosystem)."""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.angular import (
    scan_missing_component_resources,
    scan_standalone_mismatches,
    workspace_entries,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule

# The builders load what angular.json (or an Nx project.json) names; the
# test runners' setup file is <project>/src/test-setup.ts by convention, at
# any depth of a workspace.
ANGULAR_ENTRY_CONVENTIONS = EntryConventions(
    config_files=("angular.json",),
    extensions=frozenset({".ts", ".js", ".mjs", ".cjs"}),
    marker_dependencies=("@angular/core",),
    root_stems=frozenset({"test-setup"}),
    root_depth=99,
    declared_entries=workspace_entries,
)

# Classes Angular's compiler and injector wire from metadata. One importer
# (a route config or a module) is how they are used, not a sign to inline.
ANGULAR_INJECTED_DECORATORS = frozenset(
    {"Component", "Directive", "Pipe", "NgModule", "Injectable"}
)


ANGULAR_SCANNERS: tuple[ScannerRule, ...] = (
    ScannerRule(
        id="missing_component_resource",
        scan=lambda root, _lang: scan_missing_component_resources(root),
        issue_factory=lambda entry: make_issue(
            "angular",
            entry["file"],
            f"missing_component_resource::{entry['resource']}",
            tier=2,
            confidence="high",
            summary=f"@Component {entry['key']} names a file that doesn't exist ({entry['resource']}).",
            detail={"line": entry["line"], "resource": entry["resource"]},
        ),
        log_message=lambda count: (
            f"       angular: {count} component resources missing"
        ),
    ),
    ScannerRule(
        id="standalone_mismatch",
        scan=lambda root, _lang: scan_standalone_mismatches(root),
        issue_factory=lambda entry: make_issue(
            "angular",
            entry["file"],
            f"standalone_mismatch::{entry['key']}::{entry['name']}",
            tier=2,
            confidence="high",
            summary=(
                f"{entry['name']} is standalone but declared in an NgModule."
                if entry["standalone"]
                else f"{entry['name']} is not standalone but is listed in imports."
            ),
            detail={"line": entry["line"], "name": entry["name"]},
        ),
        log_message=lambda count: (
            f"       angular: {count} declarables used against their standalone flag"
        ),
    ),
)


ANGULAR_SPEC = FrameworkSpec(
    id="angular",
    label="Angular",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("@angular/core",),
        config_files=("angular.json",),
        script_pattern=r"(?:^|\s)ng\s+(?:serve|build|test)\b",
    ),
    scanners=ANGULAR_SCANNERS,
    entry_conventions=ANGULAR_ENTRY_CONVENTIONS,
    injected_class_decorators=ANGULAR_INJECTED_DECORATORS,
)


__all__ = ["ANGULAR_ENTRY_CONVENTIONS", "ANGULAR_INJECTED_DECORATORS", "ANGULAR_SPEC"]
