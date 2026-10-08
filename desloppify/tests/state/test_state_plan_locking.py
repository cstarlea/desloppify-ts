"""Read-modify-write of state and plan is serialized by the locks (CE-4).

The concurrency tests use real processes: two commands racing on the same
``.desloppify`` directory are separate processes, and only the OS lock
(not the in-process one) serializes them.
"""

from __future__ import annotations

import argparse
import json
import multiprocessing
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from desloppify.app.commands.helpers.command_lock import (
    command_lock,
    command_needs_lock,
)
from desloppify.base.discovery import file_paths as file_paths_mod
from desloppify.base.discovery.file_paths import LockOrderError, exclusive_file_lock
from desloppify.engine._plan.persistence import load_plan, plan_lock, save_plan
from desloppify.engine._state import progression as progression_mod
from desloppify.engine._state.persistence import (
    hold_state_lock,
    load_state,
    save_state,
    state_lock,
)
from desloppify.engine._state.schema import empty_state

_CTX = multiprocessing.get_context("spawn")
_WORKERS = 4
_ROUNDS = 15


# ---------------------------------------------------------------------------
# Worker functions (module level so ``spawn`` can import them)
# ---------------------------------------------------------------------------


def _state_worker(state_file: str, worker: int, rounds: int) -> None:
    for i in range(rounds):
        with state_lock(Path(state_file)) as state:
            state["scan_count"] = int(state.get("scan_count", 0)) + 1
            ignores = list(state.get("config_ignores") or [])
            ignores.append(f"w{worker}-{i}")
            state["config_ignores"] = ignores


def _plan_worker(plan_file: str, worker: int, rounds: int) -> None:
    path = Path(plan_file)
    for i in range(rounds):
        with plan_lock(path):
            plan = load_plan(path)
            plan["queue_order"].append(f"w{worker}-{i}")
            save_plan(plan, path)


def _command_worker(state_dir: str, worker: int, rounds: int) -> None:
    """Simulate a mutating command: load state and plan, edit both, save."""
    state_file = Path(state_dir) / "state.json"
    plan_file = Path(state_dir) / "plan.json"
    args = argparse.Namespace(command="plan", plan_action="queue", state=str(state_file))
    for i in range(rounds):
        with command_lock(args):
            state = load_state(state_file)
            plan = load_plan(plan_file)
            state["scan_count"] = int(state.get("scan_count", 0)) + 1
            plan["queue_order"].append(f"w{worker}-{i}")
            save_state(state, state_file)
            save_plan(plan, plan_file)


def _load_state_worker(state_file: str, start: float) -> None:
    while time.time() < start:
        time.sleep(0.001)
    load_state(Path(state_file))


def _load_plan_worker(plan_file: str, start: float) -> None:
    while time.time() < start:
        time.sleep(0.001)
    load_plan(Path(plan_file))


def _scanned_state() -> dict:
    # A state backed by a scan; without one, a load rebuilds state from a
    # non-empty plan and the counter would be reset.
    state = empty_state()
    state["last_scan"] = "2026-01-01T00:00:00+00:00"
    return state


def _run_processes(target, args_for_worker) -> None:
    procs = [
        _CTX.Process(target=target, args=args_for_worker(worker))
        for worker in range(_WORKERS)
    ]
    for proc in procs:
        proc.start()
    for proc in procs:
        proc.join(timeout=120)
    for proc in procs:
        assert proc.exitcode == 0, f"worker exited with {proc.exitcode}"


def _expected_entries() -> set[str]:
    return {f"w{w}-{i}" for w in range(_WORKERS) for i in range(_ROUNDS)}


# ---------------------------------------------------------------------------
# No lost updates across processes
# ---------------------------------------------------------------------------


def test_concurrent_state_lock_loses_no_update(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    save_state(empty_state(), state_file)

    _run_processes(_state_worker, lambda w: (str(state_file), w, _ROUNDS))

    state = load_state(state_file)
    assert state["scan_count"] == _WORKERS * _ROUNDS
    assert set(state["config_ignores"]) == _expected_entries()


def test_concurrent_plan_lock_loses_no_update(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.json"

    _run_processes(_plan_worker, lambda w: (str(plan_file), w, _ROUNDS))

    assert set(load_plan(plan_file)["queue_order"]) == _expected_entries()
    assert len(load_plan(plan_file)["queue_order"]) == _WORKERS * _ROUNDS


def test_concurrent_commands_lose_no_state_or_plan_update(tmp_path: Path) -> None:
    state_dir = tmp_path / ".desloppify"
    state_dir.mkdir()
    save_state(_scanned_state(), state_dir / "state.json")

    _run_processes(_command_worker, lambda w: (str(state_dir), w, _ROUNDS))

    assert load_state(state_dir / "state.json")["scan_count"] == _WORKERS * _ROUNDS
    queue = load_plan(state_dir / "plan.json")["queue_order"]
    assert len(queue) == _WORKERS * _ROUNDS
    assert set(queue) == _expected_entries()


def test_concurrent_cli_commands_keep_every_cluster(tmp_path: Path) -> None:
    """Real `desloppify plan cluster create` runs, started together."""
    (tmp_path / ".desloppify").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "a.ts").write_text("export const a = 1;\n")
    env = {**os.environ, "DESLOPPIFY_ROOT": str(tmp_path)}
    names = [f"cluster{i}" for i in range(_WORKERS)]
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "desloppify", "plan", "cluster", "create", name,
             "--description", "race"],
            cwd=tmp_path,
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        for name in names
    ]
    for proc in procs:
        _, err = proc.communicate(timeout=120)
        assert proc.returncode == 0, err.decode()

    plan = load_plan(tmp_path / ".desloppify" / "plan.json")
    assert set(names) <= set(plan["clusters"])


# ---------------------------------------------------------------------------
# Deterministic ordering: a second RMW waits for the first
# ---------------------------------------------------------------------------


def test_second_state_lock_waits_and_sees_first_update(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    save_state(empty_state(), state_file)
    first_inside = threading.Event()

    def first() -> None:
        with state_lock(state_file) as state:
            first_inside.set()
            time.sleep(0.3)
            state["scan_count"] = state.get("scan_count", 0) + 1

    def second() -> None:
        first_inside.wait(5)
        with state_lock(state_file) as state:
            state["scan_count"] = state.get("scan_count", 0) + 1

    threads = [threading.Thread(target=first), threading.Thread(target=second)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)

    # Unlocked, both would read 0 and the result would be 1.
    assert load_state(state_file)["scan_count"] == 2


# ---------------------------------------------------------------------------
# Corrupt-file recovery happens once, under the lock
# ---------------------------------------------------------------------------


def test_concurrent_loads_of_corrupt_state_recover_it_once(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    good = empty_state()
    good["scan_count"] = 7
    save_state(good, state_file)
    backup = state_file.with_suffix(".json.bak")
    backup.write_text(state_file.read_text())
    state_file.write_text("{ not json")

    start = time.time() + 1.5
    _run_processes(_load_state_worker, lambda w: (str(state_file), start))

    corrupted = sorted(p.name for p in tmp_path.iterdir() if ".corrupted" in p.name)
    # Unlocked, a second load could set aside the file the first restored.
    assert corrupted == ["state.json.corrupted"]
    assert json.loads(state_file.read_text())["scan_count"] == 7


def test_concurrent_loads_of_corrupt_plan_recover_it_once(tmp_path: Path) -> None:
    plan_file = tmp_path / "plan.json"
    plan = load_plan(plan_file)
    plan["queue_order"] = ["keep-me"]
    save_plan(plan, plan_file)
    plan_file.with_suffix(".json.bak").write_text(plan_file.read_text())
    plan_file.write_text("{ not json")

    start = time.time() + 1.5
    _run_processes(_load_plan_worker, lambda w: (str(plan_file), start))

    corrupted = sorted(p.name for p in tmp_path.iterdir() if ".corrupted" in p.name)
    assert corrupted == ["plan.json.corrupted"]
    assert json.loads(plan_file.read_text())["queue_order"] == ["keep-me"]


def test_racing_threads_recover_corrupt_state_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Slow, staggered reads: a later load reads the corrupt file before an
    earlier one restores it, then would set the restored file aside."""
    from desloppify.engine._state import persistence as persistence_mod

    state_file = tmp_path / "state.json"
    good = empty_state()
    good["scan_count"] = 9
    save_state(good, state_file)
    state_file.with_suffix(".json.bak").write_text(state_file.read_text())
    state_file.write_text("{ not json")

    real_read = persistence_mod._read_state_file

    def slow_read(path):
        try:
            return real_read(path)
        finally:
            if path == state_file:
                time.sleep(0.2)

    monkeypatch.setattr(persistence_mod, "_read_state_file", slow_read)
    loaded: list[dict] = []

    def load(delay: float) -> None:
        time.sleep(delay)
        loaded.append(load_state(state_file))

    threads = [threading.Thread(target=load, args=(0.05 * i,)) for i in range(3)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)

    assert [s["scan_count"] for s in loaded] == [9, 9, 9]
    corrupted = sorted(p.name for p in tmp_path.iterdir() if ".corrupted" in p.name)
    # Unlocked, a later load would set aside the file an earlier one restored.
    assert corrupted == ["state.json.corrupted"]
    assert json.loads(state_file.read_text())["scan_count"] == 9


def test_corrupt_state_recovers_in_memory_when_lock_is_busy(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from desloppify.engine._state import persistence as persistence_mod

    monkeypatch.setattr(persistence_mod, "_RECOVERY_LOCK_TIMEOUT", 0.1)
    state_file = tmp_path / "state.json"
    good = empty_state()
    good["scan_count"] = 3
    save_state(good, state_file)
    state_file.with_suffix(".json.bak").write_text(state_file.read_text())
    state_file.write_text("{ not json")

    holder_ready = threading.Event()
    release = threading.Event()

    def holder() -> None:
        with hold_state_lock(state_file):
            holder_ready.set()
            release.wait(5)

    thread = threading.Thread(target=holder)
    thread.start()
    holder_ready.wait(5)
    try:
        state = load_state(state_file)
    finally:
        release.set()
        thread.join(5)

    assert state["scan_count"] == 3
    # Nothing on disk was touched without the lock.
    assert state_file.read_text() == "{ not json"
    assert not list(tmp_path.glob("*.corrupted*"))


# ---------------------------------------------------------------------------
# Progression trim is atomic with respect to appends
# ---------------------------------------------------------------------------


def test_progression_trim_drops_no_concurrent_append(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "progression.jsonl"
    per_thread = 40
    real_write = progression_mod.safe_write_text

    def slow_write(path, content):
        # Widen the gap between the trim's read and its rewrite; an append
        # landing in it would be lost if the trim ran outside the lock.
        time.sleep(0.005)
        real_write(path, content)

    monkeypatch.setattr(progression_mod, "safe_write_text", slow_write)
    monkeypatch.setattr(progression_mod, "_MAX_LINES", 10_000)
    log.write_text("".join(json.dumps({"pad": i}) + "\n" for i in range(10_001)))

    # scan_count=50 makes every append also run the trim pass.
    def appender(worker: int) -> None:
        for i in range(per_thread):
            progression_mod.append_progression_event(
                {"event_type": "t", "scan_count": 50, "id": f"{worker}-{i}"},
                path=log,
            )

    threads = [threading.Thread(target=appender, args=(w,)) for w in range(_WORKERS)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)

    ids = {
        event["id"] for event in progression_mod.load_progression(log) if "id" in event
    }
    assert len(ids) == _WORKERS * per_thread


def test_progression_trim_is_skipped_without_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    log = tmp_path / "progression.jsonl"
    log.write_text("".join(json.dumps({"n": i}) + "\n" for i in range(5)))
    monkeypatch.setattr(progression_mod, "_MAX_LINES", 2)

    def busy(*_args, **_kwargs):
        raise TimeoutError("busy")

    monkeypatch.setattr(progression_mod, "exclusive_file_lock", busy)
    progression_mod.append_progression_event(
        {"event_type": "t", "scan_count": 50}, path=log
    )
    assert len(progression_mod.load_progression(log)) == 6


# ---------------------------------------------------------------------------
# Nesting is deadlock-free; lock order is enforced
# ---------------------------------------------------------------------------


def _finishes(fn, seconds: float = 5.0) -> bool:
    done = threading.Event()
    errors: list[BaseException] = []

    def run() -> None:
        try:
            fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised below
            errors.append(exc)
        finally:
            done.set()

    threading.Thread(target=run, daemon=True).start()
    finished = done.wait(seconds)
    if errors:
        raise errors[0]
    return finished


def test_nested_locks_inside_a_command_do_not_deadlock(tmp_path: Path) -> None:
    state_dir = tmp_path / ".desloppify"
    state_dir.mkdir()
    state_file = state_dir / "state.json"
    plan_file = state_dir / "plan.json"
    save_state(_scanned_state(), state_file)
    args = argparse.Namespace(command="plan", plan_action="cluster", state=str(state_file))

    def nested() -> None:
        with command_lock(args):
            # Helpers that lock again (cluster update, state_lock users,
            # corrupt-file recovery) re-enter instead of waiting on themselves.
            with state_lock(state_file) as state:
                state["scan_count"] = 1
            with plan_lock(plan_file):
                with plan_lock(plan_file):
                    plan = load_plan(plan_file)
                    plan["queue_order"].append("x")
                    save_plan(plan, plan_file)
            with hold_state_lock(state_file):
                pass

    assert _finishes(nested)
    assert load_state(state_file)["scan_count"] == 1
    assert load_plan(plan_file)["queue_order"] == ["x"]


def test_corrupt_load_under_held_lock_recovers_without_deadlock(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"
    good = empty_state()
    good["scan_count"] = 4
    save_state(good, state_file)
    state_file.with_suffix(".json.bak").write_text(state_file.read_text())
    state_file.write_text("{ not json")
    loaded: list[dict] = []

    def run() -> None:
        with hold_state_lock(state_file):
            loaded.append(load_state(state_file))

    assert _finishes(run)
    assert loaded[0]["scan_count"] == 4
    assert (tmp_path / "state.json.corrupted").exists()


def test_taking_state_lock_after_plan_lock_fails_fast(tmp_path: Path) -> None:
    state_file = tmp_path / "state.json"

    def wrong_order() -> None:
        with plan_lock(tmp_path / "plan.json"):
            with pytest.raises(LockOrderError):
                with hold_state_lock(state_file):
                    pass

    assert _finishes(wrong_order)


def test_corrupt_state_load_under_plan_lock_recovers_in_memory(tmp_path: Path) -> None:
    """Recovery would need state-after-plan; it skips the disk instead of hanging."""
    state_file = tmp_path / "state.json"
    good = empty_state()
    good["scan_count"] = 5
    save_state(good, state_file)
    state_file.with_suffix(".json.bak").write_text(state_file.read_text())
    state_file.write_text("{ not json")
    loaded: list[dict] = []

    def run() -> None:
        with plan_lock(tmp_path / "plan.json"):
            loaded.append(load_state(state_file))

    assert _finishes(run)
    assert loaded[0]["scan_count"] == 5
    assert state_file.read_text() == "{ not json"


# ---------------------------------------------------------------------------
# Lock helper details
# ---------------------------------------------------------------------------


def test_lock_without_fcntl_still_serializes_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if sys.platform == "win32":
        pytest.skip("Windows uses msvcrt, not fcntl")
    monkeypatch.setitem(sys.modules, "fcntl", None)  # import fcntl -> ImportError
    lock = tmp_path / "x.lock"
    counter = {"n": 0}

    def bump() -> None:
        for _ in range(200):
            with exclusive_file_lock(lock, timeout=10):
                value = counter["n"]
                time.sleep(0)
                counter["n"] = value + 1

    threads = [threading.Thread(target=bump) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert counter["n"] == 800


def test_lock_released_after_exception_and_fd_closed(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    with pytest.raises(ValueError):
        with exclusive_file_lock(lock, timeout=1):
            raise ValueError("boom")
    entry = file_paths_mod._held_locks[Path(os.path.abspath(lock))]
    assert entry.depth == 0 and entry.fd is None
    # Another process can take it straight away.
    probe = subprocess.run(
        [sys.executable, "-c",
         "import sys; from pathlib import Path;"
         "from desloppify.base.discovery.file_paths import exclusive_file_lock as l\n"
         f"with l(Path({str(lock)!r}), timeout=0): pass"],
        capture_output=True,
        timeout=60,
    )
    assert probe.returncode == 0, probe.stderr.decode()
    # The lock file itself stays (unlinking it under a waiter is unsafe);
    # no temp files are left behind.
    assert sorted(p.name for p in tmp_path.iterdir()) == ["x.lock"]


def test_lock_times_out_against_another_process(tmp_path: Path) -> None:
    lock = tmp_path / "x.lock"
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time; from pathlib import Path;"
         "from desloppify.base.discovery.file_paths import exclusive_file_lock as l\n"
         f"with l(Path({str(lock)!r}), timeout=10):\n"
         "    print('held', flush=True); time.sleep(30)"],
        stdout=subprocess.PIPE,
    )
    try:
        assert holder.stdout is not None
        assert holder.stdout.readline().strip() == b"held"
        waits: list[int] = []
        with pytest.raises(TimeoutError):
            with exclusive_file_lock(lock, timeout=0.2, on_wait=lambda: waits.append(1)):
                pass
        assert waits == [1]
    finally:
        holder.kill()
        holder.wait()


# ---------------------------------------------------------------------------
# Which commands take the command lock
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("namespace", "expected"),
    [
        ({"command": "scan"}, True),
        ({"command": "plan", "plan_action": "cluster"}, True),
        ({"command": "plan", "plan_action": "triage", "run_stages": False}, True),
        ({"command": "plan", "plan_action": "triage", "run_stages": True}, False),
        ({"command": "review", "import_file": "x.json"}, True),
        ({"command": "review", "run_batches": True}, False),
        ({"command": "review", "import_file": "x.json", "scan_after_import": True}, False),
        ({"command": "suppress"}, True),
        ({"command": "exclude"}, True),
        ({"command": "zone"}, True),
        ({"command": "autofix"}, True),
        ({"command": "config"}, True),
        ({"command": "status"}, False),
        ({"command": "show"}, False),
        ({"command": "next"}, False),
        ({"command": "backlog"}, False),
    ],
)
def test_command_needs_lock(namespace: dict, expected: bool) -> None:
    assert command_needs_lock(argparse.Namespace(**namespace)) is expected
