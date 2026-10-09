"""The project's own linter (ESLint), run with its config and scored under Lint."""

from desloppify.languages.typescript.detectors.lint.detector import (
    DEFAULT_TYPE_AWARE_MAX_FILES,
    LintResult,
    detect_lint_result,
)

__all__ = ["DEFAULT_TYPE_AWARE_MAX_FILES", "LintResult", "detect_lint_result"]
