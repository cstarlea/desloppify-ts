"""Direct tests for language resolution helpers."""

from __future__ import annotations


import pytest

import desloppify.languages._framework.registry.state as registry_state
import desloppify.languages._framework.registry.resolution as lang_resolution_mod


def test_make_lang_config_wraps_constructor_errors():
    class _BadConfig:
        def __init__(self):
            raise RuntimeError("boom")

    with pytest.raises(
        ValueError, match="Failed to instantiate language config 'bad'"
    ) as exc:
        lang_resolution_mod.make_lang_config("bad", _BadConfig)
    msg = str(exc.value)
    assert "bad" in msg
    assert "boom" in msg


def test_get_lang_uses_registry_and_reports_unknown(monkeypatch):
    sentinel_cls = object()
    monkeypatch.setattr(registry_state._STATE, "registry", {"python": sentinel_cls})
    monkeypatch.setattr(lang_resolution_mod, "load_all", lambda **_kw: None)
    monkeypatch.setattr(
        lang_resolution_mod, "make_lang_config", lambda name, cfg_cls: (name, cfg_cls)
    )

    resolved = lang_resolution_mod.get_lang("python")
    assert resolved == ("python", sentinel_cls)
    assert resolved[0] == "python"
    assert resolved[1] is sentinel_cls
    assert registry_state.is_registered("python")

    with pytest.raises(ValueError, match="Unknown language") as exc:
        lang_resolution_mod.get_lang("missing")
    assert "Available: python" in str(exc.value)


def test_available_langs_returns_sorted_list(monkeypatch):
    monkeypatch.setattr(
        registry_state._STATE, "registry", {"zeta": object(), "alpha": object()}
    )
    monkeypatch.setattr(lang_resolution_mod, "load_all", lambda **_kw: None)

    langs = lang_resolution_mod.available_langs()
    assert langs == ["alpha", "zeta"]
    assert langs[0] < langs[1]


def test_reset_dynamic_registries_for_refresh_uses_discovery_runtime_boundary(
    monkeypatch,
):
    calls: list[str] = []

    monkeypatch.setattr(
        lang_resolution_mod,
        "reset_runtime_state",
        lambda: calls.append("runtime"),
    )
    monkeypatch.setattr(
        "desloppify.base.registry.reset_registered_detectors",
        lambda: calls.append("detectors"),
    )
    monkeypatch.setattr(
        "desloppify.engine._scoring.policy.core.reset_registered_scoring_policies",
        lambda: calls.append("scoring"),
    )

    lang_resolution_mod._reset_dynamic_registries_for_refresh()

    assert calls == ["detectors", "scoring", "runtime"]
