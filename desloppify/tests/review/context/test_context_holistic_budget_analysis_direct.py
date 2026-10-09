"""Direct coverage tests for holistic budget_analysis helpers."""

from __future__ import annotations

from desloppify.intelligence.review.context_holistic.budget import analysis as analysis_mod


def test_budget_analysis_exports_expected_symbols() -> None:
    assert set(analysis_mod.__all__) == {
        "_count_signature_params",
        "_extract_type_names",
        "_score_clamped",
    }


def test_budget_analysis_helpers_are_callable_directly() -> None:
    assert analysis_mod._count_signature_params("self, a, b, cls, this, c") == 3
    assert analysis_mod._extract_type_names("pkg.UserRepo<T>, Service:") == [
        "UserRepo",
        "Service",
    ]
    assert analysis_mod._score_clamped(101.4) == 100
