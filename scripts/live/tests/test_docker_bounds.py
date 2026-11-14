"""Every Docker command the engines run is bounded, and so is every wait for a bring-up lock.

A hung Docker daemon used to hang the declaration that held the port's registry lock, a session teardown and
every server-file read, and a waiter for a bring-up lock waited forever. The scan below finds any
`subprocess.run` (or `check_output`/`check_call`/`call`) without a `timeout` in either engine; the only
exception is a build whose output streams to the terminal.
"""
from __future__ import annotations

import ast
import os
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from filelock import FileLock, Timeout

from livetest import dockercmd

LIVE = Path(__file__).resolve().parents[1] / "livetest"
INTEG = Path(__file__).resolve().parents[2] / "integration" / "inttest"

# (file, enclosing function): builds whose output streams to the terminal, watched and stoppable by the user.
STREAMING_BUILDS = {("livetest/striim_provision.py", "_stream")}


def _untimed_calls(path: Path, rel: str):
    found = []

    def visit(node, fn):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            fn = node.name
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name) and node.func.value.id == "subprocess"
                and node.func.attr in ("run", "check_output", "check_call", "call")):
            kws = {k.arg for k in node.keywords}
            if "timeout" not in kws and None not in kws:
                found.append((rel, fn, node.lineno))
        for child in ast.iter_child_nodes(node):
            visit(child, fn)

    visit(ast.parse(path.read_text()), None)
    return found


def _scan():
    out = []
    for root in (LIVE, INTEG):
        for path in sorted(root.rglob("*.py")):
            out += _untimed_calls(path, f"{root.name}/{path.relative_to(root).as_posix()}")
    return out


def test_no_subprocess_call_without_a_timeout_outside_streaming_builds():
    untimed = [c for c in _scan() if (c[0], c[1]) not in STREAMING_BUILDS]
    assert untimed == []


def test_the_streaming_allow_list_names_only_calls_that_exist():
    present = {(c[0], c[1]) for c in _scan()}
    assert STREAMING_BUILDS <= present        # a stale entry would hide a new untimed call of that name


# ---------------------------------------------------------------- the runner

def _sleeper(seconds):
    return [sys.executable, "-c", f"import time; time.sleep({seconds})"]


def test_a_command_that_outlives_its_bound_is_stopped_and_answers_like_a_failure():
    started = time.monotonic()
    r = dockercmd.run(_sleeper(30), timeout=0.3)
    assert time.monotonic() - started < 10
    assert r.returncode == dockercmd.TIMED_OUT and r.stdout == ""
    assert "did not finish within 0s and was stopped" in r.stderr and "Docker daemon" not in r.stderr


def test_a_docker_command_that_times_out_says_so():
    msg = dockercmd.timed_out_message(["docker", "ps", "-a", "--filter", "x"], 60)
    assert msg == "`docker ps -a ...` did not finish within 60s and was stopped (is the Docker daemon responding?)"


def test_a_checked_command_that_times_out_raises_a_called_process_error_naming_the_bound():
    with pytest.raises(subprocess.CalledProcessError) as ei:
        dockercmd.run(_sleeper(30), timeout=0.3, check=True, capture=False)
    assert ei.value.returncode == dockercmd.TIMED_OUT and "did not finish within" in str(ei.value)


def test_a_command_inside_its_bound_is_returned_unchanged():
    r = dockercmd.run([sys.executable, "-c", "import sys; print('out'); sys.exit(3)"], timeout=30)
    assert r.returncode == 3 and r.stdout == "out\n"
    with pytest.raises(subprocess.CalledProcessError) as ei:
        dockercmd.run([sys.executable, "-c", "import sys; sys.exit(3)"], timeout=30, check=True)
    assert ei.value.returncode == 3


def test_the_bounds_are_named_and_ordered():
    assert dockercmd.QUERY_TIMEOUT_S == 60.0
    assert dockercmd.COMPOSE_DOWN_TIMEOUT_S == 600.0
    assert dockercmd.COMPOSE_UP_TIMEOUT_S == 10800.0
    assert dockercmd.BUILD_TIMEOUT_S == 3600.0
    assert dockercmd.LOCK_WAIT_S == dockercmd.COMPOSE_DOWN_TIMEOUT_S + 2 * dockercmd.COMPOSE_UP_TIMEOUT_S


# ---------------------------------------------------------------- the streamed bring-up

_GRANDCHILD = ("import subprocess, sys, time; "
               "subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)']); "
               "print('Container x Starting', flush=True); time.sleep(60)")


def test_a_streamed_command_and_its_children_are_killed_at_the_bound():
    # `docker compose` is a child of `docker`: killing only the first would leave the pipe open.
    lines = []
    started = time.monotonic()
    code = dockercmd.stream([sys.executable, "-c", _GRANDCHILD], lines.append, timeout=1.0)
    assert time.monotonic() - started < 20
    assert code == dockercmd.TIMED_OUT and lines == ["Container x Starting\n"]


def test_an_interrupted_stream_kills_the_command():
    def interrupt(line):
        raise KeyboardInterrupt
    started = time.monotonic()
    with pytest.raises(KeyboardInterrupt):
        dockercmd.stream([sys.executable, "-c", _GRANDCHILD], interrupt, timeout=60)
    assert time.monotonic() - started < 20


def test_a_streamed_command_returns_its_exit_status():
    lines = []
    code = dockercmd.stream([sys.executable, "-c", "print('a'); print('b'); raise SystemExit(2)"], lines.append,
                            timeout=30)
    assert code == 2 and lines == ["a\n", "b\n"]


def test_the_streamed_compose_up_is_bounded(monkeypatch, tmp_path):
    from livetest import services
    seen = {}

    def fake_stream(argv, on_line, *, timeout, env=None):
        seen.update(argv=argv, timeout=timeout)
        on_line("Container slt-postgres  Waiting\n")
        return dockercmd.TIMED_OUT
    monkeypatch.setattr(services.dockercmd, "stream", fake_stream)
    monkeypatch.setattr(services, "_preflight_profile", lambda defn: None)
    monkeypatch.setattr(services, "_compose_reset", lambda compose: None)
    monkeypatch.setattr(services, "_compose_env", lambda: {})
    monkeypatch.setattr(services.subprocess, "run", lambda *a, **k: SimpleNamespace(returncode=0, stdout="", stderr=""))
    defn = SimpleNamespace(name="mysql", dir=tmp_path, compose="compose.yaml", container="slt-mysql")
    monkeypatch.setattr(services.drivers, "hook", lambda d, name: None)
    phases = []
    with pytest.raises(services.ServiceError, match="did not finish within 10800s"):
        services._default_compose_up(defn, progress=lambda name, phase: phases.append(phase))
    assert seen["timeout"] == dockercmd.COMPOSE_UP_TIMEOUT_S
    assert seen["argv"][:2] == ["docker", "compose"] and "up" in seen["argv"] and "--wait" in seen["argv"]
    assert phases                                       # the phase feedback still reaches the caller


# ---------------------------------------------------------------- bounded lock waits

def test_a_waiter_for_the_provisioning_lock_gives_up_naming_it(monkeypatch, tmp_path):
    from livetest import services, stack
    monkeypatch.setattr(stack, "_LOCK_DIR", tmp_path / "locks")         # never the machine-wide lock dir
    monkeypatch.setattr(dockercmd, "LOCK_WAIT_S", 0.3)
    _, lock = services._provision_state_paths(tmp_path)
    assert str(lock).startswith(str(tmp_path))
    stop = _held_elsewhere(lock)
    try:
        box = _waits_at_most(lambda: services.ensure_provisioned_once(
            "slt-oracle", lambda: pytest.fail("provisioned"), state_dir=tmp_path))
    finally:
        stop()
    assert isinstance(box.get("error"), services.ServiceError), box
    assert "not acquired within 0s" in str(box["error"]) and str(lock) in str(box["error"])


def _held_elsewhere(path):
    """Hold ``path``'s lock from another thread (a real contending holder) until the returned stop() runs."""
    import threading
    got, done = threading.Event(), threading.Event()

    def hold():
        lock = FileLock(str(path))
        lock.acquire()
        got.set()
        done.wait(60)
        lock.release()

    t = threading.Thread(target=hold, daemon=True)
    t.start()
    assert got.wait(10)

    def stop():
        done.set()
        t.join(10)
    return stop


def _waits_at_most(fn, seconds=10):
    """Run ``fn`` in a thread; fail if it is still waiting after ``seconds`` (an unbounded wait)."""
    import threading
    box = {}

    def call():
        try:
            box["value"] = fn()
        except BaseException as e:      # noqa: BLE001 - handed back to the test
            box["error"] = e

    t = threading.Thread(target=call, daemon=True)
    t.start()
    t.join(seconds)
    assert not t.is_alive(), f"still waiting for the lock after {seconds}s"
    return box
