"""Language framework root contract (types only).

Top-level role:
- expose stable type contracts shared by language plugins.

Non-role (owned by explicit submodules, not this root):
- command composition: ``commands.*``
- runtime wiring and accessors: ``runtime_support.*``
- parser and tree-sitter infrastructure: ``treesitter.*``

Keep this module minimal so ``languages._framework`` is not a catch-all entrypoint.
"""

from __future__ import annotations

from .base.types import (
    BoundaryRule,
    DetectorPhase,
    FixerConfig,
    FixResult,
    LangConfig,
    LangValueSpec,
)

__all__ = [
    "BoundaryRule",
    "DetectorPhase",
    "FixerConfig",
    "FixResult",
    "LangConfig",
    "LangValueSpec",
]
