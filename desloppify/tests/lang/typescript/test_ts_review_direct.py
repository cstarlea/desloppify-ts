"""Direct tests for TypeScript review helpers."""

from __future__ import annotations

from desloppify.languages.typescript import review as ts_review


def test_typescript_review_guidance_has_expected_sections():
    assert "react" in ts_review.REVIEW_GUIDANCE
    assert "node" in ts_review.REVIEW_GUIDANCE
    assert "auth" in ts_review.REVIEW_GUIDANCE
    assert isinstance(ts_review.REVIEW_GUIDANCE["react"], list)
    assert isinstance(ts_review.REVIEW_GUIDANCE["node"], list)
    assert isinstance(ts_review.REVIEW_GUIDANCE["auth"], list)


def test_typescript_overrides_replace_python_flavoured_prompts():
    from desloppify.intelligence.review.dimensions.data import load_dimensions_for_lang

    _dims, prompts, _system = load_dimensions_for_lang("typescript")
    for dim in ("type_safety", "dependency_health", "test_strategy"):
        text = str(prompts[dim])
        for python_term in ("TypedDict", "Optional", "requirements.txt", "dict[str, Any]", "-> str"):
            assert python_term not in text, (dim, python_term)
    assert any("`as` casts" in item for item in prompts["type_safety"]["look_for"])


def test_scan_evidence_focus_points_at_toolchain_evidence():
    from desloppify.app.commands.review.prompt_sections import render_scan_evidence_focus

    text = render_scan_evidence_focus({"type_safety", "dependency_health", "test_strategy"})
    assert "abstractions.type_errors" in text
    assert "dependencies.manifest_issues" in text
    assert "testing.coverage" in text


def test_task_requirements_renumber_focus_lines_past_9j():
    from desloppify.app.commands.review.prompt_sections import render_task_requirements

    text = render_task_requirements(issues_cap=5, dim_set={"dependency_health", "test_strategy"})
    assert "12. For dependency_health" in text
    assert "13. For test_strategy" in text


def test_low_value_pattern_matches_dts_and_types_files():
    assert ts_review.LOW_VALUE_PATTERN.search("src/types.ts")
    assert ts_review.LOW_VALUE_PATTERN.search("src/api.d.ts")
    assert not ts_review.LOW_VALUE_PATTERN.search("src/feature/service.ts")


def test_module_patterns_detects_default_and_named_exports():
    content = "export default function A() {}\nexport const B = 1\n"
    patterns = ts_review.module_patterns(content)
    assert "default_export" in patterns
    assert "named_export" in patterns
