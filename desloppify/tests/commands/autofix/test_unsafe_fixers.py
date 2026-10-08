"""Fixers marked unsafe must not write without --unsafe or be suggested."""

from __future__ import annotations

from argparse import Namespace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import desloppify.app.commands.autofix.apply_retro as retro_mod
import desloppify.app.commands.autofix.cmd as cmd_mod
from desloppify.base.exception_sets import CommandError
from desloppify.intelligence.narrative.action_engine import supported_fixers
from desloppify.languages._framework.base.types import FixerConfig
from desloppify.languages.typescript._fixers import get_ts_fixers


def _fixer(*, unsafe: bool, calls: list | None = None) -> FixerConfig:
    def fix(entries, *, dry_run=False):
        if calls is not None:
            calls.append(dry_run)
        return SimpleNamespace(entries=[], skip_reasons={})

    return FixerConfig(
        label="things",
        detect=lambda _p: [{"file": "a.ts", "line": 1}],
        fix=fix,
        detector="unused",
        unsafe=unsafe,
    )


def _args(**overrides) -> Namespace:
    base = {"fixer": "risky", "path": ".", "dry_run": False, "unsafe": False}
    base.update(overrides)
    return Namespace(**base)


def _run(fixer: FixerConfig, args: Namespace) -> None:
    with (
        patch.object(cmd_mod, "resolve_fixer_config", return_value=(None, fixer)),
        patch.object(cmd_mod, "_warn_uncommitted_changes"),
        patch.object(cmd_mod, "_apply_and_report"),
        patch.object(cmd_mod, "_report_dry_run"),
        patch.object(cmd_mod, "_print_fix_summary"),
    ):
        cmd_mod.cmd_autofix(args)


def test_unsafe_fixer_refuses_to_write_without_flag() -> None:
    calls: list = []
    with pytest.raises(CommandError, match="marked unsafe"):
        _run(_fixer(unsafe=True, calls=calls), _args())
    assert calls == []


def test_unsafe_fixer_allows_dry_run_and_explicit_opt_in() -> None:
    calls: list = []
    _run(_fixer(unsafe=True, calls=calls), _args(dry_run=True))
    _run(_fixer(unsafe=True, calls=calls), _args(unsafe=True))
    assert calls == [True, False]


def test_safe_fixer_runs_without_flag() -> None:
    calls: list = []
    _run(_fixer(unsafe=False, calls=calls), _args())
    assert calls == [False]


def test_typescript_code_rewriting_fixers_are_marked_unsafe() -> None:
    unsafe = {name for name, fixer in get_ts_fixers().items() if fixer.unsafe}
    assert unsafe == {
        "empty-if-chain",
    }


def test_unsafe_fixers_are_not_advertised_as_supported() -> None:
    supported = supported_fixers({}, "typescript")
    assert supported == {
        "dead-useeffect",
        "debug-logs",
        "unused-imports",
        "unused-params",
        "unused-vars",
    }


def test_import_cascade_skips_unsafe_fixer(capsys) -> None:
    lang = SimpleNamespace(fixers={"unused-imports": _fixer(unsafe=True)})
    retro_mod._cascade_unused_import_cleanup(
        Path("."), state={"issues": {}}, _prev_score=0.0, dry_run=False, lang=lang
    )
    assert "marked unsafe" in capsys.readouterr().out
