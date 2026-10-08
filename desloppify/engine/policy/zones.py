"""Zone classification system — deterministic file intent classification.

Classifies files into zones (production, test, config, generated, script, vendor)
based on path patterns. Zone metadata flows through issues, scoring, and the LLM.
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.engine.policy.zones_data import (
    CONFIG_SKIP_DETECTORS,
    SCRIPT_SKIP_DETECTORS,
    SKIP_ALL_DETECTORS,
    TEST_SKIP_DETECTORS,
)

logger = logging.getLogger(__name__)


class Zone(str, Enum):
    """File intent zone — determines scoring and detection policy."""

    PRODUCTION = "production"
    TEST = "test"
    CONFIG = "config"
    GENERATED = "generated"
    SCRIPT = "script"
    VENDOR = "vendor"


# Zones excluded from the health score
EXCLUDED_ZONES = {Zone.TEST, Zone.CONFIG, Zone.GENERATED, Zone.VENDOR}

# String values for quick lookup in scoring (issues store zone as string)
EXCLUDED_ZONE_VALUES = {z.value for z in EXCLUDED_ZONES}

# Shared review filtering sets (enum-backed to keep comparisons type-safe).
REVIEW_SELECTION_EXCLUDED_ZONES = frozenset({Zone.TEST, Zone.GENERATED, Zone.VENDOR})
REVIEW_COVERAGE_EXCLUDED_ZONES = frozenset(
    {Zone.TEST, Zone.GENERATED, Zone.VENDOR, Zone.CONFIG, Zone.SCRIPT}
)


def normalize_zone(zone: object) -> Zone | None:
    """Normalize enum/string/duck-typed zone objects to a ``Zone`` member."""
    if isinstance(zone, Zone):
        return zone
    raw = getattr(zone, "value", zone)
    if isinstance(raw, Zone):
        return raw
    if isinstance(raw, str):
        try:
            return Zone(raw)
        except ValueError:
            return None
    return None


def zone_in(zone: object, zones: frozenset[Zone] | set[Zone]) -> bool:
    """Return ``True`` when *zone* resolves to a member of *zones*."""
    normalized = normalize_zone(zone)
    return normalized in zones if normalized is not None else False


@dataclass
class ZoneRule:
    """A classification rule: zone + list of path patterns.

    Patterns are matched against relative file paths. First matching rule wins.
    Pattern types (auto-detected from shape):
      - "/dir/"   → substring match on full path (directory marker)
      - ".ext"    → basename ends-with (suffix/extension, e.g. ".d.ts", ".test.")
      - "prefix_" → basename starts-with (trailing underscore)
      - "name.py" → basename exact match (has extension, no /)
      - fallback  → substring on full path
    """

    zone: Zone
    patterns: list[str]


def _match_pattern(rel_path: str, pattern: str) -> bool:
    """Match a zone pattern against a relative file path.

    See ZoneRule docstring for pattern type conventions.
    """
    basename = os.path.basename(rel_path)

    # Directory pattern: "/dir/" → substring on padded path
    if pattern.startswith("/") and pattern.endswith("/"):
        return pattern in ("/" + rel_path + "/")

    # Suffix/extension pattern: starts with "." → contains on basename
    if pattern.startswith("."):
        return pattern in basename

    # Prefix pattern: ends with "_" → basename starts-with
    if pattern.endswith("_"):
        return basename.startswith(pattern)

    # Suffix pattern: starts with "_" → basename ends-with (_test.py, _pb2.py)
    if pattern.startswith("_"):
        return basename.endswith(pattern)

    # Exact basename: has a proper file extension (1-5 chars after last dot),
    # no "/" → exact basename match (config.py, setup.py, conftest.py)
    if "/" not in pattern and "." in pattern:
        ext = pattern.rsplit(".", 1)[-1]
        if ext and len(ext) <= 5 and ext.isalnum():
            return basename == pattern

    # Fallback: substring on full path (vite.config, tsconfig, eslint, etc.)
    return pattern in rel_path


# ── Common zone rules (shared across languages) ──────────

COMMON_ZONE_RULES = [
    ZoneRule(Zone.VENDOR, ["/vendor/", "/third_party/", "/vendored/"]),
    ZoneRule(Zone.GENERATED, ["/generated/", "/__generated__/"]),
    ZoneRule(Zone.TEST, ["/tests/", "/test/", "/fixtures/"]),
    # Example apps are runnable demos, untested by convention like scripts.
    ZoneRule(Zone.SCRIPT, ["/scripts/", "/bin/", "/examples/", "/example/"]),
]


_GENERATED_MARKER_RE = re.compile(
    r"@generated\b|\bauto-?generated\b|\bautomatically generated\b"
    r"|\bcode generated\b.*\bdo not edit\b",
    re.IGNORECASE,
)
_HEADER_BYTES = 4096
_COMMENT_PREFIXES = ("//", "/*", "*", "#")


def has_generated_header(text: str) -> bool:
    """Return whether the file's leading comment block marks it as generated.

    Only comments before the first line of code count (after an optional
    shebang), so a ``@generated`` mention in the body is ignored.
    """
    in_block = False
    for raw_line in text.lstrip("\ufeff").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        if in_block:
            in_block = "*/" not in line
        elif line.startswith("/*"):
            in_block = "*/" not in line[2:]
        elif not line.startswith(_COMMENT_PREFIXES):
            return False
        if _GENERATED_MARKER_RE.search(line):
            return True
    return False


def _file_has_generated_header(file_path: str) -> bool:
    try:
        with open(resolve_path(file_path), "rb") as handle:
            head = handle.read(_HEADER_BYTES)
    except OSError:
        return False
    return has_generated_header(head.decode("utf-8", errors="replace"))


def _classify_directory(
    rel_dir: str, rules: list[ZoneRule], file_zones: list[Zone]
) -> Zone:
    """Zone for a directory: its directory patterns, else its files' common zone."""
    for rule in rules:
        for pattern in rule.patterns:
            if (
                pattern.startswith("/")
                and pattern.endswith("/")
                and _match_pattern(rel_dir, pattern)
            ):
                return rule.zone
    zones = set(file_zones)
    if len(zones) == 1:
        return zones.pop()
    return Zone.PRODUCTION


def classify_file(
    rel_path: str, rules: list[ZoneRule], overrides: dict[str, str] | None = None
) -> Zone:
    """Classify a file by its relative path. Overrides take priority."""
    if overrides:
        override = overrides.get(rel_path)
        if override:
            try:
                return Zone(override)
            except ValueError as exc:
                log_best_effort_failure(
                    logger, f"parse zone override for {rel_path}", exc
                )
    for rule in rules:
        for pattern in rule.patterns:
            if _match_pattern(rel_path, pattern):
                return rule.zone
    return Zone.PRODUCTION


class FileZoneMap:
    """Cached zone classification for a set of files.

    Built once per scan from file list + zone rules.
    """

    def __init__(
        self,
        files: list[str],
        rules: list[ZoneRule],
        rel_fn: Callable[[str], str] | None = None,
        overrides: dict[str, str] | None = None,
    ):
        """Build a zone map from files, ordered rules, and optional overrides.

        A file whose leading comment marks it as generated (``@generated``,
        "auto-generated", "Code generated ... DO NOT EDIT") is in the generated zone unless an
        override or a vendor path says otherwise.
        """
        self._map: dict[str, Zone] = {}
        self._rel_map: dict[str, Zone] = {}
        self._rel_fn = rel_fn
        self._overrides = overrides
        self._rules = rules
        self._dir_files: dict[str, list[Zone]] = {}
        self._dir_zones: dict[str, Zone] = {}
        for file_path in files:
            rel_path = rel_fn(file_path) if rel_fn else file_path
            zone = classify_file(rel_path, rules, overrides)
            if (
                zone not in (Zone.GENERATED, Zone.VENDOR)
                and not (overrides and rel_path in overrides)
                and _file_has_generated_header(file_path)
            ):
                zone = Zone.GENERATED
            self._map[file_path] = zone
            self._rel_map[rel_path] = zone
            child, parent = rel_path, os.path.dirname(rel_path)
            while parent and parent != child:
                self._dir_files.setdefault(parent, []).append(zone)
                child, parent = parent, os.path.dirname(parent)

    def _directory_zone(self, rel_dir: str) -> Zone | None:
        """Zone for a directory holding scanned files (directory-level issues)."""
        rel_dir = rel_dir.rstrip("/")
        file_zones = self._dir_files.get(rel_dir)
        if file_zones is None:
            return None
        zone = self._dir_zones.get(rel_dir)
        if zone is None:
            zone = _classify_directory(rel_dir, self._rules, file_zones)
            self._dir_zones[rel_dir] = zone
        return zone

    def get(self, path: str) -> Zone:
        """Get zone for a file or directory path. PRODUCTION if not classified."""
        direct = self._map.get(path)
        if direct is not None:
            return direct

        rel_direct = self._rel_map.get(path)
        if rel_direct is not None:
            return rel_direct

        rel_path = path
        if self._rel_fn is not None:
            try:
                rel_path = self._rel_fn(path)
            except (OSError, TypeError, ValueError):
                rel_path = path
            rel_zone = self._rel_map.get(rel_path)
            if rel_zone is not None:
                return rel_zone

        dir_zone = self._directory_zone(rel_path) or self._directory_zone(path)
        if dir_zone is not None:
            return dir_zone

        return Zone.PRODUCTION

    def exclude(self, files: list[str], *zones: Zone) -> list[str]:
        """Return files NOT in the given zones."""
        zone_set = set(zones)
        return [
            file_path
            for file_path in files
            if self._map.get(file_path, Zone.PRODUCTION) not in zone_set
        ]

    def include_only(self, files: list[str], *zones: Zone) -> list[str]:
        """Return files that ARE in the given zones."""
        zone_set = set(zones)
        return [
            file_path
            for file_path in files
            if self._map.get(file_path, Zone.PRODUCTION) in zone_set
        ]

    def counts(self) -> dict[str, int]:
        """Return file count per zone."""
        counts: dict[str, int] = {}
        for zone in self._map.values():
            counts[zone.value] = counts.get(zone.value, 0) + 1
        return counts

    def production_count(self) -> int:
        """Count files classified as production."""
        return len(self._map) - self.non_production_count()

    def non_production_count(self) -> int:
        """Count files in excluded zones (test/config/generated/vendor)."""
        return sum(1 for z in self._map.values() if z in EXCLUDED_ZONES)

    def all_files(self) -> list[str]:
        """Return all classified file paths."""
        return list(self._map.keys())

    def items(self) -> list[tuple[str, Zone]]:
        """Return all (path, zone) pairs."""
        return list(self._map.items())


# ── Zone detection policies ────────────────────────────────


@dataclass
class ZonePolicy:
    """Per-zone detection policy.

    skip_detectors: detectors to skip entirely for this zone.
    downgrade_detectors: detectors whose confidence is downgraded to "low".
    exclude_from_score: whether issues in this zone are excluded from scoring.
    """

    skip_detectors: set[str] = field(default_factory=set)
    downgrade_detectors: set[str] = field(default_factory=set)
    exclude_from_score: bool = False


ZONE_POLICIES: dict[Zone, ZonePolicy] = {
    Zone.PRODUCTION: ZonePolicy(),
    Zone.TEST: ZonePolicy(
        skip_detectors=TEST_SKIP_DETECTORS,
        downgrade_detectors={"smells", "structural"},
        exclude_from_score=True,
    ),
    Zone.CONFIG: ZonePolicy(
        skip_detectors=CONFIG_SKIP_DETECTORS,
        exclude_from_score=True,
    ),
    Zone.GENERATED: ZonePolicy(
        skip_detectors=SKIP_ALL_DETECTORS,
        exclude_from_score=True,
    ),
    Zone.VENDOR: ZonePolicy(
        skip_detectors=SKIP_ALL_DETECTORS,
        exclude_from_score=True,
    ),
    Zone.SCRIPT: ZonePolicy(
        skip_detectors=SCRIPT_SKIP_DETECTORS,
        downgrade_detectors={"structural"},
    ),
}


# ── Helpers for phase runners ─────────────────────────────


def adjust_potential(zone_map, total: int) -> int:
    """Subtract non-production files from a potential count.

    Uses the zone map's own file list — no need to pass files separately.
    No-op if zone_map is None.
    """
    if zone_map is None:
        return total
    return max(total - zone_map.non_production_count(), 0)


def should_skip_detector_in_zone(zone: object, detector: object) -> bool:
    """Return whether a detector is excluded by an issue's stored zone.

    Queue consumers only have the zone stamped onto an issue, not the source
    ``FileZoneMap`` used during scanning.  Keep their policy decision aligned
    with phase-time filtering rather than treating every non-production zone
    as non-actionable.
    """
    normalized_zone = normalize_zone(zone)
    if normalized_zone is None or not isinstance(detector, str):
        return False
    policy = ZONE_POLICIES.get(normalized_zone)
    return policy is not None and detector in policy.skip_detectors


def should_skip_issue(zone_map, filepath: str, detector: str) -> bool:
    """Check whether a file's zone policy skips its detector."""
    if zone_map is None:
        return False
    return should_skip_detector_in_zone(zone_map.get(filepath), detector)


def filter_entries(
    zone_map, entries: list[dict], detector: str, file_key: str = "file"
) -> list[dict]:
    """Filter detector entries by zone policy. No-op if zone_map is None.

    If file_key points to a list (e.g. cycle entries with "files"), checks
    the first element.
    """
    if zone_map is None:
        return entries

    def _get_path(entry):
        val = entry[file_key]
        return val[0] if isinstance(val, list) else val

    return [
        e for e in entries if not should_skip_issue(zone_map, _get_path(e), detector)
    ]
