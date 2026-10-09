"""Supabase spec (Node ecosystem): detection, Edge Function entries, data client, RLS checks.

Detected from a Supabase client library or the CLI's ``supabase/config.toml``.
"""

from __future__ import annotations

from desloppify.engine._state.filtering import make_issue
from desloppify.languages._framework.node.frameworks.supabase import (
    edge_function_entries,
    scan_rls_disabled_in_public,
    scan_security_definer_views,
)

from ..types import DetectionConfig, EntryConventions, FrameworkSpec, ScannerRule
from . import SCRIPT_EXTENSIONS

_CONFIG_FILES = ("supabase/config.toml",)
_CLIENT_PACKAGES = (
    "@supabase/supabase-js",
    "@supabase/ssr",
    "@supabase/auth-helpers-nextjs",
    "@supabase/auth-helpers-react",
    "@supabase/auth-helpers-sveltekit",
    "@nuxtjs/supabase",
)

# Each Edge Function is served from supabase/functions/<name>/index.ts.
SUPABASE_ENTRY_CONVENTIONS = EntryConventions(
    config_files=_CONFIG_FILES,
    extensions=SCRIPT_EXTENSIONS,
    declared_entries=edge_function_entries,
)

SUPABASE_SPEC = FrameworkSpec(
    id="supabase",
    label="Supabase",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=_CLIENT_PACKAGES,
        dev_dependencies=(*_CLIENT_PACKAGES, "supabase"),
        config_files=_CONFIG_FILES,
    ),
    entry_conventions=SUPABASE_ENTRY_CONVENTIONS,
    data_clients=("supabase",),
    scanners=(
        ScannerRule(
            id="rls_disabled_in_public",
            scan=lambda root, _lang: scan_rls_disabled_in_public(root),
            issue_factory=lambda entry: make_issue(
                "supabase",
                entry["file"],
                f"rls_disabled_in_public::{entry['table']}",
                tier=2,
                confidence="medium",
                summary=(
                    f"Table {entry['table']} is served by the Data API but the migrations "
                    "never enable row level security, so the anon key can read and write it."
                ),
                detail={"line": entry["line"], "table": entry["table"]},
            ),
            log_message=lambda count: f"       supabase: {count} public tables without RLS",
        ),
        ScannerRule(
            id="security_definer_view",
            scan=lambda root, _lang: scan_security_definer_views(root),
            issue_factory=lambda entry: make_issue(
                "supabase",
                entry["file"],
                f"security_definer_view::{entry['view']}",
                tier=2,
                confidence="medium",
                summary=(
                    f"View {entry['view']} runs with its owner's rights, bypassing the RLS of "
                    "the tables it reads; create it WITH (security_invoker = true)."
                ),
                detail={"line": entry["line"], "view": entry["view"]},
            ),
            log_message=lambda count: (
                f"       supabase: {count} public views without security_invoker"
            ),
        ),
    ),
)


__all__ = ["SUPABASE_ENTRY_CONVENTIONS", "SUPABASE_SPEC"]
