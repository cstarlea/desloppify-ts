"""TypeScript-specific security detectors."""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from pathlib import Path
from types import SimpleNamespace

from desloppify.base.discovery.file_paths import resolve_path
from desloppify.base.discovery.sfc import read_code_text
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.base.signal_patterns import is_server_only_path
from desloppify.engine.policy.zones import FileZoneMap, Zone
from desloppify.languages._framework.frameworks.detection import framework_values
from desloppify.languages.typescript.detectors.contracts import DetectorResult
from desloppify.languages.typescript.detectors.security.backend import (
    backend_security_issues,
    make_app_context,
)
from desloppify.languages.typescript.detectors.security.entries import (
    _make_security_entry,
)
from desloppify.languages.typescript.detectors.security.file_checks import (
    _file_level_security_issues,
)
from desloppify.languages.typescript.detectors.security.line_checks import (
    _line_security_issues,
)
from desloppify.languages.typescript.detectors.security.patterns import public_secret_re
from desloppify.languages.typescript.syntax.scanner import SourceText

logger = logging.getLogger(__name__)


class _PublicEnvContext:
    """The client-exposed env prefixes per package (its frameworks' plus the
    configured ones), cached by the directory of the nearest package.json."""

    def __init__(self, settings: Mapping[str, object] | None) -> None:
        settings = dict(settings or {})
        configured = settings.get("public_env_prefixes")
        self._configured = tuple(str(p) for p in configured) if isinstance(configured, list) else ()
        self._lang = SimpleNamespace(runtime_setting=settings.get)
        self._by_package: dict[Path, re.Pattern[str] | None] = {}

    def pattern_for(self, filepath: str) -> re.Pattern[str] | None:
        start = Path(resolve_path(filepath)).parent
        package = next((d for d in (start, *start.parents) if (d / "package.json").is_file()), start)
        if package not in self._by_package:
            detected = framework_values(package, self._lang, "public_env_prefixes")
            self._by_package[package] = public_secret_re((*detected, *self._configured))
        return self._by_package[package]


def detect_ts_security(
    files: list[str],
    zone_map: FileZoneMap | None,
    settings: Mapping[str, object] | None = None,
) -> DetectorResult[dict]:
    """Detect TypeScript-specific security issues with explicit population semantics.

    ``settings`` are the language settings; ``auth_functions`` adds names that
    count as an auth check for server actions and route handlers, and
    ``public_env_prefixes`` adds client-exposed env prefixes to those of the
    detected frameworks.
    """
    entries: list[dict] = []
    scanned = 0
    app_context = make_app_context(settings)
    public_env = _PublicEnvContext(settings)

    for filepath in files:
        if zone_map is not None:
            zone = zone_map.get(filepath)
            if zone in (Zone.TEST, Zone.CONFIG, Zone.GENERATED, Zone.VENDOR):
                continue

        try:
            content = read_code_text(resolve_path(filepath), errors="replace")
        except OSError as exc:
            log_best_effort_failure(logger, f"read TypeScript security source {filepath}", exc)
            entries.append(
                _make_security_entry(
                    filepath,
                    1,
                    str(exc),
                    check_id="scan_read_error",
                    summary="Failed to read file during TypeScript security scan",
                    severity="low",
                    confidence="high",
                    remediation="Ensure file is readable and rerun security scan.",
                )
            )
            continue

        scanned += 1
        normalized_path = filepath.replace("\\", "/")
        is_server_only = is_server_only_path(normalized_path)
        source = SourceText(content, filepath)
        has_dev_guard = "import.meta.env.DEV" in content or "isDev" in content
        public_secret = public_env.pattern_for(filepath)

        # Only matches that start in code count: "allow eval (HMR)" in a doc
        # comment or `<div dangerouslySetInnerHTML>` in a string isn't a call.
        for line_num in range(1, len(source.lines) + 1):
            entries.extend(
                _line_security_issues(
                    filepath=filepath,
                    normalized_path=normalized_path,
                    source=source,
                    line_num=line_num,
                    is_server_only=is_server_only,
                    has_dev_guard=has_dev_guard,
                    public_secret=public_secret,
                )
            )

        entries.extend(
            _file_level_security_issues(
                filepath=filepath,
                normalized_path=normalized_path,
                source=source,
            )
        )
        entries.extend(
            backend_security_issues(
                filepath=filepath,
                normalized_path=normalized_path,
                lines=source.lines,
                app=app_context,
            )
        )

    return DetectorResult(entries=entries, population_kind="files", population_size=scanned)


__all__ = [
    "detect_ts_security",
]
