"""Logging for the CLI: plain ``  WARNING: ...`` lines on stderr, coloured like ``colorize``."""

from __future__ import annotations

import logging
import os
import sys

from desloppify.base.output.terminal import COLORS, no_color_enabled

LOG_LEVEL_ENV = "DESLOPPIFY_LOG_LEVEL"
_DEFAULT_LEVEL = logging.WARNING
_LEVEL_COLORS = {
    logging.CRITICAL: "red",
    logging.ERROR: "red",
    logging.WARNING: "yellow",
    logging.INFO: "dim",
    logging.DEBUG: "dim",
}


class _StderrHandler(logging.StreamHandler):
    """Writes to whatever ``sys.stderr`` is at emit time, so captured stderr sees it."""

    def __init__(self) -> None:
        super().__init__(sys.stderr)

    @property  # type: ignore[override]
    def stream(self):
        return sys.stderr

    @stream.setter
    def stream(self, _value) -> None:
        pass


class CliLogFormatter(logging.Formatter):
    """``  WARNING: message``, in the colour ``warn_best_effort`` uses for that level.

    Colour only when stderr is a terminal and ``NO_COLOR`` is unset. DEBUG
    records name their logger, since they are for whoever is debugging.
    """

    def format(self, record: logging.LogRecord) -> str:
        message = record.getMessage()
        if record.levelno <= logging.DEBUG:
            message = f"{message} ({record.name})"
        text = f"  {record.levelname}: {message}"
        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
        if record.exc_text:
            text = f"{text}\n{record.exc_text}"
        if record.stack_info:
            text = f"{text}\n{self.formatStack(record.stack_info)}"
        color = _color_for(record.levelno)
        if color is None or no_color_enabled() or not _stderr_isatty():
            return text
        return f"{COLORS[color]}{text}{COLORS['reset']}"


def _color_for(levelno: int) -> str | None:
    for level in sorted(_LEVEL_COLORS, reverse=True):
        if levelno >= level:
            return _LEVEL_COLORS[level]
    return _LEVEL_COLORS[logging.DEBUG]


def _stderr_isatty() -> bool:
    try:
        return sys.stderr.isatty()
    except (AttributeError, ValueError):
        return False


def _level_from_env() -> int:
    raw = os.environ.get(LOG_LEVEL_ENV, "").strip()
    if not raw:
        return _DEFAULT_LEVEL
    if raw.isdigit():
        return int(raw)
    level = logging.getLevelName(raw.upper())
    return level if isinstance(level, int) else _DEFAULT_LEVEL


def configure_cli_logging(level: int | None = None) -> None:
    """Send ``desloppify.*`` log records to stderr in the CLI's format.

    Idempotent: a second call only updates the level. The level comes from
    *level*, else ``DESLOPPIFY_LOG_LEVEL`` (a name such as ``DEBUG`` or a
    number), else WARNING. Records still propagate to the root logger, which
    has no handlers in the CLI, so nothing prints twice; with a handler
    installed here, Python's last-resort handler no longer prints the bare
    message.
    """
    resolved = _level_from_env() if level is None else level
    package_logger = logging.getLogger("desloppify")
    handler = next(
        (h for h in package_logger.handlers if isinstance(h, _StderrHandler)), None
    )
    if handler is None:
        handler = _StderrHandler()
        handler.setFormatter(CliLogFormatter())
        package_logger.addHandler(handler)
    handler.setLevel(resolved)
    # The root logger's WARNING already lets warnings through; lower the
    # package logger only when asked for more, so its level stays NOTSET
    # (and follows the root's) otherwise.
    package_logger.setLevel(resolved if resolved < _DEFAULT_LEVEL else logging.NOTSET)


__all__ = ["LOG_LEVEL_ENV", "CliLogFormatter", "configure_cli_logging"]
