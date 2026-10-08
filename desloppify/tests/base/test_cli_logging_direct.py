"""Tests for CLI logging configuration (desloppify.base.output.cli_logging)."""

from __future__ import annotations

import io
import logging

import pytest

import desloppify.base.output.cli_logging as cli_logging_mod
from desloppify.base.output.cli_logging import LOG_LEVEL_ENV, configure_cli_logging
from desloppify.base.output.terminal import COLORS


@pytest.fixture(autouse=True)
def _clean_package_logger(monkeypatch):
    monkeypatch.delenv(LOG_LEVEL_ENV, raising=False)
    monkeypatch.delenv("NO_COLOR", raising=False)
    package_logger = logging.getLogger("desloppify")
    before_handlers = list(package_logger.handlers)
    before_level = package_logger.level
    yield
    package_logger.handlers[:] = before_handlers
    package_logger.setLevel(before_level)


def _cli_handlers() -> list[logging.Handler]:
    return [
        h
        for h in logging.getLogger("desloppify").handlers
        if isinstance(h, cli_logging_mod._StderrHandler)
    ]


class _TtyStream(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_warning_is_a_plain_line_on_captured_stderr(capsys):
    configure_cli_logging()
    logging.getLogger("desloppify.engine._state.persistence").warning(
        "State file %s is corrupt; using an empty state", "state.json"
    )
    err = capsys.readouterr().err
    assert err == "  WARNING: State file state.json is corrupt; using an empty state\n"


def test_warning_is_yellow_on_a_terminal(monkeypatch):
    stream = _TtyStream()
    monkeypatch.setattr("sys.stderr", stream)
    configure_cli_logging()
    logging.getLogger("desloppify.x").warning("careful")
    logging.getLogger("desloppify.x").error("broken")
    assert stream.getvalue() == (
        f"{COLORS['yellow']}  WARNING: careful{COLORS['reset']}\n"
        f"{COLORS['red']}  ERROR: broken{COLORS['reset']}\n"
    )


def test_no_color_disables_colour_on_a_terminal(monkeypatch):
    stream = _TtyStream()
    monkeypatch.setattr("sys.stderr", stream)
    monkeypatch.setenv("NO_COLOR", "1")
    configure_cli_logging()
    logging.getLogger("desloppify.x").warning("careful")
    assert stream.getvalue() == "  WARNING: careful\n"


def test_debug_hidden_by_default(capsys):
    configure_cli_logging()
    logging.getLogger("desloppify.x").debug("noise")
    logging.getLogger("desloppify.x").info("chatter")
    assert capsys.readouterr().err == ""


def test_level_from_environment(monkeypatch, capsys):
    monkeypatch.setenv(LOG_LEVEL_ENV, "debug")
    configure_cli_logging()
    logging.getLogger("desloppify.base.output.fallbacks").debug("fallback failed")
    assert capsys.readouterr().err == (
        "  DEBUG: fallback failed (desloppify.base.output.fallbacks)\n"
    )


def test_error_level_from_environment_hides_warnings(monkeypatch, capsys):
    monkeypatch.setenv(LOG_LEVEL_ENV, "ERROR")
    configure_cli_logging()
    logging.getLogger("desloppify.x").warning("careful")
    logging.getLogger("desloppify.x").error("broken")
    assert capsys.readouterr().err == "  ERROR: broken\n"


def test_unknown_environment_level_falls_back_to_warning(monkeypatch, capsys):
    monkeypatch.setenv(LOG_LEVEL_ENV, "chatty")
    configure_cli_logging()
    logging.getLogger("desloppify.x").info("chatter")
    logging.getLogger("desloppify.x").warning("careful")
    assert capsys.readouterr().err == "  WARNING: careful\n"


def test_exception_traceback_follows_the_message(capsys):
    configure_cli_logging()
    try:
        raise ValueError("bad value")
    except ValueError:
        logging.getLogger("desloppify.x").warning("append failed", exc_info=True)
    lines = capsys.readouterr().err.splitlines()
    assert lines[0] == "  WARNING: append failed"
    assert lines[1].startswith("Traceback")
    assert lines[-1] == "ValueError: bad value"


def test_configuring_twice_keeps_one_handler(capsys):
    configure_cli_logging()
    configure_cli_logging()
    assert len(_cli_handlers()) == 1
    logging.getLogger("desloppify.x").warning("once")
    assert capsys.readouterr().err == "  WARNING: once\n"


def test_other_libraries_are_left_alone(capsys):
    configure_cli_logging()
    assert _cli_handlers()
    assert not any(
        isinstance(h, cli_logging_mod._StderrHandler) for h in logging.getLogger().handlers
    )


def test_records_still_reach_caplog(caplog):
    configure_cli_logging()
    with caplog.at_level(logging.WARNING):
        logging.getLogger("desloppify.x").warning("seen by pytest")
    assert "seen by pytest" in caplog.text


def test_cli_main_configures_logging(monkeypatch, capsys):
    from desloppify import cli as cli_mod

    monkeypatch.setattr("sys.argv", ["desloppify"])
    cli_mod.main()  # no command: prints help
    assert len(_cli_handlers()) == 1
    capsys.readouterr()
