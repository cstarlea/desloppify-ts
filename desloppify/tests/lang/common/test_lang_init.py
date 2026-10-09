"""Tests for desloppify.languages.framework — default_lang, get_lang, get_lang_hook."""

import pytest

from desloppify.languages._framework.base.types import DetectorPhase, LangConfig
from desloppify.languages.framework import (
    DEFAULT_LANG,
    default_lang,
    get_lang,
    get_lang_hook,
)

# ── get_lang ─────────────────────────────────────────────────


def test_get_lang_typescript():
    """get_lang('typescript') returns a LangConfig for TypeScript."""
    cfg = get_lang("typescript")
    assert isinstance(cfg, LangConfig)
    assert cfg.name == "typescript"
    assert any(ext in cfg.extensions for ext in [".ts", ".tsx"])


@pytest.mark.parametrize("name", ["javascript", "python", ""])
def test_get_lang_unknown_raises(name):
    """get_lang rejects every name but typescript."""
    with pytest.raises(ValueError, match="Unknown language"):
        get_lang(name)


def test_get_lang_returns_default_instance():
    """get_lang and default_lang share one config instance."""
    assert DEFAULT_LANG == "typescript"
    assert get_lang("typescript") is default_lang() is default_lang()


# ── get_lang_hook ────────────────────────────────────────────


def test_get_lang_hook_returns_typescript_test_coverage_module():
    hook = get_lang_hook("typescript", "test_coverage")
    assert hook is not None
    assert callable(getattr(hook, "has_testable_logic", None))
    assert callable(getattr(hook, "parse_test_import_specs", None))


@pytest.mark.parametrize(
    ("lang_name", "hook_name"),
    [(None, "test_coverage"), ("python", "test_coverage"), ("typescript", "nope")],
)
def test_get_lang_hook_returns_none_for_unknown(lang_name, hook_name):
    assert get_lang_hook(lang_name, hook_name) is None


# ── TypeScript config ────────────────────────────────────────


def test_typescript_config_core_contract():
    """The TypeScript config has the callables and tables scans rely on."""
    cfg = default_lang()
    assert cfg.phases
    assert all(isinstance(phase, DetectorPhase) for phase in cfg.phases)
    assert all(phase.label.strip() and callable(phase.run) for phase in cfg.phases)
    assert callable(cfg.extract_functions)
    assert callable(cfg.file_finder)
    assert callable(cfg.build_dep_graph)
    assert cfg.zone_rules
    assert cfg.default_scan_profile in {"objective", "full", "ci"}


def test_typescript_config_has_shared_core_phase_shape():
    """Shared review/security phases stay canonical and ordered."""
    cfg = default_lang()
    labels = [phase.label for phase in cfg.phases]
    assert labels.count("Test coverage") == 1
    assert labels.count("Security") == 1
    assert labels.count("Subjective review") == 1
    assert labels.count("Duplicates") == 1
    assert labels[-1] == "Duplicates"
    assert cfg.phases[-1].slow is True


def test_typescript_config_does_not_expose_legacy_setting_keys():
    assert not hasattr(default_lang(), "legacy_setting_keys")
