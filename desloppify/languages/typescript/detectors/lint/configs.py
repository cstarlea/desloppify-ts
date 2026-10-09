"""Which linter a project configures, and where its local binary is."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from desloppify.base.discovery.source import DEFAULT_EXCLUSIONS

ESLINT_FLAT_CONFIGS = tuple(
    f"eslint.config.{ext}" for ext in ("js", "mjs", "cjs", "ts", "mts", "cts")
)
ESLINT_LEGACY_CONFIGS = (
    ".eslintrc.js",
    ".eslintrc.cjs",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    ".eslintrc.json",
    ".eslintrc",
)
_LABELS = {"eslint": "ESLint"}


@dataclass(frozen=True)
class LinterConfig:
    """A linter configured in ``path`` (a config file, or package.json)."""

    linter: str
    path: Path
    legacy: bool = False

    @property
    def label(self) -> str:
        return _LABELS[self.linter]

    @property
    def directory(self) -> Path:
        return self.path.parent


def _package_json(directory: Path) -> dict:
    try:
        data = json.loads((directory / "package.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def configs_in(directory: Path) -> list[LinterConfig]:
    """The flat ESLint config in one directory, if any.

    A flat config is a project boundary: ESLint 10 looks it up per file, and
    it ignores .eslintrc files and ``eslintConfig`` entirely.
    """
    flat = next((directory / name for name in ESLINT_FLAT_CONFIGS if (directory / name).is_file()), None)
    return [LinterConfig("eslint", flat)] if flat is not None else []


def _legacy_config(directory: Path) -> LinterConfig | None:
    legacy = next(
        (directory / name for name in ESLINT_LEGACY_CONFIGS if (directory / name).is_file()), None
    )
    if legacy is None and "eslintConfig" in _package_json(directory):
        legacy = directory / "package.json"
    return LinterConfig("eslint", legacy, legacy=True) if legacy is not None else None


def find_lint_configs(path: Path) -> list[LinterConfig]:
    """The configs in the nearest directory at or above ``path`` that has any.

    Legacy configs (.eslintrc, ``eslintConfig``) cascade into each other and
    are ignored once a flat config is in effect, so they count only when no
    flat config is above ``path``; then the nearest one is used.
    """
    current = path.resolve()
    if current.is_file():
        current = current.parent
    legacy: LinterConfig | None = None
    for directory in (current, *current.parents):
        configs = configs_in(directory)
        if configs:
            return configs
        legacy = legacy or _legacy_config(directory)
    return [legacy] if legacy is not None else []


def nested_config_dirs(scan_root: Path, config_dir: Path) -> list[Path]:
    """Directories under ``scan_root`` (other than ``config_dir``) with their own lint config."""
    found: list[Path] = []
    for dirpath, dirnames, _ in os.walk(scan_root):
        dirnames[:] = sorted(
            d for d in dirnames if d not in DEFAULT_EXCLUSIONS and not d.startswith(".")
        )
        directory = Path(dirpath)
        if directory != config_dir and configs_in(directory):
            found.append(directory)
            dirnames[:] = []
    return found


def find_local_bin(name: str, *start_dirs: Path) -> Path | None:
    """``node_modules/.bin/<name>`` at or above a start directory; never a global install."""
    names = (f"{name}.cmd", name) if os.name == "nt" else (name,)
    for start in start_dirs:
        for directory in (start, *start.parents):
            for candidate_name in names:
                candidate = directory / "node_modules" / ".bin" / candidate_name
                if candidate.is_file():
                    return candidate
    return None


__all__ = [
    "ESLINT_FLAT_CONFIGS",
    "ESLINT_LEGACY_CONFIGS",
    "LinterConfig",
    "configs_in",
    "find_local_bin",
    "find_lint_configs",
    "nested_config_dirs",
]
