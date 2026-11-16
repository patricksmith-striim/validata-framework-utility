"""The integration engine's switches.

INT_SHARED_SERVICES is retired: it dropped the session lock while the YAML path still started
containers and a serial session's ${TID} stayed empty, so two sessions addressed the same tables.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest
from filelock import FileLock

from inttest import plugin as P

ROOT = Path(__file__).resolve().parents[3]


class _Cfg:
    option = types.SimpleNamespace(collectonly=False)


@pytest.fixture
def held(monkeypatch, tmp_path):
    """Another session holds a session lock this test owns (the surrounding run's is set aside)."""
    monkeypatch.setattr(P, "_session_lock", None)
    monkeypatch.delenv("PYTEST_XDIST_WORKER", raising=False)
    foreign = tmp_path / ".int-session.lock"
    monkeypatch.setattr(P, "_session_lock_path", lambda: foreign)
    other = FileLock(str(foreign), timeout=0)
    other.acquire()
    try:
        yield
    finally:
        other.release()


# --- INT_SHARED_SERVICES ---------------------------------------------------------------------

def test_int_shared_services_no_longer_skips_the_session_lock(held, monkeypatch):
    monkeypatch.delenv("INT_ALLOW_CONCURRENT_SESSIONS", raising=False)
    monkeypatch.setenv("INT_SHARED_SERVICES", "1")
    with pytest.raises(pytest.UsageError, match="another integration-test session"):
        P._acquire_session_lock(_Cfg())


def test_int_shared_services_is_read_and_documented_nowhere():
    """Its readers, its two doc passages, the .env.example line and the settings row are gone. The
    release notes and the upgrade guide say it was removed; tests may name it to prove that."""
    hits = []
    for path in sorted(ROOT.rglob("*")):
        rel = path.relative_to(ROOT)
        if (not path.is_file() or rel.parts[0] in (".git", ".venv-test") or "tests" in rel.parts
                or rel.name in ("RELEASE-NOTES.md", "UPGRADING.md") or path.suffix in (".pyc", ".jar")):
            continue
        try:
            text = path.read_text()
        except (UnicodeDecodeError, OSError):
            continue
        if "INT_SHARED_SERVICES" in text or "_shared_services(" in text:
            hits.append(str(rel))
    assert hits == []
