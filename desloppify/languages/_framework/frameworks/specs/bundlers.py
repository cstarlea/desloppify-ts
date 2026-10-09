"""Specs that carry knowledge about a build tool or app platform but no
scanners. Vite and Expo inline env vars with their public prefix into client
code; Expo Router loads its routes by file name."""

from __future__ import annotations

from ..types import DetectionConfig, EntryConventions, FrameworkSpec
from . import SCRIPT_EXTENSIONS, config_names

# Expo Router serves every module under app/ (src/app/) as a route or layout.
EXPO_ROUTER_ENTRY_CONVENTIONS = EntryConventions(
    config_files=("app.json", *config_names("app.config")),
    extensions=SCRIPT_EXTENSIONS,
    entry_dirs=("app", "src/app"),
    dependencies=("expo-router",),
    marker_dependencies=("expo-router",),
)

VITE_SPEC = FrameworkSpec(
    id="vite",
    label="Vite",
    ecosystem="node",
    detection=DetectionConfig(
        dependencies=("vite",),
        dev_dependencies=("vite",),
        config_files=config_names("vite.config"),
    ),
    public_env_prefixes=("VITE_",),
)

EXPO_SPEC = FrameworkSpec(
    id="expo",
    label="Expo",
    ecosystem="node",
    detection=DetectionConfig(dependencies=("expo",)),
    entry_conventions=EXPO_ROUTER_ENTRY_CONVENTIONS,
    public_env_prefixes=("EXPO_PUBLIC_",),
)


__all__ = ["EXPO_ROUTER_ENTRY_CONVENTIONS", "EXPO_SPEC", "VITE_SPEC"]
