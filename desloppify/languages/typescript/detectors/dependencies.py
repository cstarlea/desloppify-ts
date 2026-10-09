"""Unused and unlisted dependencies and unlisted binaries, read from Knip.

The packages checked are the manifests (``package.json``) nearest the scanned
sources, inside the scan path. Knip's answer is filtered where it is known to
be unreliable:

* A manifest whose declared dependencies are not all installed is skipped for
  unused dependencies and binaries: Knip maps binaries to packages and loads
  plugin configs through ``node_modules``, so a missing install reads as unused.
* A dependency that a scanned source file still imports is not reported
  unused. Knip calls it unused because every importer is a file it considers
  unused; those files are the finding (``orphaned``), the dependency a cascade.
* A dependency whose name or binary a manifest script mentions, or that
  configures itself under its own manifest key (``"lint-staged": {...}``), is
  not reported unused: scripts run through a task runner Knip doesn't parse
  (``nub exec --node husky``) hide the binary from it. Monorepo scripts often
  use tools the root declares, so every manifest's scripts count.
* Binaries are only taken from manifest scripts. Ones Knip finds in source
  (``execa('openssl')``) are usually system commands.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import rel, resolve_path
from desloppify.base.discovery.source import find_ts_and_js_files
from desloppify.engine.policy.zones import Zone
from desloppify.languages._framework.base.types import DetectorCoverageStatus
from desloppify.languages.typescript.detectors.knip_adapter import (
    KnipRun,
    in_scan_path,
    run_knip,
)

_DECLARED_FIELDS = ("dependencies", "devDependencies")
_SPECIFIER_RE = re.compile(
    r"""(?:\bfrom\s*|\bimport\s*\(?\s*|\brequire\s*\(\s*|\bimport\s+)['"]([^'"\s]+)['"]"""
)
_SCRIPT_TOKEN_RE = re.compile(r"[^\s'\"`;&|()=<>]+")
_KNIP_REMEDIATION = {
    "knip_not_installed": "Install Knip in the project (npm i -D knip) and rerun scan.",
    "no_package_json": "Scan a directory inside a Node package (with package.json).",
}


@dataclass(frozen=True)
class DependencyResult:
    """``population_size`` is None when Knip didn't run (skipped)."""

    entries: list[dict]
    population_size: int | None
    coverage: DetectorCoverageStatus | None


def _reduced(summary: str, *, reason: str, confidence: float, remediation: str) -> DetectorCoverageStatus:
    return DetectorCoverageStatus(
        detector="dependencies",
        status="reduced",
        confidence=confidence,
        summary=summary,
        impact="Unused dependencies and unlisted binaries may be under-reported for this scan.",
        remediation=remediation,
        tool="knip",
        reason=reason,
    )


def _read_manifest(manifest: Path) -> dict:
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def declared_dependencies(manifest: Path) -> set[str]:
    data = _read_manifest(manifest)
    names: set[str] = set()
    for field in _DECLARED_FIELDS:
        value = data.get(field)
        if isinstance(value, dict):
            names.update(name for name in value if isinstance(name, str))
    return names


def _is_installed(name: str, start: Path) -> bool:
    for directory in (start, *start.parents):
        if (directory / "node_modules" / name).exists():
            return True
    return False


def _nearest_manifest(filepath: Path, stop: Path) -> Path | None:
    for directory in filepath.parents:
        if (directory / "package.json").is_file():
            return directory / "package.json"
        if directory == stop:
            break
    return None


def package_name(specifier: str) -> str | None:
    """``@scope/pkg/sub`` → ``@scope/pkg``; relative, absolute and builtins → None."""
    if specifier.startswith((".", "/", "node:", "#", "~")) or ":" in specifier:
        return None
    parts = specifier.split("/")
    if specifier.startswith("@"):
        return "/".join(parts[:2]) if len(parts) > 1 else None
    return parts[0] or None


def _types_target(name: str) -> str:
    """``@types/scope__pkg`` → ``@scope/pkg``; ``@types/pkg`` → ``pkg``."""
    bare = name.removeprefix("@types/")
    return "@" + bare.replace("__", "/", 1) if "__" in bare else bare


def _imported_packages(files: list[str]) -> dict[str, set[str]]:
    """Package name → the absolute files that import it, from import specifiers."""
    found: dict[str, set[str]] = {}
    for filepath in files:
        resolved = resolve_path(filepath)
        try:
            text = Path(resolved).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for specifier in _SPECIFIER_RE.findall(text):
            name = package_name(specifier)
            if name:
                found.setdefault(name, set()).add(str(Path(resolved).resolve()))
    return found


def _still_imported(name: str, manifest_dir: Path, imported: dict[str, set[str]]) -> bool:
    targets = {name, _types_target(name)} if name.startswith("@types/") else {name}
    return any(
        in_scan_path(Path(importer), manifest_dir)
        for target in targets
        for importer in imported.get(target, ())
    )


def _manifest_mentions(manifests: set[Path]) -> set[str]:
    """Words in the manifests' scripts, and their top-level keys."""
    mentions: set[str] = set()
    for manifest in manifests:
        data = _read_manifest(manifest)
        mentions.update(key for key in data if isinstance(key, str))
        scripts = data.get("scripts")
        if isinstance(scripts, dict):
            for command in scripts.values():
                if isinstance(command, str):
                    mentions.update(_SCRIPT_TOKEN_RE.findall(command))
    return mentions


def _installed_bins(name: str, start: Path) -> set[str]:
    for directory in (start, *start.parents):
        data = _read_manifest(directory / "node_modules" / name / "package.json")
        if data:
            bins = data.get("bin")
            if isinstance(bins, dict):
                return {key for key in bins if isinstance(key, str)}
            if isinstance(bins, str):
                return {name.rsplit("/", 1)[-1]}
            return set()
    return set()


def _mentioned(name: str, manifest_dir: Path, mentions: set[str]) -> bool:
    return name in mentions or bool(_installed_bins(name, manifest_dir) & mentions)


def _zone(zone_map: Any, filepath: Path) -> Zone:
    return zone_map.get(rel(str(filepath))) if zone_map is not None else Zone.PRODUCTION


def _unlisted_entries(
    run: KnipRun, path: Path, manifests: set[Path], zone_map: Any
) -> list[dict]:
    """One entry per (manifest, package) imported without being declared."""
    grouped: dict[tuple[Path, str], list[tuple[Path, int]]] = {}
    for filepath, item in run.items("unlisted"):
        if not (isinstance(item, dict) and item.get("name")) or not in_scan_path(filepath, path):
            continue
        if _zone(zone_map, filepath) in (Zone.GENERATED, Zone.VENDOR):
            continue
        manifest = _nearest_manifest(filepath, path.resolve())
        if manifest is None or manifest.resolve() not in manifests:
            continue
        line = item.get("line") if isinstance(item.get("line"), int) else 0
        grouped.setdefault((manifest.resolve(), item["name"]), []).append((filepath, line))
    entries = []
    for (manifest, name), importers in sorted(grouped.items()):
        importers.sort()
        runtime = any(
            _zone(zone_map, filepath) in (Zone.PRODUCTION, Zone.SCRIPT) for filepath, _ in importers
        )
        entries.append(
            {
                "file": rel(str(manifest)),
                "kind": "unlisted",
                "name": name,
                "line": 0,
                "confidence": "high" if runtime else "medium",
                "importers": [f"{rel(str(f))}:{line}" for f, line in importers],
            }
        )
    return entries


def detect_dependencies(
    path: Path, zone_map: Any = None, *, cache: dict[str, Any] | None = None
) -> DependencyResult:
    """Return the dependency entries Knip reports for the manifests in ``path``.

    Entry kinds: ``unused`` (``dependencies``), ``unused_dev``
    (``devDependencies``), ``unlisted`` (imported, not declared) and
    ``unlisted_binary`` (run by a script, not declared).
    """
    run = run_knip(path, cache=cache)
    if not run.usable:
        reason = run.failure or "knip_failed"
        return DependencyResult(
            [],
            None,
            _reduced(
                f"Dependency checks skipped: Knip did not run ({reason})",
                reason=reason,
                confidence=0.0,
                remediation=_KNIP_REMEDIATION.get(
                    reason, "Check that `npx knip` runs in this project and rerun scan."
                ),
            ),
        )

    scan_root = path.resolve()
    sources = find_ts_and_js_files(path)
    manifests: set[Path] = set()
    for filepath in sources:
        manifest = _nearest_manifest(Path(resolve_path(filepath)).resolve(), scan_root)
        if manifest is not None and in_scan_path(manifest.resolve(), path):
            manifests.add(manifest.resolve())

    declared = {manifest: declared_dependencies(manifest) for manifest in manifests}
    uninstalled = {
        manifest
        for manifest, names in declared.items()
        if any(not _is_installed(name, manifest.parent) for name in names)
    }
    imported = _imported_packages(sources)
    mentions = _manifest_mentions(manifests)

    entries: list[dict] = []
    for category, kind, confidence in (
        ("dependencies", "unused", "high"),
        ("devDependencies", "unused_dev", "medium"),
        ("binaries", "unlisted_binary", "medium"),
    ):
        for filepath, item in run.items(category):
            if not (isinstance(item, dict) and item.get("name")):
                continue
            if filepath not in manifests or filepath in uninstalled:
                continue
            name = item["name"]
            if kind != "unlisted_binary" and (
                _still_imported(name, filepath.parent, imported)
                or _mentioned(name, filepath.parent, mentions)
            ):
                continue
            line = item.get("line") if isinstance(item.get("line"), int) else 0
            entries.append(
                {
                    "file": rel(str(filepath)),
                    "kind": kind,
                    "name": name,
                    "line": line,
                    "confidence": confidence,
                }
            )
    unlisted = _unlisted_entries(run, path, manifests, zone_map)
    entries.extend(unlisted)

    population = sum(len(names) for m, names in declared.items() if m not in uninstalled)
    population += len(unlisted)
    coverage = None
    if uninstalled:
        listed = ", ".join(sorted(rel(str(m)) for m in uninstalled)[:3])
        more = f" (+{len(uninstalled) - 3} more)" if len(uninstalled) > 3 else ""
        coverage = _reduced(
            f"Unused dependencies and binaries not checked in {len(uninstalled)} of "
            f"{len(manifests)} packages whose dependencies are not installed: {listed}{more}",
            reason="dependencies_not_installed",
            confidence=round(1 - len(uninstalled) / max(len(manifests), 1), 2),
            remediation="Install the project's dependencies and rerun scan.",
        )
    return DependencyResult(entries, population, coverage)


__all__ = ["DependencyResult", "declared_dependencies", "detect_dependencies", "package_name"]
