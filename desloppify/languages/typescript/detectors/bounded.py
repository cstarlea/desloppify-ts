"""Run a tool under a time and memory limit (the extra per-package runs of monorepo mode).

Memory is limited twice: ``--max-old-space-size`` in ``NODE_OPTIONS`` makes a
Node tool fail cleanly, and on Linux a watchdog sums the resident memory of
the process and everything it started (a native tool, such as TypeScript 7's
compiler, ignores the Node flag) and kills them all past the limit.
"""

from __future__ import annotations

import os
import signal
import subprocess  # nosec B404
import threading
import time
from dataclasses import dataclass
from pathlib import Path

_POLL_SECONDS = 0.25
_PAGE_BYTES = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096


@dataclass(frozen=True)
class RunLimits:
    timeout: float
    max_memory_mb: int


class Budget:
    """Total time for a sequence of bounded runs, and each run's memory limit.

    ``seconds`` of 0 means no total limit; each run still gets at most ``cap``.
    """

    def __init__(self, seconds: float, max_memory_mb: int) -> None:
        self.seconds = seconds
        self.max_memory_mb = max_memory_mb
        self._start: float | None = None

    def limits(self, cap: float) -> RunLimits | None:
        """Limits for the next run, or None once the total time is spent."""
        now = time.monotonic()
        if self._start is None:
            self._start = now
        if not self.seconds:
            return RunLimits(cap, self.max_memory_mb)
        left = self.seconds - (now - self._start)
        return RunLimits(min(left, cap), self.max_memory_mb) if left > 0 else None


class MemoryLimitExceeded(subprocess.SubprocessError):
    """The process tree went over ``RunLimits.max_memory_mb`` and was killed."""


def _session_rss_mb(session: int) -> float | None:
    """Resident memory of every process in ``session``; None without ``/proc``."""
    proc = Path("/proc")
    if not proc.is_dir():
        return None
    pages = 0
    for entry in proc.iterdir():
        if not entry.name.isdigit():
            continue
        try:
            stat = (entry / "stat").read_text()
            # Fields after the parenthesised command: state ppid pgrp session ...
            if int(stat.rsplit(")", 1)[1].split()[3]) != session:
                continue
            pages += int((entry / "statm").read_text().split()[1])
        except (OSError, ValueError, IndexError):
            continue
    return pages * _PAGE_BYTES / 2**20


def _kill(process: subprocess.Popen[str]) -> None:
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except (OSError, AttributeError):
        process.kill()


def _env(env: dict[str, str] | None, limits: RunLimits) -> dict[str, str]:
    merged = dict(os.environ if env is None else env)
    flag = f"--max-old-space-size={limits.max_memory_mb}"
    merged["NODE_OPTIONS"] = f"{merged.get('NODE_OPTIONS', '')} {flag}".strip()
    return merged


def run_bounded(
    cmd: list[str],
    *,
    cwd: Path,
    limits: RunLimits,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """``subprocess.run`` with output captured, killed past either limit.

    Raises ``subprocess.TimeoutExpired`` or ``MemoryLimitExceeded``.
    """
    process = subprocess.Popen(  # nosec B603
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        cwd=cwd,
        env=_env(env, limits),
        start_new_session=os.name != "nt",
    )
    exceeded = threading.Event()
    done = threading.Event()

    def watch() -> None:
        while not done.wait(_POLL_SECONDS):
            rss = _session_rss_mb(process.pid)
            if rss is None:
                return
            if rss > limits.max_memory_mb:
                exceeded.set()
                _kill(process)
                return

    watchdog = threading.Thread(target=watch, daemon=True)
    if os.name != "nt":
        watchdog.start()
    try:
        stdout, stderr = process.communicate(timeout=limits.timeout)
    except subprocess.TimeoutExpired:
        _kill(process)
        process.communicate()
        raise
    finally:
        done.set()
    if exceeded.is_set():
        raise MemoryLimitExceeded(f"over {limits.max_memory_mb} MB")
    return subprocess.CompletedProcess(cmd, process.returncode, stdout, stderr)


__all__ = ["Budget", "MemoryLimitExceeded", "RunLimits", "run_bounded"]
