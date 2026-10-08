"""Public scoring policy facade."""

from __future__ import annotations

from desloppify.engine._scoring.policy.core import (
    DIMENSIONS,
    HOLISTIC_POTENTIAL,
    is_loc_weighted_dimension,
)

__all__ = [
    "DIMENSIONS",
    "HOLISTIC_POTENTIAL",
    "is_loc_weighted_dimension",
]
