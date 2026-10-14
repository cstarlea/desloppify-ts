"""Signature variance detection — same function name, different signatures across modules.

It looks for one helper name that a package's modules each export with
different parameters (``formatPrice(cents)`` here, ``formatPrice(amount,
currency)`` there), an inconsistent API. A name alone isn't enough:
module-local functions, separate packages, and modules implementing a
common interface (strategy modules that each export ``createWriter``,
``writeRow``, ``finish``) are left out.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable

from desloppify_ts.engine.detectors.base import FunctionInfo

# Names that are legitimately polymorphic — skip them
_ALLOWLIST = {
    "__init__",
    "__repr__",
    "__str__",
    "__eq__",
    "__hash__",
    "__lt__",
    "__le__",
    "__gt__",
    "__ge__",
    "__len__",
    "__iter__",
    "__next__",
    "__enter__",
    "__exit__",
    "__call__",
    "__getattr__",
    "__setattr__",
    "__getitem__",
    "__setitem__",
    "__delitem__",
    "__contains__",
    "main",
    "setup",
    "teardown",
    "configure",
    "run",
    "handle",
    "setUp",
    "tearDown",
    "setUpClass",
    "tearDownClass",
    "get",
    "post",
    "put",
    "delete",
    "patch",  # HTTP methods
    "GET",
    "POST",
    "PUT",
    "PATCH",
    "DELETE",
    "HEAD",
    "OPTIONS",  # route handlers (Next.js, Remix, Hono)
    # PHP magic + framework-polymorphic methods
    "__construct",
    "__destruct",
    "__get",
    "__set",
    "__isset",
    "__unset",
    "__toString",
    "__invoke",
    "__clone",
    "__debugInfo",
    "__serialize",
    "__unserialize",
    "boot",
    "register",
    "render",
    "toArray",
    "rules",
    "authorize",
}


def _signature_group_keys(name: str) -> list[tuple[str, str]]:
    """Return grouping keys: exact name and selected naming-pattern buckets."""
    keys = [("name", name)]
    normalized = name.lstrip("_")
    if normalized.startswith("phase_") and len(normalized) > len("phase_"):
        keys.append(("pattern", "phase_*"))
    return keys


# Modules exporting this many names besides the grouped one in common are
# implementations of one interface, whose signatures vary by design.
_SHARED_INTERFACE_NAMES = 2


def _interface_files(
    files: set[str], names_by_file: dict[str, set[str]], name: str
) -> set[str]:
    """Files in a group that share an interface with another file of the group."""
    ordered = sorted(files)
    found: set[str] = set()
    for i, first in enumerate(ordered):
        for second in ordered[i + 1 :]:
            shared = (names_by_file[first] & names_by_file[second]) - {name}
            if len(shared) >= _SHARED_INTERFACE_NAMES:
                found |= {first, second}
    return found


def detect_signature_variance(
    functions: list[FunctionInfo],
    min_occurrences: int = 3,
    *,
    scope_of: Callable[[str], str] | None = None,
) -> tuple[list[dict], int]:
    """Find function names appearing 3+ times across files with different signatures.

    Returns (entries, total_functions_checked).

    ``scope_of`` maps a file to its package; names are grouped per package.

    Each entry represents a function name group where signatures differ:
    {
        "name": str,           # function name
        "occurrences": int,    # how many definitions
        "files": list[str],    # distinct files
        "variants": list[dict] # [{file, line, params, param_count}]
    }
    """
    # Group by exact function name and select naming-pattern groups.
    by_group: dict[tuple[str, str, str], list[FunctionInfo]] = defaultdict(list)
    names_by_file: dict[str, set[str]] = defaultdict(set)
    for fn in functions:
        normalized_name = fn.name.lstrip("_")
        is_phase_pattern = normalized_name.startswith("phase_")
        if (
            fn.name.startswith("_")
            and not fn.name.startswith("__")
            and not is_phase_pattern
        ):
            continue  # Skip private functions — expected to be independent
        if fn.name in _ALLOWLIST:
            continue
        if fn.name.startswith("test_"):
            continue  # Skip test functions
        if getattr(fn, "default_export", False) or getattr(fn, "object_member", False):
            continue
        if getattr(fn, "exported", None) is False:
            continue  # Module-local: its name can't clash with another module's
        file = getattr(fn, "file", "")
        scope = scope_of(file) if scope_of is not None else ""
        names_by_file[file].add(fn.name)
        for group_type, group_name in _signature_group_keys(fn.name):
            by_group[(group_type, group_name, scope)].append(fn)

    entries = []
    for (group_type, group_name, _scope), fns in by_group.items():
        # Need at least min_occurrences across different files
        distinct_files = set(getattr(f, "file", "") for f in fns)
        if len(distinct_files) < min_occurrences:
            continue
        if group_type == "name":
            interface = _interface_files(distinct_files, names_by_file, group_name)
            if interface:
                fns = [f for f in fns if getattr(f, "file", "") not in interface]
                distinct_files -= interface
                if len(distinct_files) < min_occurrences:
                    continue

        # Compare parameter and return signatures (ignore self/cls params).
        variants = []
        for fn in fns:
            params = [p for p in fn.params if p not in ("self", "cls")]
            variants.append(
                {
                    "file": getattr(fn, "file", ""),
                    "line": fn.line,
                    "params": params,
                    "param_count": len(params),
                    "return_annotation": getattr(fn, "return_annotation", None),
                }
            )

        param_signatures = set()
        for v in variants:
            param_signatures.add(tuple(v["params"]))
        return_signatures = {
            str(v["return_annotation"]).strip()
            for v in variants
            if v.get("return_annotation")
        }
        has_param_variance = len(param_signatures) >= 2
        has_return_variance = len(return_signatures) >= 2

        # All identical or not enough annotated return variants.
        if not has_param_variance and not has_return_variance:
            continue

        entries.append(
            {
                "name": group_name,
                "group_type": group_type,
                "occurrences": len(fns),
                "files": sorted(distinct_files),
                "file_count": len(distinct_files),
                "variants": variants,
                "signature_count": len(param_signatures),
                "return_signature_count": len(return_signatures),
                "has_param_variance": has_param_variance,
                "has_return_variance": has_return_variance,
            }
        )

    entries.sort(
        key=lambda e: (
            -max(e["signature_count"], e["return_signature_count"]),
            -e["occurrences"],
            e["name"],
        )
    )
    return entries, len(functions)
