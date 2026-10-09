"""SvelteKit framework spec (Node ecosystem)."""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.sveltekit import (
    scan_load_global_fetch,
    scan_redirects_in_try,
    scan_server_imports_in_client,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule
from . import SCRIPT_EXTENSIONS, config_names

_SVELTE_CONFIG_FILES = config_names("svelte.config")

# Route files (+page.svelte, +layout.server.ts, +server.js, +page@.svelte)
# anywhere under routes/, hooks and the service worker in src/, and param
# matchers in src/params/. A plain Svelte + Vite app has a svelte.config
# too, so these apply only where @sveltejs/kit is a dependency.
SVELTEKIT_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_SVELTE_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS | {".svelte"},
    root_stems=frozenset(
        {"hooks", "hooks.server", "hooks.client", "service-worker", "instrumentation.server"}
    ),
    route_dir="routes",
    route_stem_prefix="+",
    entry_dirs=("src/params", "src/service-worker"),
    dependencies=("@sveltejs/kit",),
)

SVELTEKIT_SCANNERS: tuple[ScannerRule, ...] = (
    ScannerRule(
        id="server_import_in_client",
        scan=lambda root, _lang: scan_server_imports_in_client(root),
        issue_factory=lambda entry: make_issue(
            "sveltekit",
            entry["file"],
            f"server_import_in_client::{entry['module']}",
            tier=2,
            confidence="high",
            summary=(
                f"Browser-side module imports server-only {entry['module']}; "
                "SvelteKit won't bundle it for the client."
            ),
            detail={"line": entry["line"], "module": entry["module"]},
        ),
        log_message=lambda count: f"       sveltekit: {count} server-only imports in client modules",
    ),
    ScannerRule(
        id="load_global_fetch",
        scan=lambda root, _lang: scan_load_global_fetch(root),
        issue_factory=lambda entry: make_issue(
            "sveltekit",
            entry["file"],
            "load_global_fetch",
            tier=2,
            confidence="high",
            summary=(
                "Universal load calls the global fetch, so the request runs again in the "
                "browser on hydration; use the fetch passed to load."
                if entry["universal"]
                else "Server load calls the global fetch with a relative URL, which it can't "
                "resolve; use the fetch passed to load."
            ),
            detail={"line": entry["line"]},
        ),
        log_message=lambda count: f"       sveltekit: {count} load functions use the global fetch",
    ),
    ScannerRule(
        id="redirect_in_try",
        scan=lambda root, _lang: scan_redirects_in_try(root),
        issue_factory=lambda entry: make_issue(
            "sveltekit",
            entry["file"],
            f"redirect_in_try::{entry['line']}",
            tier=2,
            confidence="high",
            summary=(
                "redirect() throws, and this try's catch swallows it without rethrowing "
                "or checking isRedirect()."
            ),
            detail={"line": entry["line"]},
        ),
        log_message=lambda count: f"       sveltekit: {count} redirects caught by their own try",
    ),
)

SVELTEKIT_SPEC = FrameworkSpec(
    id="sveltekit",
    label="SvelteKit",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("@sveltejs/kit",),
        dev_dependencies=("@sveltejs/kit",),
        script_pattern=r"(?:^|\s)svelte-kit\s",
    ),
    scanners=SVELTEKIT_SCANNERS,
    entry_conventions=SVELTEKIT_ENTRY_CONVENTIONS,
    # $env/static/public and $env/dynamic/public.
    public_env_prefixes=("PUBLIC_",),
)


__all__ = ["SVELTEKIT_ENTRY_CONVENTIONS", "SVELTEKIT_SPEC"]
