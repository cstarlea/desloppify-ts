"""Adopt a per-language state file from the multi-language era as ``state.json``.

Before the TypeScript-only fork, state lived in ``.desloppify/state-<lang>.json``.
JavaScript projects used ``state-javascript.json`` and recorded ``javascript`` as
their language; the TypeScript plugin now scans those files, so their issues are
relabelled. Otherwise the scan merge treats them as another language's issues and
never auto-resolves them.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

from desloppify.base.discovery.file_paths import safe_write_text
from desloppify.base.output.fallbacks import log_best_effort_failure
from desloppify.engine._state.schema import json_default

logger = logging.getLogger(__name__)

LANG = "typescript"
# Checked in order: a TypeScript state wins over a JavaScript one.
LEGACY_STATE_FILES = ("state-typescript.json", "state-javascript.json")
_FOLDED_LANGS = frozenset({"javascript"})
_LANG_KEYED_SECTIONS = ("scan_coverage", "lang_capabilities")


def _relabel_issue_langs(issues: object) -> None:
    if not isinstance(issues, dict):
        return
    for issue in issues.values():
        if isinstance(issue, dict) and issue.get("lang") in _FOLDED_LANGS:
            issue["lang"] = LANG


def relabel_folded_langs(state: dict) -> None:
    """Rewrite languages folded into the TypeScript plugin as ``typescript``."""
    if state.get("lang") in _FOLDED_LANGS:
        state["lang"] = LANG
    for key in ("work_items", "issues"):
        _relabel_issue_langs(state.get(key))
    for key in _LANG_KEYED_SECTIONS:
        section = state.get(key)
        if not isinstance(section, dict) or LANG in section:
            continue
        for old in _FOLDED_LANGS & section.keys():
            section[LANG] = section.pop(old)
            break


def migrate_legacy_lang_state(state_dir: Path) -> Path | None:
    """Move a legacy ``state-<lang>.json`` to ``state.json`` if there is none yet.

    Returns the legacy file that was adopted, or None. Other languages' state
    files are left alone.
    """
    target = state_dir / "state.json"
    if target.exists():
        return None
    legacy = next(
        (state_dir / n for n in LEGACY_STATE_FILES if (state_dir / n).is_file()), None
    )
    if legacy is None:
        return None
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return None
        relabel_folded_langs(data)
        safe_write_text(target, json.dumps(data, indent=2, default=json_default) + "\n")
        legacy.unlink()
    except (OSError, ValueError) as exc:
        log_best_effort_failure(logger, f"migrate {legacy} to {target}", exc)
        return None
    return legacy


__all__ = ["LEGACY_STATE_FILES", "migrate_legacy_lang_state", "relabel_folded_langs"]
