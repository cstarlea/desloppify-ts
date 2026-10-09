"""NestJS framework spec (Node ecosystem)."""

from __future__ import annotations

import json
from pathlib import Path

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.nestjs import (
    scan_providers_missing_injectable,
    scan_unregistered_controllers,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule

_CONFIG_FILES = ("nest-cli.json", ".nest-cli.json", ".nestcli.json", "nest.json")
_EXTENSIONS = (".ts", ".js", ".mts", ".mjs")


def _nest_cli_entries(package_root: Path) -> frozenset[str]:
    """The entry file of each app nest-cli.json declares (``sourceRoot``/``entryFile``)."""
    for name in _CONFIG_FILES:
        try:
            payload = json.loads((package_root / name).read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, ValueError):
            continue
        if not isinstance(payload, dict):
            return frozenset()
        apps = [payload]
        projects = payload.get("projects")
        if isinstance(projects, dict):
            apps.extend(p for p in projects.values() if isinstance(p, dict))
        found: set[str] = set()
        for app in apps:
            source_root = app.get("sourceRoot") or "src"
            entry = app.get("entryFile") or "main"
            if not isinstance(source_root, str) or not isinstance(entry, str):
                continue
            base = (Path(source_root) / entry).as_posix().lstrip("./")
            found.update(
                f"{base}{ext}" for ext in _EXTENSIONS if (package_root / f"{base}{ext}").is_file()
            )
        return frozenset(found)
    return frozenset()


NESTJS_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_CONFIG_FILES,
    extensions=frozenset({".ts", ".js"}),
    marker_dependencies=("@nestjs/core",),
    declared_entries=_nest_cli_entries,
)

# Classes Nest's container instantiates from module metadata. One importer
# (the module that lists them) is how they are wired, not a sign to inline.
NESTJS_INJECTED_DECORATORS = frozenset(
    {
        "Module",
        "Global",
        "Injectable",
        "Controller",
        "Resolver",
        "Catch",
        "WebSocketGateway",
        "Scalar",
        "Plugin",
        "Processor",
        "EventsHandler",
        "CommandHandler",
        "QueryHandler",
        "Saga",
    }
)


NESTJS_SCANNERS: tuple[ScannerRule, ...] = (
    ScannerRule(
        id="unregistered_controller",
        scan=lambda root, _lang: scan_unregistered_controllers(root),
        issue_factory=lambda entry: make_issue(
            "nestjs",
            entry["file"],
            f"unregistered_controller::{entry['name']}",
            tier=2,
            confidence="medium",
            summary=(
                f"@Controller class {entry['name']} is in no module's controllers, "
                "so its routes are never mounted."
            ),
            detail={"line": entry["line"], "name": entry["name"]},
        ),
        log_message=lambda count: f"       nestjs: {count} controllers registered in no module",
    ),
    ScannerRule(
        id="provider_missing_injectable",
        scan=lambda root, _lang: scan_providers_missing_injectable(root),
        issue_factory=lambda entry: make_issue(
            "nestjs",
            entry["file"],
            f"provider_missing_injectable::{entry['name']}",
            tier=2,
            confidence="high",
            summary=(
                f"Provider {entry['name']} has constructor dependencies but no @Injectable(), "
                "so Nest can't resolve them."
            ),
            detail={"line": entry["line"], "name": entry["name"]},
        ),
        log_message=lambda count: (
            f"       nestjs: {count} providers with dependencies lack @Injectable()"
        ),
    ),
)


NESTJS_SPEC = FrameworkSpec(
    id="nestjs",
    label="NestJS",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("@nestjs/core",),
        config_files=_CONFIG_FILES,
        script_pattern=r"(?:^|\s)nest\s+(?:start|build)\b",
    ),
    scanners=NESTJS_SCANNERS,
    entry_conventions=NESTJS_ENTRY_CONVENTIONS,
    injected_class_decorators=NESTJS_INJECTED_DECORATORS,
)


__all__ = ["NESTJS_ENTRY_CONVENTIONS", "NESTJS_INJECTED_DECORATORS", "NESTJS_SPEC"]
