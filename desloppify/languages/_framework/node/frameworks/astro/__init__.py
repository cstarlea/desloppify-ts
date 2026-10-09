"""Astro framework support shared across JS/TS scans."""

from __future__ import annotations

from .scanners import (
    scan_astro_glob,
    scan_client_directives_on_astro_components,
    scan_server_env_in_client_scripts,
)

__all__ = [
    "scan_astro_glob",
    "scan_client_directives_on_astro_components",
    "scan_server_env_in_client_scripts",
]
