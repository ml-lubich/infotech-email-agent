"""Tests for the ``infotech-email-agent`` Typer CLI.

These tests pin the public help surface (so docs and examples cannot
silently drift from the binary) and validate the deterministic command
paths. Heavyweight side effects — uvicorn, the bun build, browser
launch, sleeping in dev — are patched out; we never bind a port from
the test suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from invoice_agent_web import cli as web_cli


@pytest.fixture
def runner() -> CliRunner:
    # Newer click merges stderr into stdout; we just read result.output.
    return CliRunner()


@pytest.fixture
def no_side_effects(monkeypatch: pytest.MonkeyPatch) -> dict[str, list[Any]]:
    """Neutralize uvicorn, browser, sleep, and bun build for the test run."""
    calls: dict[str, list[Any]] = {
        "uvicorn": [],
        "browser": [],
        "build": [],
        "subprocess": [],
    }

    class _StubUvicorn:
        @staticmethod
        def run(app_path: str, **kwargs: Any) -> None:
            calls["uvicorn"].append({"app": app_path, **kwargs})

    monkeypatch.setitem(
        __import__("sys").modules, "uvicorn", _StubUvicorn()
    )
    monkeypatch.setattr(web_cli.webbrowser, "open", lambda url: calls["browser"].append(url) or True)
    monkeypatch.setattr(web_cli.time, "sleep", lambda _s: None)

    def _fake_build(force: bool = False) -> None:
        calls["build"].append({"force": force})

    monkeypatch.setattr(web_cli, "_build_frontend", _fake_build)

    def _fake_subprocess_call(cmd: list[str], **_kw: Any) -> int:
        calls["subprocess"].append(cmd)
        return 0

    monkeypatch.setattr(web_cli.subprocess, "call", _fake_subprocess_call)
    return calls


# --------------------------------------------------------------------------- #
# --help surface (pins the documented examples)
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("flag", ["--help"])
def test_root_help_shows_subcommands_and_examples(
    runner: CliRunner, flag: str
) -> None:
    result = runner.invoke(web_cli.app, [flag])
    assert result.exit_code == 0, result.output
    out = result.output
    # All four subcommands must be advertised on the root --help.
    for cmd in ("up", "start", "stop", "restart", "status", "dev", "doctor", "version"):
        assert cmd in out, f"missing '{cmd}' in --help output"
    # Examples listed at the top must mention the binary name.
    assert "infotech-email-agent" in out
    assert "OPENAI_API_KEY" in out
    # Linked doc reference (RUNBOOK is the canonical operator doc).
    assert "RUNBOOK" in out


@pytest.mark.parametrize(
    "subcommand,must_contain",
    [
        ("up", ["--port", "--no-browser", "--rebuild", "Examples:", "INVOICE_WEB_HOST"]),
        ("start", ["--port", "--no-browser", "--rebuild", "out/web/server.pid"]),
        ("stop", ["SIGTERM", "out/web/server.pid"]),
        ("restart", ["--port", "--rebuild"]),
        ("status", ["PID file", "log file"]),
        ("dev", ["Terminal 1", "Terminal 2", "bun run dev", "5173"]),
        ("doctor", ["Example:", "OPENAI_API_KEY", "frontend bundle"]),
        ("version", ["package version"]),
    ],
)
def test_subcommand_help_contains_examples(
    runner: CliRunner, subcommand: str, must_contain: list[str]
) -> None:
    result = runner.invoke(web_cli.app, [subcommand, "--help"])
    assert result.exit_code == 0, result.output
    for token in must_contain:
        assert token in result.output, (
            f"`{subcommand} --help` is missing '{token}'\n--- got ---\n{result.output}"
        )


# --------------------------------------------------------------------------- #
# version
# --------------------------------------------------------------------------- #


def test_version_prints_binary_and_semver(runner: CliRunner) -> None:
    result = runner.invoke(web_cli.app, ["version"])
    assert result.exit_code == 0
    assert "infotech-email-agent" in result.output
    # Either a real version or the documented fallback message.
    assert any(
        token in result.output for token in (".", "unknown")
    ), result.output


# --------------------------------------------------------------------------- #
# doctor
# --------------------------------------------------------------------------- #


def test_doctor_reports_status_lines(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-doctor")
    monkeypatch.delenv("INVOICE_PIPELINE_LLM_DISABLED", raising=False)
    result = runner.invoke(web_cli.app, ["doctor"])
    assert result.exit_code == 0, result.output
    out = result.output
    # ASCII banner present (the spaced subtitle line below the box-drawing).
    assert "I N V O I C E" in out
    for label in ("OPENAI_API_KEY", "LLM shots", "python", "bun", "frontend dist", "runs dir"):
        assert label in out, f"doctor output missing '{label}': {out}"


def test_doctor_reports_disabled_llm_when_env_set(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("INVOICE_PIPELINE_LLM_DISABLED", "1")
    result = runner.invoke(web_cli.app, ["doctor"])
    assert result.exit_code == 0
    assert "disabled" in result.output


# --------------------------------------------------------------------------- #
# up
# --------------------------------------------------------------------------- #


def test_up_exits_2_without_openai_key(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Block .env from re-injecting the key by no-op'ing load_dotenv.
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: False)
    result = runner.invoke(web_cli.app, ["up", "--no-browser"])
    assert result.exit_code == 2, result.output
    assert "OPENAI_API_KEY" in result.output


def test_up_invokes_uvicorn_with_requested_host_and_port(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    no_side_effects: dict[str, list[Any]],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-up")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    result = runner.invoke(
        web_cli.app, ["up", "--no-browser", "--port", "8765", "--host", "127.0.0.1"]
    )
    assert result.exit_code == 0, result.output
    assert no_side_effects["uvicorn"], "uvicorn.run was not invoked"
    invocation = no_side_effects["uvicorn"][0]
    assert invocation["app"] == "invoice_agent_web.main:app"
    assert invocation["host"] == "127.0.0.1"
    assert invocation["port"] == 8765
    assert invocation["reload"] is False
    # Browser open suppressed by --no-browser.
    assert no_side_effects["browser"] == []
    # Frontend build is invoked exactly once (force=False by default).
    assert no_side_effects["build"] == [{"force": False}]
    assert "Dashboard" in result.output
    assert "http://127.0.0.1:8765/" in result.output


def test_up_rebuild_flag_forces_frontend_build(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    no_side_effects: dict[str, list[Any]],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-rebuild")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    result = runner.invoke(
        web_cli.app, ["up", "--no-browser", "--rebuild", "--port", "8123"]
    )
    assert result.exit_code == 0, result.output
    assert no_side_effects["build"] == [{"force": True}]


def test_default_invocation_runs_up(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    no_side_effects: dict[str, list[Any]],
) -> None:
    """No subcommand -> root callback delegates to ``up`` with resolved defaults."""
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-default")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    result = runner.invoke(web_cli.app, [])
    assert result.exit_code == 0, result.output
    assert no_side_effects["uvicorn"], "uvicorn.run was not invoked"
    # Regression guard: the root callback must pass *resolved* defaults to
    # `up`, not Typer's OptionInfo sentinels (uvloop rejects the latter
    # with "TypeError: port must be a str, bytes or int").
    invocation = no_side_effects["uvicorn"][0]
    assert isinstance(invocation["host"], str)
    assert isinstance(invocation["port"], int)
    assert invocation["host"] == "127.0.0.1"
    assert invocation["port"] == 8000


# --------------------------------------------------------------------------- #
# dev
# --------------------------------------------------------------------------- #


def test_dev_starts_backend_with_reload(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    no_side_effects: dict[str, list[Any]],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dev")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    result = runner.invoke(web_cli.app, ["dev", "--port", "8101"])
    assert result.exit_code == 0, result.output
    assert no_side_effects["uvicorn"], "uvicorn.run was not invoked"
    invocation = no_side_effects["uvicorn"][0]
    assert invocation["reload"] is True
    assert invocation["port"] == 8101
    # Vite dev instructions printed.
    assert "bun run dev" in result.output
    assert "5173" in result.output


def test_dev_warns_without_openai_key(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    no_side_effects: dict[str, list[Any]],
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: False)
    result = runner.invoke(web_cli.app, ["dev"])
    assert result.exit_code == 0, result.output
    assert "OPENAI_API_KEY" in result.output


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def test_bundle_built_detects_index_html(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_dist = tmp_path / "dist"
    fake_dist.mkdir()
    monkeypatch.setattr(web_cli, "FRONTEND_DIST", fake_dist)
    assert web_cli._bundle_built() is False
    (fake_dist / "index.html").write_text("<!doctype html>", encoding="utf-8")
    assert web_cli._bundle_built() is True


def test_build_frontend_no_op_when_bundle_present(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_dist = tmp_path / "dist"
    fake_dist.mkdir()
    (fake_dist / "index.html").write_text("ok", encoding="utf-8")
    monkeypatch.setattr(web_cli, "FRONTEND_DIST", fake_dist)
    # If build were attempted, this fake bun would record a call.
    invoked: list[list[str]] = []
    monkeypatch.setattr(
        web_cli.subprocess, "call", lambda cmd, **_k: invoked.append(cmd) or 0
    )
    web_cli._build_frontend(force=False)
    assert invoked == []


def test_build_frontend_errors_without_bun(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    fake_dist = tmp_path / "dist"  # not built
    monkeypatch.setattr(web_cli, "FRONTEND_DIST", fake_dist)
    monkeypatch.setattr(web_cli, "_have_bun", lambda: None)
    import typer

    with pytest.raises(typer.Exit) as exc:
        web_cli._build_frontend(force=False)
    assert exc.value.exit_code == 2


# --------------------------------------------------------------------------- #
# lifecycle: start / stop / restart / status
# --------------------------------------------------------------------------- #


@pytest.fixture
def lifecycle_env(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> dict[str, Any]:
    """Sandbox the PID/log files and stub spawn/terminate so no real process runs."""
    pid_file = tmp_path / "server.pid"
    log_file = tmp_path / "server.log"
    monkeypatch.setattr(web_cli, "_RUNTIME_DIR", tmp_path)
    monkeypatch.setattr(web_cli, "_PID_FILE", pid_file)
    monkeypatch.setattr(web_cli, "_LOG_FILE", log_file)

    state: dict[str, Any] = {
        "spawn_calls": [],
        "terminate_calls": [],
        "alive_pids": set(),
        "next_pid": 4242,
    }

    def _fake_spawn(host: str, port: int) -> int:
        pid = int(state["next_pid"])
        state["next_pid"] = pid + 1
        state["spawn_calls"].append({"host": host, "port": port, "pid": pid})
        state["alive_pids"].add(pid)
        pid_file.write_text(f"{pid}\n")
        return pid

    def _fake_terminate(pid: int, timeout_s: float = 10.0) -> bool:
        state["terminate_calls"].append(pid)
        state["alive_pids"].discard(pid)
        return True

    def _fake_alive(pid: int) -> bool:
        return pid in state["alive_pids"]

    monkeypatch.setattr(web_cli, "_spawn_background_server", _fake_spawn)
    monkeypatch.setattr(web_cli, "_terminate_pid", _fake_terminate)
    monkeypatch.setattr(web_cli, "_pid_alive", _fake_alive)
    monkeypatch.setattr(web_cli, "_build_frontend", lambda force=False: None)
    monkeypatch.setattr(web_cli.webbrowser, "open", lambda url: True)
    monkeypatch.setattr(web_cli.time, "sleep", lambda _s: None)
    state["pid_file"] = pid_file
    state["log_file"] = log_file
    return state


def test_status_exits_3_when_no_pidfile(
    runner: CliRunner, lifecycle_env: dict[str, Any]
) -> None:
    result = runner.invoke(web_cli.app, ["status"])
    assert result.exit_code == 3, result.output
    assert "not running" in result.output


def test_stop_is_noop_when_no_pidfile(
    runner: CliRunner, lifecycle_env: dict[str, Any]
) -> None:
    result = runner.invoke(web_cli.app, ["stop"])
    assert result.exit_code == 0, result.output
    assert "no running server" in result.output
    assert lifecycle_env["terminate_calls"] == []


def test_start_writes_pidfile_and_status_reports_running(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_env: dict[str, Any],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-start")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)

    result = runner.invoke(
        web_cli.app, ["start", "--no-browser", "--port", "8765"]
    )
    assert result.exit_code == 0, result.output
    assert lifecycle_env["spawn_calls"] == [
        {"host": "127.0.0.1", "port": 8765, "pid": 4242}
    ]
    assert lifecycle_env["pid_file"].is_file()
    assert lifecycle_env["pid_file"].read_text().strip() == "4242"
    assert "started" in result.output
    assert "8765" in result.output

    status_res = runner.invoke(web_cli.app, ["status"])
    assert status_res.exit_code == 0, status_res.output
    assert "running" in status_res.output
    assert "4242" in status_res.output


def test_start_refuses_when_pidfile_already_present(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_env: dict[str, Any],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dupe")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    # Pre-seed a live PID.
    lifecycle_env["pid_file"].write_text("9999\n")
    lifecycle_env["alive_pids"].add(9999)

    result = runner.invoke(web_cli.app, ["start", "--no-browser"])
    assert result.exit_code == 1, result.output
    assert "already running" in result.output
    assert lifecycle_env["spawn_calls"] == []


def test_start_exits_2_without_openai_key(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_env: dict[str, Any],
) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: False)
    result = runner.invoke(web_cli.app, ["start", "--no-browser"])
    assert result.exit_code == 2, result.output
    assert "OPENAI_API_KEY" in result.output
    assert lifecycle_env["spawn_calls"] == []


def test_stop_terminates_running_server_and_removes_pidfile(
    runner: CliRunner, lifecycle_env: dict[str, Any]
) -> None:
    lifecycle_env["pid_file"].write_text("4242\n")
    lifecycle_env["alive_pids"].add(4242)

    result = runner.invoke(web_cli.app, ["stop"])
    assert result.exit_code == 0, result.output
    assert lifecycle_env["terminate_calls"] == [4242]
    assert not lifecycle_env["pid_file"].exists()
    assert "stopped" in result.output


def test_restart_stops_then_starts(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_env: dict[str, Any],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-restart")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    lifecycle_env["pid_file"].write_text("1111\n")
    lifecycle_env["alive_pids"].add(1111)

    result = runner.invoke(web_cli.app, ["restart", "--port", "9001"])
    assert result.exit_code == 0, result.output
    assert lifecycle_env["terminate_calls"] == [1111]
    assert len(lifecycle_env["spawn_calls"]) == 1
    assert lifecycle_env["spawn_calls"][0]["port"] == 9001
    # Fresh PID was written, not the old one.
    assert lifecycle_env["pid_file"].read_text().strip() == "4242"


def test_restart_works_when_nothing_was_running(
    runner: CliRunner,
    monkeypatch: pytest.MonkeyPatch,
    lifecycle_env: dict[str, Any],
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-cold-restart")
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    result = runner.invoke(web_cli.app, ["restart"])
    assert result.exit_code == 0, result.output
    assert lifecycle_env["terminate_calls"] == []
    assert len(lifecycle_env["spawn_calls"]) == 1


def test_read_pidfile_clears_stale_entry(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("424242\n")
    monkeypatch.setattr(web_cli, "_PID_FILE", pid_file)
    monkeypatch.setattr(web_cli, "_pid_alive", lambda _pid: False)
    assert web_cli._read_pidfile() is None
    assert not pid_file.exists(), "stale pidfile should be removed"


def test_read_pidfile_ignores_garbage(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pid_file = tmp_path / "server.pid"
    pid_file.write_text("not-a-pid\n")
    monkeypatch.setattr(web_cli, "_PID_FILE", pid_file)
    assert web_cli._read_pidfile() is None


def test_pid_alive_returns_false_for_nonpositive() -> None:
    assert web_cli._pid_alive(0) is False
    assert web_cli._pid_alive(-1) is False


# --------------------------------------------------------------------------- #
# config show / config path
# --------------------------------------------------------------------------- #


def test_config_show_prints_paths_and_resolved_settings(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-cfg")
    result = runner.invoke(web_cli.app, ["config", "show"])
    assert result.exit_code == 0, result.output
    out = result.output
    for label in (
        "Config sources",
        "global TOML",
        "project TOML",
        "OPENAI_API_KEY",
        "Resolved settings",
        "agent_model",
        "extract_model",
        "web_port",
        "llm_disabled",
    ):
        assert label in out, f"config show missing '{label}': {out}"


def test_config_path_is_machine_friendly(runner: CliRunner) -> None:
    result = runner.invoke(web_cli.app, ["config", "path"])
    assert result.exit_code == 0, result.output
    lines = [ln for ln in result.output.strip().splitlines() if ln]
    assert any(ln.startswith("global=") for ln in lines)
    assert any(ln.startswith("project=") for ln in lines)
