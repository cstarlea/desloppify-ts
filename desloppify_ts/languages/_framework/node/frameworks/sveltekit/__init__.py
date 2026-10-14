"""SvelteKit framework support shared across JS/TS scans."""

from __future__ import annotations

from .scanners import (
    scan_load_global_fetch,
    scan_redirects_in_try,
    scan_server_imports_in_client,
)

__all__ = [
    "scan_load_global_fetch",
    "scan_redirects_in_try",
    "scan_server_imports_in_client",
]
