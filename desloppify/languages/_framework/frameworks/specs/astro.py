"""Astro framework spec (Node ecosystem)."""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.astro import (
    scan_astro_glob,
    scan_client_directives_on_astro_components,
    scan_server_env_in_client_scripts,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule
from . import SCRIPT_EXTENSIONS, config_names

_ASTRO_CONFIG_FILES = config_names("astro.config")

# Every file in src/pages/ is a page or an endpoint; middleware, content
# collections and actions are loaded by name.
ASTRO_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_ASTRO_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS | {".astro", ".vue", ".svelte"},
    root_stems=frozenset({"middleware", "content.config", "live.config"}),
    entry_dirs=("src/pages",),
    entry_paths=frozenset({"src/content/config", "src/middleware/index", "src/actions/index"}),
)

ASTRO_SCANNERS: tuple[ScannerRule, ...] = (
    ScannerRule(
        id="server_env_in_client_script",
        scan=lambda root, _lang: scan_server_env_in_client_scripts(root),
        issue_factory=lambda entry: make_issue(
            "astro",
            entry["file"],
            f"server_env_in_client_script::{entry['name']}",
            tier=2,
            confidence="high",
            summary=(
                f"Client <script> imports {entry['name']}, which only the server can import."
                if entry["name"].startswith("astro:")
                else f"Client <script> reads import.meta.env.{entry['name']}, which is undefined "
                "in the browser; only PUBLIC_ variables are bundled."
            ),
            detail={"line": entry["line"], "name": entry["name"]},
        ),
        log_message=lambda count: f"       astro: {count} server-only env reads in client scripts",
    ),
    ScannerRule(
        id="client_directive_on_astro_component",
        scan=lambda root, _lang: scan_client_directives_on_astro_components(root),
        issue_factory=lambda entry: make_issue(
            "astro",
            entry["file"],
            f"client_directive_on_astro_component::{entry['component']}",
            tier=2,
            confidence="high",
            summary=(
                f"{entry['directive']} on <{entry['component']}>, an Astro component: it never "
                "hydrates, so the directive does nothing."
            ),
            detail={"line": entry["line"], "component": entry["component"], "directive": entry["directive"]},
        ),
        log_message=lambda count: f"       astro: {count} client directives on Astro components",
    ),
    ScannerRule(
        id="deprecated_astro_glob",
        scan=lambda root, _lang: scan_astro_glob(root),
        issue_factory=lambda entry: make_issue(
            "astro",
            entry["file"],
            "deprecated_astro_glob",
            tier=2,
            confidence="high",
            summary=(
                "Astro.glob() is deprecated since Astro 5; use import.meta.glob() or a content collection"
                + (f" ({entry['count']} calls in this file)." if entry["count"] > 1 else ".")
            ),
            detail={"line": entry["line"], "count": entry["count"]},
        ),
        log_message=lambda count: f"       astro: {count} files call Astro.glob()",
    ),
)

ASTRO_SPEC = FrameworkSpec(
    id="astro",
    label="Astro",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("astro",),
        dev_dependencies=("astro",),
        config_files=_ASTRO_CONFIG_FILES,
        script_pattern=r"(?:^|\s)astro\s",
    ),
    scanners=ASTRO_SCANNERS,
    entry_conventions=ASTRO_ENTRY_CONVENTIONS,
    public_env_prefixes=("PUBLIC_",),
)


__all__ = ["ASTRO_ENTRY_CONVENTIONS", "ASTRO_SPEC"]
