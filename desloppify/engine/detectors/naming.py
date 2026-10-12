"""Naming consistency analysis: flag directories with mixed filename conventions."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from pathlib import Path

from desloppify.base.discovery.file_paths import rel

# A single lowercase word (``csv``, ``utils``) is also valid in these.
_FLAT_LOWER_COMPATIBLE = frozenset({"kebab-case", "snake_case", "camelCase"})


def _classify_convention(filename: str) -> str | None:
    """Classify a filename into a naming convention by its stem.

    Every extension is stripped: ``x.test.ts`` and ``x.d.ts`` both classify ``x``.
    """
    stem = filename.split(".", 1)[0]
    if not stem:
        return None

    if "-" in stem and stem == stem.lower():
        return "kebab-case"
    if stem[0].isupper() and "-" not in stem:
        return "PascalCase"
    if stem[0].islower() and any(c.isupper() for c in stem) and "-" not in stem:
        return "camelCase"
    if "_" in stem and stem == stem.lower():
        return "snake_case"
    if stem.islower() and "-" not in stem:
        return "flat_lower"
    return None


def detect_naming_inconsistencies(
    path: Path,
    file_finder,
    skip_names: set[str] | None = None,
    skip_dirs: set[str] | None = None,
    skip_file: Callable[[str], bool] | None = None,
) -> tuple[list[dict], int]:
    """Find directories where minority naming convention is significant.

    Thresholds: minority must have >= 5 files (absolute) and >= 15% of total (proportional).
    ``skip_file`` drops files whose names don't count (tests, framework-reserved).
    """
    all_skip_names = skip_names or set()
    all_skip_dirs = skip_dirs or set()
    files = file_finder(path)

    dir_files: dict[str, dict[str, list[str]]] = defaultdict(lambda: defaultdict(list))

    for filepath in files:
        p = Path(filepath)
        dirname = str(p.parent)
        rdir = rel(dirname)
        filename = p.name

        if filename in all_skip_names:
            continue
        if rdir in all_skip_dirs:
            continue
        if skip_file is not None and skip_file(filepath):
            continue

        convention = _classify_convention(filename)
        if convention:
            dir_files[dirname][convention].append(filename)

    entries = []
    for dirname, by_convention in dir_files.items():
        conventions = _merge_flat_lower(by_convention)
        if len(conventions) < 2:
            continue

        sorted_conventions = sorted(conventions.items(), key=lambda x: -len(x[1]))
        majority_name, majority_files = sorted_conventions[0]
        total = sum(len(fs) for fs in conventions.values())

        for conv_name, conv_files in sorted_conventions[1:]:
            if len(conv_files) < 5:
                continue
            if len(conv_files) / total < 0.15:
                continue

            entries.append(
                {
                    "directory": rel(dirname),
                    "majority": majority_name,
                    "majority_count": len(majority_files),
                    "minority": conv_name,
                    "minority_count": len(conv_files),
                    "total_files": total,
                    "outliers": sorted(conv_files[:10]),
                }
            )

    return sorted(entries, key=lambda e: -e["minority_count"]), len(dir_files)


def _merge_flat_lower(conventions: dict[str, list[str]]) -> dict[str, list[str]]:
    """Count single-word stems with the leading convention when they fit it."""
    flat = conventions.get("flat_lower")
    others = {
        name: files for name, files in conventions.items() if name != "flat_lower"
    }
    if not flat or not others:
        return conventions
    leading = max(others, key=lambda name: len(others[name]))
    if leading not in _FLAT_LOWER_COMPATIBLE:
        return conventions
    others[leading] = [*others[leading], *flat]
    return others


__all__ = ["detect_naming_inconsistencies"]
