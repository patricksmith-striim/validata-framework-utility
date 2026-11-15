"""Bounded Docker (and local probe) commands, shared by the live and integration engines.

A hung Docker daemon used to hang whatever called it: a declaration holding the port's registry lock, a
session teardown, every polling read of a server file. Each command now has a named bound, and a command
that outlives it is stopped and answers like a failed one (exit status ``TIMED_OUT``, a stderr that says
so), so every caller's existing failure path reports it.

Builds whose output streams to the terminal (``striim_provision._stream``: the installer download, the
Striim image build) are the one exception: the user watches them progress and can stop them. The cluster's
own `compose up`/`down` stream too, but through ``stream`` below, under the compose bounds.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading

# `docker ps`, `inspect`, `images`, `cp` of a small file, `exec` of a read, a listing, a checksum or a
# removal, and local probes (`java -version`, `openssl`). The bound the ownership ledger has used for
# every one of its docker commands.
QUERY_TIMEOUT_S = 60.0
# `docker compose down -v`: stops each container (10 s grace by default, in parallel) and removes its
# volumes, which run to gigabytes for Oracle and SQL Server.
COMPOSE_DOWN_TIMEOUT_S = 600.0
# `docker compose up -d --wait [--build]`, and a service's post-up script: an image pull or build, then
# the healthcheck wait (up to about 17 minutes for the slowest native service, and about 2 hours for a
# Teradata boot under emulation, which the integration compose lock has waited out).
COMPOSE_UP_TIMEOUT_S = 10800.0
# A build whose output is captured, not streamed (a Maven OP/UDF build, the GoldenGate harness javac):
# a cold local Maven repository downloads its dependencies first.
BUILD_TIMEOUT_S = 3600.0
# How long a process waits for a bring-up lock another process holds: the holder's reset, bring-up and
# post-up are each bounded above, so a waiter outlives any holder that is still making progress.
LOCK_WAIT_S = COMPOSE_DOWN_TIMEOUT_S + 2 * COMPOSE_UP_TIMEOUT_S

TIMED_OUT = 124                 # the exit status `timeout(1)` uses


def timed_out_message(argv, timeout: float) -> str:
    shown = " ".join(str(a) for a in list(argv)[:3])
    hint = " (is the Docker daemon responding?)" if argv and str(argv[0]) == "docker" else ""
    return f"`{shown} ...` did not finish within {timeout:.0f}s and was stopped{hint}"


class DockerTimeout(subprocess.CalledProcessError):
    """A ``check=True`` command that outlived its bound. A CalledProcessError, so callers that already
    handle a failed command handle this one; its text says it timed out."""

    def __init__(self, argv, timeout, stdout=None):
        super().__init__(TIMED_OUT, argv, stdout, timed_out_message(argv, timeout))
        self.timeout = timeout

    def __str__(self):
        return self.stderr


def run(argv, *, timeout: float = QUERY_TIMEOUT_S, check: bool = False, capture: bool = True, **kw):
    """``subprocess.run`` with a bound. On expiry the command is killed and a CompletedProcess with exit
    status ``TIMED_OUT`` is returned (``check=True``: ``DockerTimeout`` is raised). ``capture=False``
    leaves stdout and stderr on the terminal. Missing binaries raise FileNotFoundError, as before."""
    if capture:
        kw.setdefault("capture_output", True)
        kw.setdefault("text", True)
    try:
        r = subprocess.run(argv, timeout=timeout, **kw)
    except subprocess.TimeoutExpired as e:
        if check:
            raise DockerTimeout(argv, timeout, e.stdout) from None
        return subprocess.CompletedProcess(argv, TIMED_OUT, "" if capture else None,
                                           timed_out_message(argv, timeout))
    if check and r.returncode:
        raise subprocess.CalledProcessError(r.returncode, argv, r.stdout, r.stderr)
    return r


def _kill_group(proc) -> None:
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError):
        pass


def stream(argv, on_line, *, timeout: float, env=None, cwd=None) -> int:
    """Run ``argv`` with stdout and stderr merged, handing each line to ``on_line``; return its exit status,
    or ``TIMED_OUT`` when it outlived ``timeout``. The command runs in its own process group, killed whole on
    expiry and on any exception (Ctrl-C included): `docker compose` runs as a child of `docker`, and either
    can hold the output pipe open. ``cwd`` is the directory it runs in (None: this process's)."""
    proc = subprocess.Popen(argv, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=1,
                            env=env, cwd=cwd, start_new_session=True)
    fired = threading.Event()

    def expire():
        fired.set()
        _kill_group(proc)

    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    try:
        for line in proc.stdout:
            on_line(line)
        proc.wait()
    except BaseException:
        _kill_group(proc)
        raise
    finally:
        timer.cancel()
        if proc.poll() is None:
            _kill_group(proc)
        proc.wait()
        proc.stdout.close()
    return TIMED_OUT if fired.is_set() else proc.returncode
