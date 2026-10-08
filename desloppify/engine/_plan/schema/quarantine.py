"""Set aside malformed plan entries on load instead of discarding the plan.

The unit of quarantine is one entry of a plan collection: a ``queue_order``
ID, a ``skipped`` entry, a cluster, an override, a ``superseded`` entry, an
``execution_log`` or ``commit_log`` record, or a ``promoted_ids`` or
``uncommitted_issues`` ID. Each is independent, so one bad entry should not
cost the rest. A top-level
container of the wrong type (e.g. ``skipped`` holding a list) is quarantined
whole, since there is nothing inside it to keep.
"""

from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any, TypedDict

from desloppify.engine._plan.schema.normalize import CONTAINER_TYPES
from desloppify.engine._plan.skip_policy import VALID_SKIP_KINDS
from desloppify.engine._state.schema import utc_now

QUARANTINE_KEY = "quarantined_entries"


class QuarantinedPlanEntry(TypedDict):
    """One plan entry set aside on load, kept verbatim for inspection."""

    section: str  # the plan key the entry came from
    key: str | int | None  # dict key, list position, or None for a whole container
    reason: str
    quarantined_at: str
    item: Any


def _record(
    quarantine: list[QuarantinedPlanEntry],
    section: str,
    key: str | int | None,
    reason: str,
    item: object,
) -> None:
    quarantine.append(
        {
            "section": section,
            "key": key,
            "reason": reason,
            "quarantined_at": utc_now(),
            "item": copy.deepcopy(item),
        }
    )


def _type_name(value: object) -> str:
    return type(value).__name__


def _quarantine_wrong_containers(
    plan: dict[str, Any], quarantine: list[QuarantinedPlanEntry]
) -> None:
    for key, expected_type in CONTAINER_TYPES:
        value = plan.get(key)
        if value is None or isinstance(value, expected_type):
            continue
        expected = "a list" if expected_type is list else "an object"
        _record(quarantine, key, None, f"{key} is a {_type_name(value)}, not {expected}", value)
        plan[key] = expected_type()


def _id_problem(label: str) -> Callable[[int, object], str | None]:
    def problem_of(index: int, entry: object) -> str | None:
        if isinstance(entry, str):
            return None
        return f"{label} {index} is a {_type_name(entry)}, not an ID string"

    return problem_of


def _is_str(value: object) -> bool:
    return isinstance(value, str)


def _is_str_or_none(value: object) -> bool:
    return value is None or isinstance(value, str)


def _is_id_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _is_dict(value: object) -> bool:
    return isinstance(value, dict)


# (field, check, required) for each record-shaped entry.
_FieldRules = tuple[tuple[str, Callable[[object], bool], bool], ...]

_EXECUTION_LOG_FIELDS: _FieldRules = (
    ("timestamp", _is_str, True),
    ("action", _is_str, True),
    ("issue_ids", _is_id_list, False),
    ("cluster_name", _is_str_or_none, False),
    ("detail", _is_dict, False),
)
_COMMIT_FIELDS: _FieldRules = (
    ("sha", _is_str, True),
    ("issue_ids", _is_id_list, False),
    ("branch", _is_str_or_none, False),
    ("note", _is_str_or_none, False),
    ("recorded_at", _is_str, False),
    ("cluster_name", _is_str_or_none, False),
)
_SUPERSEDED_FIELDS: _FieldRules = (
    ("original_id", _is_str, True),
    ("candidates", _is_id_list, False),
    ("remapped_to", _is_str_or_none, False),
)


def _record_problem(label: str, entry: object, fields: _FieldRules) -> str | None:
    if not isinstance(entry, dict):
        return f"{label} is a {_type_name(entry)}, not an object"
    for field, check, required in fields:
        if field not in entry:
            if required:
                return f"{label} has no {field}"
            continue
        if not check(entry[field]):
            return f"{label} has malformed {field} {entry[field]!r}"
    return None


def _execution_log_problem(index: int, entry: object) -> str | None:
    return _record_problem(f"execution log entry {index}", entry, _EXECUTION_LOG_FIELDS)


def _commit_problem(index: int, entry: object) -> str | None:
    return _record_problem(f"commit record {index}", entry, _COMMIT_FIELDS)


def _quarantine_list_entries(
    plan: dict[str, Any],
    section: str,
    problem_of: Callable[[int, object], str | None],
    quarantine: list[QuarantinedPlanEntry],
) -> None:
    entries = plan.get(section)
    if not isinstance(entries, list):
        return
    kept: list[Any] = []
    for index, entry in enumerate(entries):
        reason = problem_of(index, entry)
        if reason is None:
            kept.append(entry)
            continue
        _record(quarantine, section, index, reason, entry)
    entries[:] = kept


def _cluster_problem(name: str, cluster: object) -> str | None:
    if not isinstance(cluster, dict):
        return f"cluster {name!r} is a {_type_name(cluster)}, not an object"
    cluster.setdefault("name", name)
    if not isinstance(cluster["name"], str):
        return f"cluster {name!r} has non-string name {cluster['name']!r}"
    issue_ids = cluster.get("issue_ids")
    if issue_ids is not None and not isinstance(issue_ids, list):
        return f"cluster {name!r} has non-list issue_ids {issue_ids!r}"
    steps = cluster.get("action_steps")
    if steps is not None and not isinstance(steps, list):
        return f"cluster {name!r} has non-list action_steps {steps!r}"
    for step in steps or []:
        # Legacy plain-string steps are upgraded to objects on load.
        if not isinstance(step, dict | str):
            return f"cluster {name!r} has malformed action step {step!r}"
    return None


def _skip_problem(issue_id: str, entry: object) -> str | None:
    if not isinstance(entry, dict):
        return f"skip entry {issue_id!r} is a {_type_name(entry)}, not an object"
    entry.setdefault("issue_id", issue_id)
    return None


def _superseded_problem(issue_id: str, entry: object) -> str | None:
    if isinstance(entry, dict):
        entry.setdefault("original_id", issue_id)
    return _record_problem(f"superseded entry {issue_id!r}", entry, _SUPERSEDED_FIELDS)


def _override_problem(issue_id: str, entry: object) -> str | None:
    if not isinstance(entry, dict):
        return f"override {issue_id!r} is a {_type_name(entry)}, not an object"
    return None


def _quarantine_dict_entries(
    plan: dict[str, Any],
    section: str,
    problem_of: Callable[[str, object], str | None],
    quarantine: list[QuarantinedPlanEntry],
) -> None:
    entries = plan.get(section)
    if not isinstance(entries, dict):
        return
    for key in list(entries):
        # Quarantine the entry as it was on disk, not half-normalized.
        original = copy.deepcopy(entries[key])
        reason = problem_of(key, entries[key])
        if reason is not None:
            entries.pop(key)
            _record(quarantine, section, key, reason, original)


def quarantine_malformed_entries(
    plan: dict[str, Any], quarantine: list[QuarantinedPlanEntry]
) -> None:
    """Move structurally broken entries out of a raw loaded plan.

    Runs before the schema upgrades, which assume these shapes.
    """
    _quarantine_wrong_containers(plan, quarantine)
    _quarantine_list_entries(plan, "queue_order", _id_problem("queue entry"), quarantine)
    _quarantine_dict_entries(plan, "skipped", _skip_problem, quarantine)
    _quarantine_dict_entries(plan, "clusters", _cluster_problem, quarantine)
    _quarantine_dict_entries(plan, "overrides", _override_problem, quarantine)
    _quarantine_dict_entries(plan, "superseded", _superseded_problem, quarantine)
    _quarantine_list_entries(plan, "promoted_ids", _id_problem("promoted ID"), quarantine)
    _quarantine_list_entries(
        plan, "uncommitted_issues", _id_problem("uncommitted ID"), quarantine
    )
    _quarantine_list_entries(plan, "execution_log", _execution_log_problem, quarantine)
    _quarantine_list_entries(plan, "commit_log", _commit_problem, quarantine)


def _invalid_skip_kind(issue_id: str, entry: object) -> str | None:
    kind = entry.get("kind") if isinstance(entry, dict) else None
    if kind in VALID_SKIP_KINDS:
        return None
    return f"skip entry {issue_id!r} has invalid kind {kind!r}"


def quarantine_invalid_entries(
    plan: dict[str, Any], quarantine: list[QuarantinedPlanEntry]
) -> None:
    """Move entries that break ``validate_plan`` out of an upgraded plan.

    Runs after the schema upgrades, which rename legacy skip kinds.
    """
    _quarantine_dict_entries(plan, "skipped", _invalid_skip_kind, quarantine)
    # An ID both queued and skipped: the skip is the explicit decision.
    skipped = plan["skipped"]
    queue_order = plan["queue_order"]
    kept: list[str] = []
    for issue_id in queue_order:
        if issue_id in skipped:
            _record(
                quarantine,
                "queue_order",
                issue_id,
                f"queue entry {issue_id!r} is also skipped",
                issue_id,
            )
            continue
        kept.append(issue_id)
    queue_order[:] = kept


def merge_quarantined_entries(
    plan: dict[str, Any], new_entries: list[QuarantinedPlanEntry]
) -> None:
    """Append ``new_entries`` to the plan's quarantine list.

    An older entry for the same keyed section entry is replaced.
    """
    if not new_entries:
        return
    new_keys = {
        (entry["section"], entry["key"])
        for entry in new_entries
        if isinstance(entry["key"], str)
    }
    existing = plan.get(QUARANTINE_KEY)
    kept = [
        entry
        for entry in (existing if isinstance(existing, list) else [])
        if not (
            isinstance(entry, dict)
            and (entry.get("section"), entry.get("key")) in new_keys
        )
    ]
    plan[QUARANTINE_KEY] = kept + new_entries


__all__ = [
    "QUARANTINE_KEY",
    "QuarantinedPlanEntry",
    "merge_quarantined_entries",
    "quarantine_invalid_entries",
    "quarantine_malformed_entries",
]
