"""Direct tests for review dimension guidance helpers."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

import desloppify.intelligence.review.dimensions.data as dimensions_data_mod
import desloppify.intelligence.review.dimensions.lang as dimensions_mod
import desloppify.intelligence.review.dimensions.selection as dimensions_selection_mod
import desloppify.intelligence.review.dimensions.validation as dimensions_validation_mod
from desloppify.base.text_utils import is_numeric
from desloppify.intelligence.review.dimensions.holistic import DIMENSIONS


def test_collect_holistic_dims_by_lang_keys_typescript(monkeypatch):
    monkeypatch.setattr(
        dimensions_mod,
        "get_lang",
        lambda _name: SimpleNamespace(holistic_review_dimensions=["logic_clarity"]),
    )
    assert dimensions_mod._collect_holistic_dims_by_lang() == {
        "typescript": ["logic_clarity"]
    }

    monkeypatch.setattr(
        dimensions_mod,
        "get_lang",
        lambda _name: SimpleNamespace(holistic_review_dimensions=[]),
    )
    assert dimensions_mod._collect_holistic_dims_by_lang() == {}


def test_collect_lang_guidance_and_get_lang_guidance(monkeypatch):
    monkeypatch.setattr(
        dimensions_mod,
        "get_lang",
        lambda _name: SimpleNamespace(review_guidance={"patterns": ["check x"]}),
    )
    assert dimensions_mod._collect_lang_guidance() == {
        "typescript": {"patterns": ["check x"]}
    }

    monkeypatch.setattr(
        dimensions_mod, "LANG_GUIDANCE", {"typescript": {"patterns": ["check x"]}}
    )
    assert dimensions_mod.get_lang_guidance("typescript") == {"patterns": ["check x"]}
    assert dimensions_mod.get_lang_guidance("python") == {}


def test_real_typescript_guidance_is_loaded():
    assert dimensions_mod.HOLISTIC_DIMENSIONS_BY_LANG["typescript"]
    assert dimensions_mod.get_lang_guidance("typescript")


def test_dimensions_schema_validation_rejects_bad_prompt_shape(monkeypatch):
    def _bad_payload(_filename: str) -> dict:
        return {
            "default_dimensions": ["naming_quality"],
            "dimension_prompts": {
                "naming_quality": {
                    "description": "names",
                    "look_for": "not-a-list",
                    "skip": ["ok"],
                },
            },
            "system_prompt": "x" * 120,
        }

    monkeypatch.setattr(dimensions_data_mod, "_load_json_payload", _bad_payload)
    dimensions_data_mod.load_dimensions.cache_clear()
    try:
        with pytest.raises(ValueError, match="look_for"):
            dimensions_data_mod.load_dimensions()
    finally:
        dimensions_data_mod.load_dimensions.cache_clear()


def test_dimensions_validation_requires_prompt_for_each_default_dimension(monkeypatch):
    def _bad_payload(_filename: str) -> dict:
        return {
            "default_dimensions": ["cross_module_architecture", "error_consistency"],
            "dimension_prompts": {
                "cross_module_architecture": {
                    "description": "arch",
                    "look_for": ["a"],
                    "skip": ["b"],
                },
            },
            "system_prompt": "x" * 120,
        }

    monkeypatch.setattr(dimensions_data_mod, "_load_json_payload", _bad_payload)
    dimensions_data_mod.load_dimensions.cache_clear()
    try:
        with pytest.raises(ValueError, match="missing prompts"):
            dimensions_data_mod.load_dimensions()
    finally:
        dimensions_data_mod.load_dimensions.cache_clear()


def test_resolve_dimensions_per_file_precedence():
    resolved = dimensions_selection_mod.resolve_dimensions(
        cli_dimensions=["logic_clarity"],
        config_dimensions=["naming_quality"],
    )
    assert resolved == ["logic_clarity"]

    resolved = dimensions_selection_mod.resolve_dimensions(
        cli_dimensions=None,
        config_dimensions=["naming_quality"],
    )
    assert resolved == ["naming_quality"]

    resolved = dimensions_selection_mod.resolve_dimensions(
        cli_dimensions=None,
        config_dimensions=None,
    )
    assert resolved == DIMENSIONS


def test_resolve_dimensions_holistic_precedence(monkeypatch):
    monkeypatch.setattr(
        dimensions_selection_mod,
        "HOLISTIC_DIMENSIONS_BY_LANG",
        {"python": ["dependency_health"]},
    )
    monkeypatch.setattr(
        dimensions_selection_mod,
        "DIMENSIONS",
        ["cross_module_architecture"],
    )

    resolved = dimensions_selection_mod.resolve_dimensions(
        lang_name="python",
        cli_dimensions=["test_strategy"],
    )
    assert resolved == ["test_strategy"]

    resolved = dimensions_selection_mod.resolve_dimensions(
        lang_name="python",
        cli_dimensions=None,
    )
    assert resolved == ["dependency_health"]

    resolved = dimensions_selection_mod.resolve_dimensions(
        lang_name="go",
        cli_dimensions=None,
    )
    assert resolved == ["cross_module_architecture"]


def test_resolve_dimensions_holistic_default_is_scorecard_complete():
    """Without lang_name, default_dimensions (full scorecard) is used."""
    full_scorecard = ["dim_a", "dim_b", "dim_c"]
    resolved = dimensions_selection_mod.resolve_dimensions(
        cli_dimensions=None,
        default_dimensions=full_scorecard,
    )
    assert resolved == full_scorecard


def test_load_dimensions_for_lang_override_replaces_dimension_list(
    tmp_path, monkeypatch
):
    shared_dir = tmp_path / "review_data"
    lang_dir = tmp_path / "lang_data"
    shared_dir.mkdir(parents=True)
    (lang_dir / "python" / "review_data").mkdir(parents=True)

    shared_payload = {
        "default_dimensions": ["shared_dim"],
        "dimension_prompts": {
            "shared_dim": {
                "description": "shared desc",
                "look_for": ["x"],
                "skip": ["y"],
            }
        },
        "system_prompt": "s" * 120,
    }
    # Override fully replaces the dimension list and removes the shared prompt entry.
    override_payload = {
        "default_dimensions": ["lang_dim"],
        "dimension_prompts": {
            "lang_dim": {
                "description": "lang desc",
                "look_for": ["a"],
                "skip": ["b"],
            }
        },
        "dimension_prompts_remove": ["shared_dim"],
    }
    (shared_dir / "dimensions.json").write_text(json.dumps(shared_payload))
    (lang_dir / "python" / "review_data" / "dimensions.override.json").write_text(
        json.dumps(override_payload)
    )

    monkeypatch.setattr(dimensions_data_mod, "_DATA_DIR", shared_dir)
    monkeypatch.setattr(dimensions_data_mod, "_LANG_DIR", lang_dir)
    dimensions_data_mod.load_dimensions_for_lang.cache_clear()
    try:
        dims, prompts, _ = dimensions_data_mod.load_dimensions_for_lang("python")
    finally:
        dimensions_data_mod.load_dimensions_for_lang.cache_clear()

    assert dims == ["lang_dim"]
    assert "lang_dim" in prompts
    assert "shared_dim" not in prompts


def test_load_dimensions_for_lang_falls_back_to_shared(tmp_path, monkeypatch):
    shared_dir = tmp_path / "review_data"
    lang_dir = tmp_path / "lang_data"
    shared_dir.mkdir(parents=True)
    (lang_dir / "python").mkdir(parents=True)

    shared_payload = {
        "default_dimensions": ["shared_dim"],
        "dimension_prompts": {
            "shared_dim": {
                "description": "shared desc",
                "look_for": ["x"],
                "skip": ["y"],
            }
        },
        "system_prompt": "s" * 120,
    }
    (shared_dir / "dimensions.json").write_text(json.dumps(shared_payload))

    monkeypatch.setattr(dimensions_data_mod, "_DATA_DIR", shared_dir)
    monkeypatch.setattr(dimensions_data_mod, "_LANG_DIR", lang_dir)
    dimensions_data_mod.load_dimensions_for_lang.cache_clear()
    try:
        dims, prompts, _ = dimensions_data_mod.load_dimensions_for_lang("python")
    finally:
        dimensions_data_mod.load_dimensions_for_lang.cache_clear()

    assert dims == ["shared_dim"]
    assert "shared_dim" in prompts


def test_load_dimensions_for_lang_applies_override_patch(tmp_path, monkeypatch):
    shared_dir = tmp_path / "review_data"
    lang_dir = tmp_path / "lang_data"
    shared_dir.mkdir(parents=True)
    (lang_dir / "python" / "review_data").mkdir(parents=True)

    shared_payload = {
        "default_dimensions": ["shared_dim"],
        "dimension_prompts": {
            "shared_dim": {
                "description": "shared desc",
                "look_for": ["x"],
                "skip": ["y"],
            }
        },
        "system_prompt": "s" * 120,
    }
    override_payload = {
        "default_dimensions_append": ["lang_dim"],
        "dimension_prompts": {
            "lang_dim": {
                "description": "lang desc",
                "look_for": ["a"],
                "skip": ["b"],
            }
        },
        "system_prompt_append": "lang-tail",
    }
    (shared_dir / "dimensions.json").write_text(json.dumps(shared_payload))
    (lang_dir / "python" / "review_data" / "dimensions.override.json").write_text(
        json.dumps(override_payload)
    )

    monkeypatch.setattr(dimensions_data_mod, "_DATA_DIR", shared_dir)
    monkeypatch.setattr(dimensions_data_mod, "_LANG_DIR", lang_dir)
    dimensions_data_mod.load_dimensions_for_lang.cache_clear()
    try:
        dims, prompts, system_prompt = dimensions_data_mod.load_dimensions_for_lang(
            "python"
        )
    finally:
        dimensions_data_mod.load_dimensions_for_lang.cache_clear()

    assert dims == ["shared_dim", "lang_dim"]
    assert "shared_dim" in prompts
    assert "lang_dim" in prompts
    assert "lang-tail" in system_prompt


def test_load_dimensions_for_lang_override_remove_and_append(tmp_path, monkeypatch):
    shared_dir = tmp_path / "review_data"
    lang_dir = tmp_path / "lang_data"
    shared_dir.mkdir(parents=True)
    (lang_dir / "python" / "review_data").mkdir(parents=True)

    shared_payload = {
        "default_dimensions": ["a", "b"],
        "dimension_prompts": {
            "a": {"description": "A", "look_for": ["x"], "skip": ["y"]},
            "b": {"description": "B", "look_for": ["x"], "skip": ["y"]},
        },
        "system_prompt": "h" * 120,
    }
    override_payload = {
        "default_dimensions_remove": ["b"],
        "default_dimensions_append": ["c"],
        "dimension_prompts": {
            "c": {"description": "C", "look_for": ["x"], "skip": ["y"]},
        },
    }
    (shared_dir / "dimensions.json").write_text(json.dumps(shared_payload))
    (lang_dir / "python" / "review_data" / "dimensions.override.json").write_text(
        json.dumps(override_payload)
    )

    monkeypatch.setattr(dimensions_data_mod, "_DATA_DIR", shared_dir)
    monkeypatch.setattr(dimensions_data_mod, "_LANG_DIR", lang_dir)
    dimensions_data_mod.load_dimensions_for_lang.cache_clear()
    try:
        dims, prompts, _ = dimensions_data_mod.load_dimensions_for_lang("python")
    finally:
        dimensions_data_mod.load_dimensions_for_lang.cache_clear()

    assert dims == ["a", "c"]
    assert "a" in prompts
    assert "c" in prompts


def test_parse_dimensions_payload_supports_meta_enabled_defaults():
    payload = {
        "dimension_prompts": {
            "structure_signal": {
                "description": "Structure signal quality",
                "look_for": ["flat root"],
                "skip": ["tiny repos"],
                "meta": {
                    "enabled_by_default": True,
                    "display_name": "Structure Signal",
                    "weight": 4.5,
                    "reset_on_scan": True,
                },
            }
        },
        "system_prompt": "x" * 120,
    }

    dims, prompts, _ = dimensions_validation_mod.parse_dimensions_payload(
        payload, context_prefix="test_payload"
    )

    assert dims == ["structure_signal"]
    assert prompts["structure_signal"]["meta"]["enabled_by_default"] is True
    assert prompts["structure_signal"]["meta"]["display_name"] == "Structure Signal"
    assert prompts["structure_signal"]["meta"]["weight"] == 4.5


def test_is_numeric_rejects_non_finite_floats():
    assert is_numeric(1) is True
    assert is_numeric(10**500) is True
    assert is_numeric(1.5) is True
    assert is_numeric(math.inf) is False
    assert is_numeric(-math.inf) is False
    assert is_numeric(math.nan) is False


def test_validate_prompt_meta_rejects_non_finite_weight():
    with pytest.raises(ValueError, match=r"prompt\.meta\.weight must be a number"):
        dimensions_validation_mod.validate_prompt_meta(
            {"weight": math.inf},
            context="prompt.meta",
        )

    with pytest.raises(ValueError, match=r"prompt\.meta\.weight must be a number"):
        dimensions_validation_mod.validate_prompt_meta(
            {"weight": math.nan},
            context="prompt.meta",
        )


def test_load_dimensions_for_lang_meta_enabled_dimension_requires_no_append(
    tmp_path, monkeypatch
):
    shared_dir = tmp_path / "review_data"
    lang_dir = tmp_path / "lang_data"
    shared_dir.mkdir(parents=True)
    (lang_dir / "python" / "review_data").mkdir(parents=True)

    shared_payload = {
        "default_dimensions": ["shared_dim"],
        "dimension_prompts": {
            "shared_dim": {
                "description": "shared desc",
                "look_for": ["x"],
                "skip": ["y"],
            }
        },
        "system_prompt": "s" * 120,
    }
    override_payload = {
        "dimension_prompts": {
            "lang_structure_nav": {
                "description": "lang nav",
                "look_for": ["z"],
                "skip": ["w"],
                "meta": {
                    "enabled_by_default": True,
                    "display_name": "Lang Structure Nav",
                    "weight": 3.2,
                },
            }
        }
    }
    (shared_dir / "dimensions.json").write_text(json.dumps(shared_payload))
    (lang_dir / "python" / "review_data" / "dimensions.override.json").write_text(
        json.dumps(override_payload)
    )

    monkeypatch.setattr(dimensions_data_mod, "_DATA_DIR", shared_dir)
    monkeypatch.setattr(dimensions_data_mod, "_LANG_DIR", lang_dir)
    dimensions_data_mod.load_dimensions_for_lang.cache_clear()
    try:
        dims, prompts, _ = dimensions_data_mod.load_dimensions_for_lang("python")
    finally:
        dimensions_data_mod.load_dimensions_for_lang.cache_clear()

    assert dims == ["shared_dim", "lang_structure_nav"]
    assert prompts["lang_structure_nav"]["meta"]["weight"] == 3.2
