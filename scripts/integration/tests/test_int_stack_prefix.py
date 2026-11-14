"""INT_STACK_PREFIX is validated as SLT_STACK_PREFIX is.

The prefix names compose projects, container names and the state files in the state directory. It was
used as given, so `../x` reached a filename (`.../x-int-session.lock`) and a trailing newline reached
compose. Both now raise, naming the setting, before anything is created.
"""
from __future__ import annotations

import pytest

from inttest import services

COMPOSE = """\
services:
  pg:
    container_name: ${INT_STACK_PREFIX:+${INT_STACK_PREFIX}-}int-postgres
"""


@pytest.mark.parametrize("bad", ["alt\n", "../x", "a/b", "Alt", "-alt", "alt_x", "alt stack", " alt"])
def test_an_invalid_int_stack_prefix_is_refused_by_state_name(bad):
    with pytest.raises(services.StackPrefixError, match="INT_STACK_PREFIX"):
        services.state_name(".int-session.lock", {"INT_STACK_PREFIX": bad})


@pytest.mark.parametrize("bad", ["alt\n", "../x"])
def test_an_invalid_int_stack_prefix_is_refused_by_declared_containers(tmp_path, bad):
    compose = tmp_path / "compose.yaml"
    compose.write_text(COMPOSE)
    with pytest.raises(services.StackPrefixError, match="INT_STACK_PREFIX"):
        services.declared_containers([compose], env={"INT_STACK_PREFIX": bad})


@pytest.mark.parametrize("good, name", [("alt", ".alt-int-session.lock"), ("x1-b2", ".x1-b2-int-session.lock"),
                                        ("", ".int-session.lock"), ("  ", ".int-session.lock")])
def test_a_valid_or_blank_int_stack_prefix_scopes_as_before(good, name):
    assert services.state_name(".int-session.lock", {"INT_STACK_PREFIX": good}) == name


def test_declared_containers_keeps_a_valid_prefix(tmp_path):
    compose = tmp_path / "compose.yaml"
    compose.write_text(COMPOSE)
    assert services.declared_containers([compose], env={"INT_STACK_PREFIX": "alt"}) == ["alt-int-postgres"]
