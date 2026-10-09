"""Tests verifying the review/ package split — all imports work, no circular deps."""

from __future__ import annotations

import importlib

import pytest

from desloppify.intelligence import review


class TestReviewImports:
    """Verify all public names are importable from desloppify.intelligence.review."""

    def test_key_public_names(self):
        """Key public names are available."""
        key_names = [
            "ReviewContext",
            "build_review_context",
            "prepare_review",
            "import_review_issues",
            "select_files_for_review",
            "DIMENSIONS",
            "generate_remediation_plan",
            "build_investigation_batches",
        ]
        for name in key_names:
            assert hasattr(review, name), f"Missing key public name: {name}"
            obj = getattr(review, name)
            # Constants should be non-empty collections; callables should be callable
            if name.isupper() or name.startswith("DEFAULT_"):
                assert obj, f"Key constant {name} should be non-empty"
            else:
                assert callable(obj), f"Key name {name} should be callable"


class TestSubmoduleImports:
    """Each submodule can be imported independently."""

    @pytest.mark.parametrize(
        "module",
        [
            "desloppify.intelligence.review.dimensions.holistic",
            "desloppify.intelligence.review.dimensions.lang",
            "desloppify.intelligence.review.context",
            "desloppify.intelligence.review.selection",
            "desloppify.intelligence.review.prepare",
            "desloppify.intelligence.review.importing.per_file",
            "desloppify.intelligence.review.importing.holistic",
            "desloppify.intelligence.review.importing.payload",
            "desloppify.intelligence.review.remediation",
        ],
    )
    def test_submodule_importable(self, module):
        mod = importlib.import_module(module)
        assert mod is not None
