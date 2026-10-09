"""React Router v7 (framework mode) / Remix framework spec (Node ecosystem)."""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.react_router import (
    declared_route_modules,
    scan_data_hooks_without_export,
    scan_remix_imports_in_react_router_v7,
    scan_route_config_missing_modules,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule

_CONFIG_FILES = (
    "react-router.config.js",
    "react-router.config.mjs",
    "react-router.config.ts",
    "remix.config.js",
    "remix.config.mjs",
    "remix.config.ts",
)

# The framework loads route modules and its entry files from the file system.
# Its own template ships a Vite config rather than a react-router.config
# file, so the scoped packages mark a package too.
REACT_ROUTER_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_CONFIG_FILES,
    extensions=frozenset({".ts", ".tsx", ".js", ".jsx"}),
    marker_dependencies=("@react-router/", "@remix-run/"),
    # Beside the routes directory (app/root.tsx); app/routes.ts is the route config.
    root_stems=frozenset(
        {
            "root",
            "routes",
            "entry.client",
            "entry.server",
            "entry.rsc",
            "entry.ssr",
            "entry.browser",
        }
    ),
    root_depth=3,
    # Everything under app/routes/ is a route module.
    entry_dir_names=frozenset({"routes"}),
    # Route modules app/routes.ts names from elsewhere in the app directory.
    declared_entries=declared_route_modules,
)


REACT_ROUTER_SCANNERS: tuple[ScannerRule, ...] = (
    ScannerRule(
        id="route_module_missing",
        scan=lambda root, _lang: scan_route_config_missing_modules(root),
        issue_factory=lambda entry: make_issue(
            "react_router",
            entry["file"],
            f"route_module_missing::{entry['module']}",
            tier=2,
            confidence="high",
            summary=f"Route config names a module that doesn't exist ({entry['module']}).",
            detail={"line": entry["line"], "module": entry["module"]},
        ),
        log_message=lambda count: (
            f"       react-router: {count} route config entries name missing modules"
        ),
    ),
    ScannerRule(
        id="remix_import_in_v7",
        scan=lambda root, _lang: scan_remix_imports_in_react_router_v7(root),
        issue_factory=lambda entry: make_issue(
            "react_router",
            entry["file"],
            f"remix_import_in_v7::{entry['module']}",
            tier=3,
            confidence="high",
            summary=(
                f"React Router v7 app imports {entry['module']}; "
                f"v7 replaced it with {entry['successor']}."
            ),
            detail={
                "line": entry["line"],
                "module": entry["module"],
                "successor": entry["successor"],
            },
        ),
        log_message=lambda count: (
            f"       react-router: {count} files import Remix packages in a v7 app"
        ),
    ),
    ScannerRule(
        id="data_hook_without_export",
        scan=lambda root, _lang: scan_data_hooks_without_export(root),
        issue_factory=lambda entry: make_issue(
            "react_router",
            entry["file"],
            f"data_hook_without_export::{entry['hook']}",
            tier=2,
            confidence="medium",
            summary=(
                f"Route module calls {entry['hook']}() but exports no "
                f"{' or '.join(entry['exports'])}, so the data is always undefined."
            ),
            detail={"line": entry["line"], "hook": entry["hook"]},
        ),
        log_message=lambda count: (
            f"       react-router: {count} route modules read data they never load"
        ),
    ),
)


REACT_ROUTER_SPEC = FrameworkSpec(
    id="react_router",
    label="React Router",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("@react-router/dev", "@remix-run/dev", "@remix-run/react"),
        dev_dependencies=("@react-router/dev", "@remix-run/dev"),
        config_files=_CONFIG_FILES,
        script_pattern=r"(?:^|\s)(?:react-router|remix)\s+(?:dev|build)\b",
    ),
    scanners=REACT_ROUTER_SCANNERS,
    entry_conventions=REACT_ROUTER_ENTRY_CONVENTIONS,
)


__all__ = ["REACT_ROUTER_ENTRY_CONVENTIONS", "REACT_ROUTER_SPEC"]
