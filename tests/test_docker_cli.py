"""Tests for the ``infotech-email-agent docker`` subgroup.

We never exec real docker — every test patches ``shutil.which`` and
``subprocess.call`` so the suite is hermetic.
"""

from __future__ import annotations

from typing import Any

import pytest
from typer.testing import CliRunner

from invoice_agent_web import cli as web_cli


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def fake_docker(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Pretend `docker` is on PATH and capture every compose invocation."""
    captured: dict[str, list[Any]] = {"calls": [], "envs": []}

    def _which(name: str) -> str | None:
        if name == "docker":
            return "/usr/local/bin/docker"
        return None

    def _call(cmd: list[str], **kw: Any) -> int:
        captured["calls"].append(cmd)
        captured["envs"].append(kw.get("env"))
        return 0

    monkeypatch.setattr(web_cli.shutil, "which", _which)
    monkeypatch.setattr(web_cli.subprocess, "call", _call)
    return captured


def test_docker_help_lists_subcommands(runner: CliRunner) -> None:
    result = runner.invoke(web_cli.app, ["docker", "--help"])
    assert result.exit_code == 0, result.output
    for sub in ("up", "down", "restart", "status", "logs"):
        assert sub in result.output


def test_docker_up_calls_compose_up_d(
    runner: CliRunner, fake_docker: dict[str, list[Any]]
) -> None:
    result = runner.invoke(web_cli.app, ["docker", "up"])
    assert result.exit_code == 0, result.output
    assert any(
        cmd[:2] == ["/usr/local/bin/docker", "compose"] and "up" in cmd and "-d" in cmd
        for cmd in fake_docker["calls"]
    )


def test_docker_up_passes_host_port_via_env(
    runner: CliRunner, fake_docker: dict[str, list[Any]]
) -> None:
    result = runner.invoke(web_cli.app, ["docker", "up", "--port", "9000"])
    assert result.exit_code == 0, result.output
    # First (and only) compose call should include HOST_PORT in its env.
    env = fake_docker["envs"][0]
    assert env is not None and env.get("HOST_PORT") == "9000"


def test_docker_down_calls_compose_down(
    runner: CliRunner, fake_docker: dict[str, list[Any]]
) -> None:
    result = runner.invoke(web_cli.app, ["docker", "down"])
    assert result.exit_code == 0, result.output
    assert fake_docker["calls"][-1][-1] == "down"


def test_docker_restart_calls_down_then_up(
    runner: CliRunner, fake_docker: dict[str, list[Any]]
) -> None:
    result = runner.invoke(web_cli.app, ["docker", "restart"])
    assert result.exit_code == 0, result.output
    sequence = [cmd[-1] if cmd[-1] in ("down",) else next(
        (a for a in cmd if a == "up"), None
    ) for cmd in fake_docker["calls"]]
    # First a `down`, then an `up`.
    assert "down" in sequence
    assert "up" in sequence
    assert sequence.index("down") < sequence.index("up")


def test_docker_logs_uses_follow_and_tail(
    runner: CliRunner, fake_docker: dict[str, list[Any]]
) -> None:
    result = runner.invoke(web_cli.app, ["docker", "logs", "--tail", "50"])
    assert result.exit_code == 0, result.output
    cmd = fake_docker["calls"][-1]
    assert "logs" in cmd and "-f" in cmd and "agent" in cmd
    assert "50" in cmd


def test_docker_status_calls_compose_ps(
    runner: CliRunner, fake_docker: dict[str, list[Any]]
) -> None:
    result = runner.invoke(web_cli.app, ["docker", "status"])
    assert result.exit_code == 0, result.output
    assert fake_docker["calls"][-1][-1] == "ps"


def test_docker_up_errors_when_docker_missing(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(web_cli.shutil, "which", lambda _name: None)
    result = runner.invoke(web_cli.app, ["docker", "up"])
    assert result.exit_code == 2, result.output
    assert "docker" in result.output.lower()
