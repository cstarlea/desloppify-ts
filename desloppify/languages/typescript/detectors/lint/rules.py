"""How much a lint finding counts: dropped, or a confidence from the rule and severity.

Formatting is the formatter's job, so layout rules aren't issues. Rules that
check what one of desloppify's own detectors checks are dropped too, so the
same problem isn't counted twice. Everything else starts from the rule's kind
(ESLint's ``meta.type``, or a curated table for the type-aware
typescript-eslint rules) and a ``warn`` severity lowers it one step.
"""

from __future__ import annotations

from typing import Any

# Rules that check what a desloppify detector already reports.
DUPLICATED_BY: dict[str, str] = {
    "no-unused-vars": "unused",
    "@typescript-eslint/no-unused-vars": "unused",
    "unused-imports/no-unused-imports": "unused",
    "unused-imports/no-unused-vars": "unused",
    "no-unused-private-class-members": "unused",
    "@typescript-eslint/no-explicit-any": "smells (any_type)",
    "@typescript-eslint/ban-ts-comment": "smells (ts_ignore, ts_nocheck, ts_expect_error_undocumented)",
    "@typescript-eslint/prefer-ts-expect-error": "smells (ts_ignore)",
    "@typescript-eslint/no-non-null-assertion": "smells (non_null_assert)",
    "no-empty": "smells (empty_catch, empty_if_chain)",
    "require-await": "smells (async_no_await)",
    "@typescript-eslint/require-await": "smells (async_no_await)",
    "@typescript-eslint/require-array-sort-compare": "smells (sort_no_comparator)",
    "no-magic-numbers": "smells (magic_number)",
    "@typescript-eslint/no-magic-numbers": "smells (magic_number)",
    "default-case": "smells (switch_no_default)",
    "no-warning-comments": "smells (todo_fixme)",
    "complexity": "smells (high_cyclomatic_complexity)",
    "max-lines-per-function": "smells (monster_function)",
    "max-lines": "structural",
    "no-eval": "security (eval_injection)",
    "no-new-func": "security (eval_injection)",
    "react/no-danger": "security (dangerously_set_inner_html)",
    # Biome
    "correctness/noUnusedVariables": "unused",
    "correctness/noUnusedImports": "unused",
    "correctness/noUnusedFunctionParameters": "unused",
    "correctness/noUnusedPrivateClassMembers": "unused",
    "suspicious/noExplicitAny": "smells (any_type)",
    "suspicious/noTsIgnore": "smells (ts_ignore)",
    "style/noNonNullAssertion": "smells (non_null_assert)",
    "suspicious/noEmptyBlockStatements": "smells (empty_catch, empty_if_chain)",
    "suspicious/useAwait": "smells (async_no_await)",
    "complexity/noExcessiveCognitiveComplexity": "smells (high_cyclomatic_complexity)",
    "complexity/noExcessiveLinesPerFunction": "smells (monster_function)",
    "security/noGlobalEval": "security (eval_injection)",
    "security/noDangerouslySetInnerHtml": "security (dangerously_set_inner_html)",
}

# Formatting plugins; ESLint's own formatting rules have ``meta.type == "layout"``.
_FORMATTING_PREFIXES = ("@stylistic/", "prettier/", "dprint/", "format/")

# Naming, ordering and file-name conventions: the project's taste, low weight.
_CONVENTION_PREFIXES = ("perfectionist/", "simple-import-sort/", "import-x/order", "import/order")
_CONVENTION_RULES = frozenset(
    {
        "camelcase",
        "id-length",
        "id-match",
        "func-style",
        "func-names",
        "arrow-body-style",
        "prefer-arrow-callback",
        "capitalized-comments",
        "sort-imports",
        "sort-keys",
        "one-var",
        "unicorn/filename-case",
        "unicorn/prevent-abbreviations",
        "react/jsx-sort-props",
        "@typescript-eslint/naming-convention",
        "@typescript-eslint/member-ordering",
        "@typescript-eslint/consistent-type-imports",
        "@typescript-eslint/consistent-type-exports",
    }
)

# typescript-eslint rules that need type information, by how much a finding
# says about a defect. Async and comparison bugs are high. The no-unsafe-*
# family flags `any` flowing through, which often comes from an untyped
# dependency or compiler options, so it is medium, like implicit any in
# type_error. Cleanups (redundant assertions, nullish/optional-chain rewrites)
# are low.
_TYPE_AWARE: dict[str, str] = {
    "@typescript-eslint/await-thenable": "high",
    "@typescript-eslint/no-array-delete": "high",
    "@typescript-eslint/no-base-to-string": "high",
    "@typescript-eslint/no-floating-promises": "high",
    "@typescript-eslint/no-for-in-array": "high",
    "@typescript-eslint/no-implied-eval": "high",
    "@typescript-eslint/no-misused-promises": "high",
    "@typescript-eslint/no-misused-spread": "high",
    "@typescript-eslint/only-throw-error": "high",
    "@typescript-eslint/prefer-promise-reject-errors": "high",
    "@typescript-eslint/restrict-plus-operands": "high",
    "@typescript-eslint/switch-exhaustiveness-check": "high",
    "@typescript-eslint/unbound-method": "high",
    "@typescript-eslint/no-deprecated": "medium",
    "@typescript-eslint/no-duplicate-type-constituents": "medium",
    "@typescript-eslint/no-redundant-type-constituents": "medium",
    "@typescript-eslint/no-unnecessary-condition": "medium",
    "@typescript-eslint/no-unsafe-argument": "medium",
    "@typescript-eslint/no-unsafe-assignment": "medium",
    "@typescript-eslint/no-unsafe-call": "medium",
    "@typescript-eslint/no-unsafe-enum-comparison": "medium",
    "@typescript-eslint/no-unsafe-member-access": "medium",
    "@typescript-eslint/no-unsafe-return": "medium",
    "@typescript-eslint/no-unsafe-type-assertion": "medium",
    "@typescript-eslint/no-unsafe-unary-minus": "medium",
    "@typescript-eslint/restrict-template-expressions": "medium",
    "@typescript-eslint/strict-boolean-expressions": "medium",
    "@typescript-eslint/use-unknown-in-catch-callback-variable": "medium",
    "@typescript-eslint/dot-notation": "low",
    "@typescript-eslint/no-confusing-void-expression": "low",
    "@typescript-eslint/no-meaningless-void-operator": "low",
    "@typescript-eslint/no-mixed-enums": "low",
    "@typescript-eslint/no-unnecessary-boolean-literal-compare": "low",
    "@typescript-eslint/no-unnecessary-qualifier": "low",
    "@typescript-eslint/no-unnecessary-template-expression": "low",
    "@typescript-eslint/no-unnecessary-type-arguments": "low",
    "@typescript-eslint/no-unnecessary-type-assertion": "low",
    "@typescript-eslint/no-unnecessary-type-conversion": "low",
    "@typescript-eslint/no-unnecessary-type-parameters": "low",
    "@typescript-eslint/non-nullable-type-assertion-style": "low",
    "@typescript-eslint/prefer-find": "low",
    "@typescript-eslint/prefer-includes": "low",
    "@typescript-eslint/prefer-nullish-coalescing": "low",
    "@typescript-eslint/prefer-optional-chain": "low",
    "@typescript-eslint/prefer-readonly": "low",
    "@typescript-eslint/prefer-readonly-parameter-types": "low",
    "@typescript-eslint/prefer-reduce-type-parameter": "low",
    "@typescript-eslint/prefer-regexp-exec": "low",
    "@typescript-eslint/prefer-return-this-type": "low",
    "@typescript-eslint/prefer-string-starts-ends-with": "low",
    "@typescript-eslint/promise-function-async": "low",
    "@typescript-eslint/return-await": "low",
}

_LOWER = {"high": "medium", "medium": "low", "low": "low"}


def is_formatting(rule: str, meta: dict[str, Any] | None) -> bool:
    return rule.startswith(_FORMATTING_PREFIXES) or bool(meta and meta.get("type") == "layout")


def _base_confidence(rule: str, meta: dict[str, Any] | None) -> str:
    if rule in _TYPE_AWARE:
        return _TYPE_AWARE[rule]
    if rule in _CONVENTION_RULES or rule.startswith(_CONVENTION_PREFIXES):
        return "low"
    docs = meta.get("docs") if isinstance(meta, dict) else None
    if isinstance(docs, dict) and docs.get("recommended") == "stylistic":
        return "low"
    kind = meta.get("type") if isinstance(meta, dict) else None
    if kind == "problem":
        return "high"
    return "medium"


def classify(rule: str, severity: str, meta: dict[str, Any] | None) -> str | None:
    """The finding's confidence, or None when it isn't an issue (formatting, duplicate)."""
    if rule in DUPLICATED_BY or is_formatting(rule, meta):
        return None
    confidence = _base_confidence(rule, meta)
    return _LOWER[confidence] if severity != "error" else confidence


__all__ = ["DUPLICATED_BY", "classify", "is_formatting"]
