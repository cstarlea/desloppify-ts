"""How strictly each TypeScript project is type-checked (``tsconfig_health``).

A project is the nearest ``tsconfig.json`` (see ``find_nearest_tsconfig``) of
at least one scanned TypeScript file that isn't a test, generated or vendored.
Options are read as tsc reads them, through ``extends`` and JSONC. A base
package that isn't installed counts when it is a well-known one
(``_KNOWN_BASES``); otherwise the option is unknown and never reported.

- ``strict``: off, unless TypeScript 6+ (where it defaults on) or the project
  turns on ``noImplicitAny`` and ``strictNullChecks`` itself. ``"strict":
  false`` has medium confidence: someone decided it.
- ``noUncheckedIndexedAccess``, ``noImplicitOverride``,
  ``verbatimModuleSyntax``: reported when strict is on and the option is
  never set. A value set anywhere, ``false`` included, is a decision and isn't
  reported. In a repo with several projects, only the root project and
  published packages are checked, not examples, docs sites or benchmarks. ``noImplicitOverride`` only counts in a project with a class that
  extends another, ``verbatimModuleSyntax`` only where the output is ES modules.
- ``drift``: between published packages, the options most of them turn on
  that this one leaves off, beyond what is already reported for it.

An option never set is reported once, on the outermost config in the repo
that the project extends (a monorepo's shared base, say), since setting it
there fixes every project that extends it. A value set is reported where it
is set.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.discovery.paths import get_project_root
from desloppify.base.discovery.source import find_ts_and_js_files, read_file_text
from desloppify.engine.policy.zones import FileZoneMap, Zone
from desloppify.languages._framework.node.js_text import code_text
from desloppify.languages.typescript.detectors.contracts import DetectorResult
from desloppify.languages.typescript.detectors.deps.resolve import (
    extends_chain,
    find_nearest_tsconfig,
    read_tsconfig,
    traced_compiler_option,
)
from desloppify.languages.typescript.syntax.queries import classes
from desloppify.languages.typescript.syntax.tree import parsed_file

_STRICTEST = {
    "strict": True,
    "noUncheckedIndexedAccess": True,
    "noImplicitOverride": True,
    "exactOptionalPropertyTypes": True,
    "noImplicitReturns": True,
    "noFallthroughCasesInSwitch": True,
    "noPropertyAccessFromIndexSignature": True,
}
# Options of well-known shared configs, for when the package isn't installed.
# ``@tsconfig/*`` bases all turn on strict.
_KNOWN_BASES: tuple[tuple[str, dict[str, Any]], ...] = (
    ("@tsconfig/strictest", _STRICTEST),
    ("@tsconfig/", {"strict": True}),
    (
        "@total-typescript/tsconfig",
        {"strict": True, "noUncheckedIndexedAccess": True, "noImplicitOverride": True, "verbatimModuleSyntax": True},
    ),
    (
        "@sindresorhus/tsconfig",
        {name: value for name, value in _STRICTEST.items() if name != "exactOptionalPropertyTypes"},
    ),
    ("@vue/tsconfig", {"strict": True, "verbatimModuleSyntax": True}),
)

# The options checked one by one when strict is on: (tier, what goes unchecked without it).
_EXTRAS: dict[str, tuple[int, str]] = {
    "noUncheckedIndexedAccess": (3, "`arr[i]` and `record[key]` are typed as never undefined"),
    "noImplicitOverride": (2, "a method that overrides a base class method needs no `override`"),
    "verbatimModuleSyntax": (2, "type-only imports can be emitted as runtime imports"),
}
# Options compared between packages.
_DRIFT_OPTIONS = (
    "strict",
    *_EXTRAS,
    "exactOptionalPropertyTypes",
    "noImplicitReturns",
    "noFallthroughCasesInSwitch",
    "noPropertyAccessFromIndexSignature",
)
_TS_SOURCE = (".ts", ".tsx", ".mts", ".cts")
_DECLARATIONS = (".d.ts", ".d.mts", ".d.cts")
_SKIPPED_ZONES = (Zone.TEST, Zone.GENERATED, Zone.VENDOR)
_CJS_MODULES = frozenset({"commonjs", "amd", "umd", "system", "none"})
_CLASS_EXTENDS = re.compile(r"\bclass\b[^{;]*?\bextends\b")


def _known_base(spec: str) -> dict[str, Any] | None:
    for prefix, options in _KNOWN_BASES:
        if spec.startswith(prefix):
            return options
    return None


@dataclass
class _Project:
    config: Path
    files: list[str] = field(default_factory=list)
    ts_major: int | None = None
    package: bool = False  # a published package (named, not private): compared for drift
    values: dict[str, tuple[Any, bool]] = field(default_factory=dict)
    reported: set[str] = field(default_factory=set)
    not_applicable: set[str] = field(default_factory=set)

    def option(self, name: str) -> tuple[Any, bool]:
        """The option's value and whether it is known (see ``traced_compiler_option``)."""
        if name not in self.values:
            self.values[name] = traced_compiler_option(self.config, name, _known_base)
        return self.values[name]

    def on(self, name: str) -> bool | None:
        """Whether ``name`` is on, None when unknown (``strict`` with its defaults)."""
        if name == "strict":
            return self.strict_on()
        value, known = self.option(name)
        return value is True if known else None

    def strict_on(self) -> bool | None:
        value, known = self.option("strict")
        if not known:
            return None
        if value is not None:
            return value is True
        if self.ts_major is not None and self.ts_major >= 6:
            return True
        return all(self.option(flag) == (True, True) for flag in ("noImplicitAny", "strictNullChecks"))


def detect_tsconfig_health(
    path: Path, zone_map: FileZoneMap | None = None
) -> DetectorResult[dict[str, Any]]:
    """Strictness issues for the TypeScript projects under ``path``; the
    population is the distinct checks run."""
    root = get_project_root().resolve()
    projects = _projects(path, zone_map, root)
    found: dict[tuple[Path, str], dict[str, Any]] = {}
    checked: set[tuple[Path, str]] = set()
    for project in projects:
        strict = project.strict_on()
        if strict is None:
            continue
        owner = _owner(project, "strict", root)
        checked.add((owner, "strict"))
        if not strict:
            project.reported.add("strict")
            found.setdefault((owner, "strict"), _strict_entry(project, owner))
            continue
        if len(projects) > 1 and not project.package and project.config.parent != root:
            continue  # an example, docs site or other private project in a monorepo
        for name, (tier, why) in _EXTRAS.items():
            if not _applicable(project, name):
                project.not_applicable.add(name)
                continue
            owner = _owner(project, name, root)
            checked.add((owner, name))
            value, known = project.option(name)
            if not known or value is not None:
                continue
            project.reported.add(name)
            found.setdefault(
                (owner, name),
                _entry(
                    owner,
                    project,
                    name,
                    tier=tier,
                    confidence="medium",
                    summary=f"tsconfig never sets {name}: {why}",
                    detail={"option": name},
                ),
            )
    entries = list(found.values())
    packages = [project for project in projects if project.package and project.strict_on()]
    if len(packages) > 1:
        for project in packages:
            checked.add((project.config, "drift"))
            entries.extend(_drift_entries(project, packages))
    return DetectorResult(entries=entries, population_kind="checks", population_size=len(checked))


def _projects(path: Path, zone_map: FileZoneMap | None, root: Path) -> list[_Project]:
    by_dir: dict[Path, Path | None] = {}
    projects: dict[Path, _Project] = {}
    for filepath in find_ts_and_js_files(path):
        if not filepath.endswith(_TS_SOURCE) or filepath.endswith(_DECLARATIONS):
            continue
        if zone_map is not None and zone_map.get(filepath) in _SKIPPED_ZONES:
            continue
        directory = Path(resolve_path(filepath)).parent
        if directory not in by_dir:
            config = find_nearest_tsconfig(directory)
            by_dir[directory] = config if config is not None and config.is_relative_to(root) else None
        config = by_dir[directory]
        if config is None:
            continue
        if config not in projects:
            projects[config] = _Project(
                config,
                ts_major=_typescript_major(config.parent, root),
                package=_published_package(config.parent, root),
            )
        projects[config].files.append(filepath)
    return [projects[config] for config in sorted(projects)]


def _owner(project: _Project, name: str, root: Path) -> Path:
    """The config to change: the one in the repo that sets ``name``, else the
    outermost config in the repo that ``project`` extends."""
    local: list[Path] = []
    for config in extends_chain(project.config):
        if not config.is_relative_to(root) or "node_modules" in config.parts:
            break
        local.append(config)
        options = (read_tsconfig(config) or {}).get("compilerOptions")
        if isinstance(options, dict) and name in options:
            return config
    return local[-1] if local else project.config


def _applicable(project: _Project, name: str) -> bool:
    if name == "noImplicitOverride":
        return any(_extends_a_class(filepath) for filepath in project.files)
    if name == "verbatimModuleSyntax":
        return _emits_es_modules(project)
    return True


def _extends_a_class(filepath: str) -> bool:
    content = read_file_text(resolve_path(filepath))
    if not content or "extends" not in content or "class" not in content:
        return False
    parsed = parsed_file(filepath)
    if parsed is not None:
        return any(info.extends for info in classes(parsed))
    return _CLASS_EXTENDS.search(code_text(content)) is not None


def _emits_es_modules(project: _Project) -> bool:
    """Whether the project's files are ES modules, where ``verbatimModuleSyntax`` applies."""
    if project.ts_major is not None and project.ts_major < 5:
        return False
    for legacy in ("importsNotUsedAsValues", "preserveValueImports"):
        if project.option(legacy)[0] is not None:
            return False
    module, known = project.option("module")
    if not known:
        return False
    if module is None:
        if project.ts_major is not None and project.ts_major >= 6:
            return True
        target, _known = project.option("target")
        return isinstance(target, str) and target.lower() not in ("es3", "es5")
    module = str(module).lower()
    if module in _CJS_MODULES:
        return False
    if module.startswith("node"):
        return _package_type(project.config.parent) == "module"
    return True


def _nearest_manifest(directory: Path, root: Path) -> dict[str, Any] | None:
    for current in (directory, *directory.parents):
        manifest = _read_json(current / "package.json")
        if manifest is not None or current == root:
            return manifest
    return None


def _published_package(directory: Path, root: Path) -> bool:
    manifest = _nearest_manifest(directory, root)
    return manifest is not None and isinstance(manifest.get("name"), str) and manifest.get("private") is not True


def _package_type(directory: Path) -> str | None:
    for current in (directory, *directory.parents):
        manifest = _read_json(current / "package.json")
        if manifest is not None:
            kind = manifest.get("type")
            return kind if isinstance(kind, str) else None
    return None


def _typescript_major(directory: Path, root: Path) -> int | None:
    """The TypeScript major version a project uses: installed, else declared."""
    for current in (directory, *directory.parents):
        installed = _read_json(current / "node_modules" / "typescript" / "package.json")
        if installed is not None:
            return _major(installed.get("version"))
        manifest = _read_json(current / "package.json") or {}
        for section in ("devDependencies", "dependencies", "peerDependencies"):
            deps = manifest.get(section)
            spec = deps.get("typescript") if isinstance(deps, dict) else None
            if isinstance(spec, str):
                if spec.startswith("catalog:"):
                    spec = _pnpm_catalog_spec(current, root, spec[len("catalog:") :] or "default")
                return _major(spec)
        if current == root:
            break
    return None


def _major(spec: object) -> int | None:
    match = re.search(r"\d+", spec) if isinstance(spec, str) else None
    return int(match.group()) if match else None


def _pnpm_catalog_spec(directory: Path, root: Path, catalog: str) -> str | None:
    """The ``typescript`` version a pnpm catalog gives (``pnpm-workspace.yaml``)."""
    for current in (directory, *directory.parents):
        workspace = current / "pnpm-workspace.yaml"
        if workspace.is_file():
            return _catalog_entry((read_file_text(str(workspace)) or "").splitlines(), catalog, "typescript")
        if current == root:
            break
    return None


def _catalog_entry(lines: list[str], catalog: str, package: str) -> str | None:
    """``package`` in the ``catalog:`` map (``default``) or in ``catalogs.<catalog>``."""
    paths = (["catalog"], ["catalogs", "default"]) if catalog == "default" else (["catalogs", catalog],)
    stack: list[tuple[int, str]] = []
    for line in lines:
        text = line.split("#", 1)[0].rstrip()
        if not text.strip():
            continue
        indent = len(text) - len(text.lstrip())
        key, _sep, value = text.strip().partition(":")
        key = key.strip().strip("'\"")
        while stack and stack[-1][0] >= indent:
            stack.pop()
        if key == package and [name for _indent, name in stack] in paths:
            return value.strip().strip("'\"") or None
        stack.append((indent, key))
    return None


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig", errors="replace"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _strict_entry(project: _Project, owner: Path) -> dict[str, Any]:
    value, _known = project.option("strict")
    if value is False:
        summary = 'tsconfig turns strict mode off ("strict": false)'
        confidence = "medium"
    else:
        summary = "tsconfig never turns on strict mode"
        confidence = "high"
    return _entry(
        owner,
        project,
        "strict",
        tier=3,
        confidence=confidence,
        summary=summary,
        detail={"option": "strict", "value": value},
    )


def _drift_entries(project: _Project, packages: list[_Project]) -> list[dict[str, Any]]:
    weaker = []
    for name in _DRIFT_OPTIONS:
        if name in project.reported or name in project.not_applicable or project.on(name) is not False:
            continue
        states = [other.on(name) for other in packages if name not in other.not_applicable]
        known = [state for state in states if state is not None]
        on = sum(1 for state in known if state)
        if on >= 2 and on * 2 > len(known):
            weaker.append(f"{name} ({on}/{len(known)})")
    if not weaker:
        return []
    return [
        _entry(
            project.config,
            project,
            "drift",
            tier=2,
            confidence="medium",
            summary=f"tsconfig leaves off what most of the {len(packages)} packages turn on: {', '.join(weaker)}",
            detail={"options": weaker, "packages": len(packages)},
        )
    ]


def _entry(owner: Path, project: _Project, check: str, **fields: Any) -> dict[str, Any]:
    detail = {"typescript": project.ts_major, **fields.pop("detail")}
    return {"file": str(owner), "check": check, "detail": detail, **fields}


__all__ = ["detect_tsconfig_health"]
