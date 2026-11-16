"""What the stack exposes on the host: the primary's JDWP debugger.

JDWP is opt-in (SLT_STRIIM_DEBUG=1) and published on 127.0.0.1 only.
"""
import re
from pathlib import Path

import pytest
import yaml

import livetest
from livetest import striim_provision as sp

REPO = Path(livetest.__file__).resolve().parents[3]
LIVE = REPO / "scripts" / "live"
INTEG = REPO / "scripts" / "integration"
STRIIM = LIVE / "services" / "striim"
ENTRYPOINT = STRIIM / "images" / "striim" / "files" / "entrypoint.sh"
DEBUG_OVERLAY = "compose.debug.yaml"
JDWP = "127.0.0.1:${SLT_STRIIM_DEBUG_HOST_PORT:-8787}:8787"


# ------------------------------------------------------------------ JDWP (opt-in, loopback only)
def test_jdwp_is_written_only_inside_the_debug_branch():
    lines = ENTRYPOINT.read_text().splitlines()
    jdwp = [i for i, line in enumerate(lines) if "Xrunjdwp" in line and not line.lstrip().startswith("#")]
    assert len(jdwp) == 1, jdwp
    opens = [i for i, line in enumerate(lines[:jdwp[0]])
             if re.match(r'\s*if \[ "\$\{STRIIM_DEBUG:-0\}" = "1" \]; then\s*$', line)]
    assert opens, "the JDWP line must sit inside `if [ \"${STRIIM_DEBUG:-0}\" = \"1\" ]; then`"
    between = lines[opens[-1] + 1:jdwp[0]]
    assert not [line for line in between if re.match(r"\s*(fi|else|elif)\b", line)], between
    close = next(i for i in range(jdwp[0] + 1, len(lines)) if re.match(r"\s*fi\s*$", lines[i]))
    # The anchor rewrite that appends the JMX agent stays unconditional: it runs with debug off too.
    anchor = next(i for i, line in enumerate(lines)
                  if "$JVM_DEBUG_OPTS $JAVA_AGENT \\\\|g'" in line and "$JAXP_OPTS" not in line)
    assert anchor > close


def test_the_base_compose_publishes_no_debugger_port():
    compose = yaml.safe_load((STRIIM / "compose.yaml").read_text())
    for name, svc in compose["services"].items():
        assert not [p for p in svc.get("ports") or [] if str(p).endswith(":8787")], name
        assert "STRIIM_DEBUG" not in (svc.get("environment") or {}), name


@pytest.mark.parametrize("value", [None, "", "0", "true", "yes", " 1"])
def test_the_debug_overlay_is_left_out_unless_the_switch_is_1(monkeypatch, value):
    if value is None:
        monkeypatch.delenv("SLT_STRIIM_DEBUG", raising=False)
    else:
        monkeypatch.setenv("SLT_STRIIM_DEBUG", value)
    assert DEBUG_OVERLAY not in sp._compose_files()


def test_slt_striim_debug_1_adds_the_overlay_publishing_jdwp_on_loopback(monkeypatch):
    monkeypatch.setenv("SLT_STRIIM_DEBUG", "1")
    files = sp._compose_files()
    assert files[files.index(DEBUG_OVERLAY) - 1] == "-f"
    overlay = yaml.safe_load((STRIIM / DEBUG_OVERLAY).read_text())
    assert set(overlay["services"]) == {"slt-striim"}
    primary = overlay["services"]["slt-striim"]
    assert primary["ports"] == [JDWP]
    assert primary["environment"] == {"STRIIM_DEBUG": "1"}


def test_slt_striim_debug_from_dotenv_is_honoured(monkeypatch):
    monkeypatch.delenv("SLT_STRIIM_DEBUG", raising=False)
    from livetest import paths
    monkeypatch.setattr(paths, "effective_env",
                        lambda env=None, *a, **k: {**(env or {}), "SLT_STRIIM_DEBUG": "1"})
    assert DEBUG_OVERLAY in sp._compose_files()


def test_debug_switch_is_a_known_setting():
    from livetest import paths
    assert "SLT_STRIIM_DEBUG" in paths.SERVICE_KEYS
