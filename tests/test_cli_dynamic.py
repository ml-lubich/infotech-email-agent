"""Dynamic CLI surface tests.

Discovers every command, subcommand, and option on its own — no
hand-maintained lists. When someone adds a new flag or a new
subcommand, this file tests it automatically. The only side effects we
allow are: parser construction and ``--help`` rendering. Anything that
would actually bind a port, spawn a process, or open a browser is
mocked at the module boundary.

Two CLIs are covered:

1. ``invoice_agent.cli`` — argparse-based ``invoice-intake`` binary.
2. ``invoice_agent_web.cli`` — Typer-based ``infotech-email-agent`` binary.

For (1) we walk ``argparse._actions``. For (2) we get the underlying
Click ``Group`` via ``typer.main.get_command`` and walk it recursively.
"""

from __future__ import annotations

import argparse
from typing import Any, Iterator

import click
import pytest
import typer
from typer.main import get_command
from typer.testing import CliRunner

from invoice_agent import cli as agent_cli
from invoice_agent_web import cli as web_cli


# --------------------------------------------------------------------------- #
# argparse CLI: invoice_agent.cli (`invoice-intake`)
# --------------------------------------------------------------------------- #


def _agent_parser() -> argparse.ArgumentParser:
    """Build the argparse parser without invoking ``main``.

    We stub ``parse_args`` for one call to capture the parser instance
    that ``_parse_args`` constructs internally.
    """
    captured: dict[str, argparse.ArgumentParser] = {}
    original_parse = argparse.ArgumentParser.parse_args

    def _capture(self: argparse.ArgumentParser, *a: Any, **kw: Any) -> Any:
        captured["parser"] = self
        # Return a benign Namespace so the caller does not crash.
        return argparse.Namespace()

    argparse.ArgumentParser.parse_args = _capture  # type: ignore[method-assign]
    try:
        agent_cli._parse_args(["--email", "x"])
    finally:
        argparse.ArgumentParser.parse_args = original_parse  # type: ignore[method-assign]
    return captured["parser"]


def _agent_options() -> list[argparse.Action]:
    """Every user-facing option on the argparse parser."""
    return [
        a for a in _agent_parser()._actions
        if a.option_strings  # skip positionals; parser has none today
    ]


def test_agent_cli_help_exits_zero(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``invoice-intake --help`` must render without exploding."""
    with pytest.raises(SystemExit) as exc:
        agent_cli._parse_args(["--help"])
    assert exc.value.code == 0
    captured = capsys.readouterr()
    assert "usage:" in captured.out.lower()


@pytest.mark.parametrize("action", _agent_options(), ids=lambda a: a.option_strings[0])
def test_agent_cli_every_option_has_help_text(action: argparse.Action) -> None:
    """Every discovered option must document itself; catches blank --help fields."""
    assert action.help, (
        f"option {action.option_strings} on invoice-intake has no help text"
    )


def test_agent_cli_main_help_returns_zero(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``main(['--help'])`` is the real entrypoint; argparse exits 0."""
    monkeypatch.setattr(agent_cli, "load_dotenv", lambda *a, **k: False)
    with pytest.raises(SystemExit) as exc:
        agent_cli.main(["--help"])
    assert exc.value.code == 0


# --------------------------------------------------------------------------- #
# Typer CLI: invoice_agent_web.cli (`infotech-email-agent`)
# --------------------------------------------------------------------------- #


def _walk_click(
    cmd: click.Command, path: tuple[str, ...] = ()
) -> Iterator[tuple[tuple[str, ...], click.Command]]:
    """Yield (invocation-path, command) for every command in the tree.

    The root group is yielded with an empty path so callers can ask for
    its ``--help`` via ``[..., '--help']``.
    """
    yield path, cmd
    if isinstance(cmd, click.Group):
        for name, sub in cmd.commands.items():
            yield from _walk_click(sub, path + (name,))


_WEB_ROOT: click.Command = get_command(web_cli.app)
_ALL_WEB_COMMANDS: list[tuple[tuple[str, ...], click.Command]] = list(_walk_click(_WEB_ROOT))


def _command_id(item: tuple[tuple[str, ...], click.Command]) -> str:
    path, _ = item
    return " ".join(path) if path else "<root>"


def _option_params(
    item: tuple[tuple[str, ...], click.Command],
) -> list[tuple[tuple[str, ...], click.Option]]:
    """Yield (invocation-path, option) for every non-help option."""
    path, cmd = item
    out: list[tuple[tuple[str, ...], click.Option]] = []
    for param in cmd.params:
        if not isinstance(param, click.Option):
            continue
        if param.name == "help":
            continue
        out.append((path, param))
    return out


_ALL_WEB_OPTIONS: list[tuple[tuple[str, ...], click.Option]] = [
    pair for item in _ALL_WEB_COMMANDS for pair in _option_params(item)
]


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def neutralized_web(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub everything that would touch the real OS / network."""
    import sys

    class _StubUvicorn:
        @staticmethod
        def run(*_a: Any, **_kw: Any) -> None:
            return None

    monkeypatch.setitem(sys.modules, "uvicorn", _StubUvicorn())
    monkeypatch.setattr(web_cli.webbrowser, "open", lambda _u: True)
    monkeypatch.setattr(web_cli.time, "sleep", lambda _s: None)
    monkeypatch.setattr(web_cli, "_build_frontend", lambda force=False: None)
    monkeypatch.setattr(web_cli.subprocess, "call", lambda *_a, **_kw: 0)
    # Background lifecycle: never actually fork or signal anything.
    monkeypatch.setattr(web_cli, "_spawn_background_server", lambda host, port: 4242)
    monkeypatch.setattr(web_cli, "_read_pidfile", lambda: None)
    monkeypatch.setattr(web_cli, "_pid_alive", lambda _pid: True)
    monkeypatch.setattr(web_cli, "_terminate_pid", lambda _pid, timeout_s=10.0: True)
    monkeypatch.setattr(web_cli, "load_dotenv", lambda *a, **k: True)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-dynamic")


# --- discovery sanity ------------------------------------------------------- #


def test_web_cli_discovery_found_commands() -> None:
    """If discovery yields nothing, the rest of this file is a no-op fraud."""
    assert len(_ALL_WEB_COMMANDS) > 1, "expected root + at least one subcommand"
    names = {" ".join(p) for p, _ in _ALL_WEB_COMMANDS if p}
    # Spot-check a couple of commands we know exist; the parametrized
    # tests below are what actually exercise the full surface.
    assert "version" in names
    assert "config show" in names


# --- --help on every (sub)command ------------------------------------------ #


@pytest.mark.parametrize("item", _ALL_WEB_COMMANDS, ids=_command_id)
def test_web_cli_help_renders_for_every_command(
    runner: CliRunner, item: tuple[tuple[str, ...], click.Command]
) -> None:
    path, _cmd = item
    result = runner.invoke(web_cli.app, [*path, "--help"])
    assert result.exit_code == 0, (
        f"`{' '.join(path) or '<root>'} --help` failed:\n{result.output}"
    )
    assert "Usage:" in result.output


@pytest.mark.parametrize("item", _ALL_WEB_COMMANDS, ids=_command_id)
def test_web_cli_short_help_alias_works(
    runner: CliRunner, item: tuple[tuple[str, ...], click.Command]
) -> None:
    """The root configures ``-h`` as a help alias; verify it propagates."""
    path, _cmd = item
    result = runner.invoke(web_cli.app, [*path, "-h"])
    assert result.exit_code == 0, (
        f"`{' '.join(path) or '<root>'} -h` failed:\n{result.output}"
    )


# --- every option declares help text + a known type ------------------------ #


@pytest.mark.parametrize(
    "item",
    _ALL_WEB_OPTIONS,
    ids=lambda pair: f"{' '.join(pair[0]) or '<root>'}::{pair[1].opts[0]}",
)
def test_web_cli_every_option_is_well_formed(
    item: tuple[tuple[str, ...], click.Option],
) -> None:
    path, opt = item
    where = " ".join(path) or "<root>"
    assert opt.opts, f"{where}::{opt.name} has no option strings"
    assert opt.help, f"{where}::{opt.opts[0]} has no help text"
    # Click resolves a ParamType for every option; missing one means
    # someone passed a non-callable default that confuses Typer.
    assert opt.type is not None, f"{where}::{opt.opts[0]} has no type"


# --- invocation smoke: pure / read-only commands --------------------------- #
#
# These commands do not bind a port or spawn a process; they only print.
# We discover them from the live tree but pin the safe ones explicitly so
# we don't accidentally invoke `up` / `start` / `dev` in CI.

_SAFE_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("version",),
    ("doctor",),
    ("status",),
    ("config", "show"),
    ("config", "path"),
)


@pytest.mark.parametrize("argv", _SAFE_COMMANDS, ids=lambda a: " ".join(a))
def test_web_cli_safe_commands_run_without_error(
    runner: CliRunner,
    neutralized_web: None,
    argv: tuple[str, ...],
) -> None:
    """Each read-only command runs end-to-end with mocked side effects.

    `status` exits 3 when no PID file exists; that is a documented
    success path, not an error, so we accept it.
    """
    # Ensure each requested command actually exists in the discovered tree
    # (catches drift if someone renames a subcommand).
    discovered = {p for p, _ in _ALL_WEB_COMMANDS if p}
    assert argv in discovered, (
        f"safe-command {argv!r} no longer exists; update _SAFE_COMMANDS"
    )
    result = runner.invoke(web_cli.app, list(argv))
    allowed = {0} | ({3} if argv == ("status",) else set())
    assert result.exit_code in allowed, (
        f"`{' '.join(argv)}` exited {result.exit_code}:\n{result.output}"
    )


# --- invocation smoke: commands that would bind / spawn -------------------- #
#
# `up`, `start`, `restart`, `dev` reach into uvicorn / the process tree.
# With `neutralized_web` they are reduced to "build banner + pretend to
# serve". We invoke each with `--no-browser` and an absurd port to prove
# option parsing accepts the documented flags without raising.


_SERVER_COMMANDS: tuple[tuple[str, ...], ...] = (
    ("up", "--no-browser", "--port", "65535"),
    ("start", "--no-browser", "--port", "65535"),
    ("restart", "--no-browser", "--port", "65535"),
    ("dev", "--port", "65535"),
)


@pytest.mark.parametrize("argv", _SERVER_COMMANDS, ids=lambda a: a[0])
def test_web_cli_server_commands_parse_and_dispatch(
    runner: CliRunner,
    neutralized_web: None,
    argv: tuple[str, ...],
) -> None:
    result = runner.invoke(web_cli.app, list(argv))
    assert result.exit_code == 0, (
        f"`{' '.join(argv)}` exited {result.exit_code}:\n{result.output}"
    )


# --- root with no args dispatches to `up` ---------------------------------- #


def test_web_cli_root_no_args_invokes_up(
    runner: CliRunner, neutralized_web: None
) -> None:
    """The root callback's contract: no subcommand ⇒ ``up``.

    Validated by checking that uvicorn would have been called. We
    re-stub uvicorn with a recording version for this single test.
    """
    import sys

    calls: list[dict[str, Any]] = []

    class _Recorder:
        @staticmethod
        def run(app_path: str, **kwargs: Any) -> None:
            calls.append({"app": app_path, **kwargs})

    sys.modules["uvicorn"] = _Recorder()  # type: ignore[assignment]
    result = runner.invoke(web_cli.app, [])
    assert result.exit_code == 0, result.output
    assert calls, "root with no args did not dispatch to `up` (uvicorn never called)"
    assert calls[0]["app"] == "invoice_agent_web.main:app"
