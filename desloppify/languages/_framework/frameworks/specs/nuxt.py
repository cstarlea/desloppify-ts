"""Nuxt framework spec (Node ecosystem)."""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.nuxt import (
    scan_data_composables_outside_setup,
    scan_legacy_process_flags,
    scan_private_runtime_config_in_client,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule
from . import SCRIPT_EXTENSIONS, config_names

_NUXT_CONFIG_FILES = config_names("nuxt.config")

# Directories Nuxt loads by convention or auto-imports from: nothing imports
# their files. Nuxt 4 keeps them in app/, Nuxt 3 at the root.
_APP_DIRS = ("components", "composables", "layouts", "middleware", "pages", "plugins", "utils")

NUXT_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_NUXT_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS | {".vue"},
    root_stems=frozenset({"app", "error", "app.config", "router.options"}),
    entry_dirs=(
        *_APP_DIRS,
        *(f"app/{name}" for name in _APP_DIRS),
        "server",  # Nitro: api/, routes/, middleware/, plugins/, auto-imported utils/
        "modules",
        "layers",
        "shared/utils",
        "shared/types",
    ),
)

NUXT_SCANNERS: tuple[ScannerRule, ...] = (
    ScannerRule(
        id="data_composable_outside_setup",
        scan=lambda root, _lang: scan_data_composables_outside_setup(root),
        issue_factory=lambda entry: make_issue(
            "nuxt",
            entry["file"],
            f"data_composable_outside_setup::{entry['line']}",
            tier=2,
            confidence="high",
            summary=(
                f"{entry['composable']}() runs after setup (an event handler, watcher or hook), "
                "where it can't register with the component or the SSR payload; use $fetch."
            ),
            detail={"line": entry["line"], "composable": entry["composable"]},
        ),
        log_message=lambda count: f"       nuxt: {count} data composables called after setup",
    ),
    ScannerRule(
        id="private_runtime_config_in_client",
        scan=lambda root, _lang: scan_private_runtime_config_in_client(root),
        issue_factory=lambda entry: make_issue(
            "nuxt",
            entry["file"],
            f"private_runtime_config_in_client::{entry['key']}",
            tier=2,
            confidence="high",
            summary=(
                f"Client-side code reads private runtimeConfig.{entry['key']}, which is "
                "undefined in the browser; only runtimeConfig.public reaches the client."
            ),
            detail={"line": entry["line"], "key": entry["key"]},
        ),
        log_message=lambda count: f"       nuxt: {count} private runtimeConfig keys read client-side",
    ),
    ScannerRule(
        id="legacy_process_flag",
        scan=lambda root, _lang: scan_legacy_process_flags(root),
        issue_factory=lambda entry: make_issue(
            "nuxt",
            entry["file"],
            "legacy_process_flag",
            tier=2,
            confidence="high",
            summary=(
                f"{entry['flag']} is deprecated since Nuxt 3; use "
                f"import.meta.{entry['flag'].split('.', 1)[1]}"
                + (f" ({entry['count']} uses in this file)." if entry["count"] > 1 else ".")
            ),
            detail={"line": entry["line"], "flag": entry["flag"], "count": entry["count"]},
        ),
        log_message=lambda count: f"       nuxt: {count} files use process.client/server/dev",
    ),
)

NUXT_SPEC = FrameworkSpec(
    id="nuxt",
    label="Nuxt",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("nuxt",),
        dev_dependencies=("nuxt",),
        config_files=_NUXT_CONFIG_FILES,
        script_pattern=r"(?:^|\s)nuxt(?:i)?\s",
    ),
    excludes=("vue",),
    scanners=NUXT_SCANNERS,
    entry_conventions=NUXT_ENTRY_CONVENTIONS,
    # runtimeConfig.public, overridable by NUXT_PUBLIC_* at runtime.
    public_env_prefixes=("NUXT_PUBLIC_",),
)


__all__ = ["NUXT_ENTRY_CONVENTIONS", "NUXT_SPEC"]
