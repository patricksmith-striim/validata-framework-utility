"""The integration tier's `compose up` publishes its ports where the harness says (SLT_BIND_ADDRESS).

Docker is mocked: no container is started. The value itself is livetest.bindaddr's (tested in the
live suite); this pins that the integration bring-up hands it to compose.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from inttest import services
from livetest import bindaddr

def _lane(tmp_path, monkeypatch):
    """The framework's own mysql service, exclusive ownership: compose_up goes straight to `up`."""
    monkeypatch.delenv("SLT_INT_SERVICES_DIR", raising=False)
    monkeypatch.setenv("SLT_STATE_DIR", str(tmp_path))
    monkeypatch.setenv("SLT_INFRA_OWNERSHIP", "exclusive")


def _capture(monkeypatch):
    seen = []

    def run(argv, **kw):
        seen.append((list(argv), kw.get("env")))
        return SimpleNamespace(returncode=0, stdout="", stderr="")
    monkeypatch.setattr(services.subprocess, "run", run)
    return seen


def test_compose_up_publishes_on_loopback_and_the_gateway_by_default(tmp_path, monkeypatch):
    _lane(tmp_path, monkeypatch)
    monkeypatch.delenv("SLT_BIND_ADDRESS", raising=False)
    monkeypatch.setattr(bindaddr, "_cached_gateway", lambda: "172.17.0.1")
    seen = _capture(monkeypatch)
    services.compose_up("mysql", MagicMock())
    ups = [env for argv, env in seen if argv[:2] == ["docker", "compose"] and "up" in argv]
    assert ups and ups[0]["SLT_BIND_ADDRESS"] == "127.0.0.1"
    assert ups[0]["SLT_BIND_GATEWAY"] == "172.17.0.1"


def test_compose_up_publishes_only_on_an_explicit_bind_address(tmp_path, monkeypatch):
    _lane(tmp_path, monkeypatch)
    monkeypatch.setenv("SLT_BIND_ADDRESS", "0.0.0.0")
    monkeypatch.setattr(bindaddr, "_cached_gateway", lambda: "172.17.0.1")
    seen = _capture(monkeypatch)
    services.compose_up("mysql", MagicMock())
    ups = [env for argv, env in seen if argv[:2] == ["docker", "compose"] and "up" in argv]
    assert ups and ups[0]["SLT_BIND_ADDRESS"] == ups[0]["SLT_BIND_GATEWAY"] == "0.0.0.0"


def test_bind_address_is_a_known_integration_setting():
    from inttest import paths
    assert "SLT_BIND_ADDRESS" in paths.SERVICE_KEYS
