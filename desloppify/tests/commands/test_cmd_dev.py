"""Tests for desloppify.app.commands.dev."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import desloppify.app.commands.dev as dev_mod
from desloppify.base.exception_sets import CommandError


def test_unknown_dev_action_raises():
    with pytest.raises(CommandError, match="test-hermes"):
        dev_mod.cmd_dev(SimpleNamespace(dev_action="scaffold-lang"))
