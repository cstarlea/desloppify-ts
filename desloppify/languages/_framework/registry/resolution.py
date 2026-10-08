"""Config instantiation and public language resolution helpers."""

from __future__ import annotations

from pathlib import Path

from . import state
from ..base.types import LangConfig
from ..contract_validation import validate_lang_contract
from .discovery import load_all, reset_runtime_state

_MARKER_GLOB_CHARS = ("*", "?", "[")


def _reset_dynamic_registries_for_refresh() -> None:
    """Reset mutable registries before refresh-driven discovery."""
    from desloppify.base.registry import reset_registered_detectors
    from desloppify.engine._scoring.policy.core import reset_registered_scoring_policies

    reset_registered_detectors()
    reset_registered_scoring_policies()
    reset_runtime_state()


def make_lang_config(name: str, cfg_cls: type) -> LangConfig:
    """Instantiate and validate a language config."""
    try:
        cfg = cfg_cls()
    except (TypeError, ValueError, AttributeError, RuntimeError, OSError) as ex:
        raise ValueError(
            f"Failed to instantiate language config '{name}': {ex}"
        ) from ex
    validate_lang_contract(name, cfg)
    return cfg


def get_lang(
    name: str,
    *,
    refresh_registry: bool = False,
) -> LangConfig:
    """Get a language config by name.

    All plugins (full and generic) store LangConfig instances in the registry.
    Test doubles that store plain classes are instantiated on demand as a fallback.
    """
    if refresh_registry:
        _reset_dynamic_registries_for_refresh()
        load_all(force_reload=True)
    elif not state.is_registered(name):
        load_all()
    if not state.is_registered(name):
        available = ", ".join(sorted(state.all_keys()))
        raise ValueError(f"Unknown language: {name!r}. Available: {available}")
    obj = state.get(name)
    if isinstance(obj, LangConfig):
        return obj
    return make_lang_config(name, obj)  # fallback for test doubles


def _detect_marker_exists(project_root: Path, marker: str) -> bool:
    marker_text = str(marker).strip()
    if not marker_text:
        return False

    # Fast path for literal markers.
    if (project_root / marker_text).exists():
        return True

    # Wildcard markers (for example "*.fsproj") are matched at project root.
    if any(ch in marker_text for ch in _MARKER_GLOB_CHARS):
        return any(project_root.glob(marker_text))
    return False


def available_langs(
    *,
    refresh_registry: bool = False,
) -> list[str]:
    """Return list of registered language names."""
    if refresh_registry:
        _reset_dynamic_registries_for_refresh()
    load_all(force_reload=refresh_registry)
    return sorted(state.all_keys())


__all__ = [
    "available_langs",
    "get_lang",
    "make_lang_config",
]
