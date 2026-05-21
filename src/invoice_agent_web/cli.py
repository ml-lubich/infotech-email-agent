"""Typer CLI for the InfoTech Email Agent dashboard.

Console-script entrypoint: ``infotech-email-agent``.

Subcommands
-----------
* ``up`` (default)  Build the React bundle if needed, then serve the API
                    + the bundle on a single port (foreground). Opens the
                    browser.
* ``start``         Same as ``up`` but detaches the server into the
                    background and writes a PID file so ``stop`` / ``restart``
                    can manage it.
* ``stop``          Stop a background server started with ``start``.
* ``restart``       ``stop`` then ``start`` (preserves host/port flags).
* ``status``        Report whether a background server is running.
* ``dev``           Run the FastAPI backend with reload on one port and
                    print Vite-dev instructions for the other.
* ``doctor``        Print env / dependency diagnostics.
* ``version``       Print the package version.
"""

from __future__ import annotations

import errno
import os
import shutil
import signal
import subprocess
import sys
import time
import webbrowser
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path
from typing import Iterable

import typer
from dotenv import load_dotenv

from invoice_agent.config import (
    APP_NAME,
    global_config_path,
    load_settings,
    project_config_paths,
)
from invoice_agent_web.main import FRONTEND_DIST, REPO_ROOT


def _default_host() -> str:
    """Default bind host = TOML/env merged settings (env still wins)."""
    return load_settings().web_host


def _default_port() -> int:
    return load_settings().web_port

app = typer.Typer(
    name="infotech-email-agent",
    add_completion=False,
    rich_markup_mode="rich",
    # Accept -h as an alias for --help on the root command and on every
    # subcommand. Click's default only wires --help; -h is a near-universal
    # convention so we register both.
    context_settings={"help_option_names": ["-h", "--help"]},
    help=(
        "Launch the InfoTech invoice-intake dashboard "
        "(FastAPI + React, single port).\n\n"
        "Quick start:\n\n"
        "  $ infotech-email-agent             # build + serve + open browser\n"
        "  $ infotech-email-agent up -p 9000  # custom port\n"
        "  $ infotech-email-agent start       # run in background (PID file)\n"
        "  $ infotech-email-agent status      # is the background server up?\n"
        "  $ infotech-email-agent restart     # bounce the background server\n"
        "  $ infotech-email-agent stop        # stop the background server\n"
        "  $ infotech-email-agent dev         # backend with auto-reload\n"
        "  $ infotech-email-agent doctor      # env / dependency diagnostics\n"
        "  $ infotech-email-agent config show # show merged config + file paths\n"
        "  $ infotech-email-agent version     # print package version\n\n"
        "Reads OPENAI_API_KEY from .env. See docs/RUNBOOK.md."
    ),
    no_args_is_help=False,
)


# --------------------------------------------------------------------------- #
# branding
# --------------------------------------------------------------------------- #

_BANNER = r"""
   ___        __      _____         _       ___                _ _
  |_ _|_ __  / _| ___|_   _|__  ___| |__   | __|_ __  __ _ __ _(_) |
   | || '_ \| |_ / _ \ | |/ _ \/ __| '_ \  | _|| '  \/ _` / _` | | |
  |___|_| |_|  _|\___/ |_|\___/\___|_| |_| |___|_|_|_\__,_\__,_|_|_|
            |_|
                A G E N T   ·   I N V O I C E   I N T A K E
"""

_C = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "cyan": "\033[36m",
    "blue": "\033[34m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "red": "\033[31m",
    "magenta": "\033[35m",
}


def _supports_color() -> bool:
    return sys.stdout.isatty() and os.getenv("NO_COLOR") is None


def _c(text: str, *codes: str) -> str:
    if not _supports_color():
        return text
    prefix = "".join(_C[c] for c in codes if c in _C)
    return f"{prefix}{text}{_C['reset']}"


def _print_banner() -> None:
    typer.echo(_c(_BANNER, "cyan", "bold"))
    typer.echo(
        _c(
            "  Vendor invoice intake · multi-shot pipeline · risk grading\n",
            "dim",
        )
    )


def _print_kv(label: str, value: str, ok: bool | None = None) -> None:
    dot = "•"
    color = "dim"
    if ok is True:
        dot, color = "✓", "green"
    elif ok is False:
        dot, color = "✗", "red"
    typer.echo(
        f"  {_c(dot, color)} {_c(label.ljust(18), 'dim')} {value}"
    )


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _bundle_built() -> bool:
    return (FRONTEND_DIST / "index.html").is_file()


def _have_bun() -> str | None:
    return shutil.which("bun")


def _build_frontend(force: bool = False) -> None:
    if _bundle_built() and not force:
        typer.echo(
            _c("  ✓ ", "green") + _c("frontend bundle present", "dim")
        )
        return
    bun = _have_bun()
    if bun is None:
        typer.secho(
            "ERROR: Bun is not installed (https://bun.sh). "
            "Install it or build the frontend manually with `npm` "
            "in src/frontend/.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    frontend_dir = REPO_ROOT / "src" / "frontend"
    if not (frontend_dir / "node_modules").is_dir():
        typer.echo(_c("  → installing frontend deps (bun install)…", "dim"))
        rc = subprocess.call([bun, "install"], cwd=frontend_dir)
        if rc != 0:
            raise typer.Exit(code=rc)

    typer.echo(_c("  → building frontend bundle (bun run build)…", "dim"))
    rc = subprocess.call([bun, "run", "build"], cwd=frontend_dir)
    if rc != 0:
        raise typer.Exit(code=rc)
    typer.echo(_c("  ✓ ", "green") + "frontend bundle built")


def _add_case(
    cases: dict[Path, Path | None], email_path: Path, pdf_path: Path | None
) -> None:
    current = cases.get(email_path)
    if pdf_path is not None:
        cases[email_path] = pdf_path
        return
    if current is None:
        cases[email_path] = None


def _scan_dir_cases(path: Path) -> list[Path]:
    email_here = path / "Email.json"
    if email_here.is_file():
        return [email_here.resolve()]
    emails: list[Path] = []
    for child in sorted(path.iterdir(), key=lambda p: p.name):
        if not child.is_dir():
            continue
        email = child / "Email.json"
        if email.is_file():
            emails.append(email.resolve())
    return emails


def _as_path_inputs(paths: Iterable[Path]) -> list[Path]:
    resolved: list[Path] = []
    for raw in paths:
        path = Path(raw).expanduser().resolve()
        if not path.exists():
            raise typer.BadParameter(f"input path does not exist: {raw}")
        resolved.append(path)
    return resolved


def discover_cases(paths: list[Path]) -> list[tuple[Path, Path | None]]:
    """Classify free-form file/folder inputs into runnable case tuples.

    Returns ``[(email_json_path, pdf_override_or_none), ...]``.
    """
    cases: dict[Path, Path | None] = {}
    for path in _as_path_inputs(paths):
        if path.is_dir():
            for email in _scan_dir_cases(path):
                _add_case(cases, email, None)
            continue
        suffix = path.suffix.lower()
        if suffix == ".json":
            _add_case(cases, path, None)
            continue
        if suffix == ".pdf":
            email = (path.parent / "Email.json").resolve()
            if not email.is_file():
                raise typer.BadParameter(f"no sibling Email.json for PDF: {path}")
            _add_case(cases, email, path)
            continue
        raise typer.BadParameter(f"unsupported input type: {path.name}")
    return sorted(cases.items(), key=lambda item: str(item[0]))


def _build_case_argv(
    email_path: Path, pdf_path: Path | None, out_dir: Path | None
) -> list[str]:
    argv = ["--email", str(email_path)]
    if pdf_path is not None:
        argv.extend(["--pdf", str(pdf_path)])
    if out_dir is not None:
        case_out = (out_dir / email_path.parent.name).resolve()
        argv.extend(["--out-dir", str(case_out)])
    return argv


def _set_no_llm_env() -> None:
    os.environ["INFOTECH_PIPELINE_LLM_DISABLED"] = "1"
    os.environ["INVOICE_PIPELINE_LLM_DISABLED"] = "1"


# --------------------------------------------------------------------------- #
# background lifecycle (start / stop / restart / status)
# --------------------------------------------------------------------------- #

# Single source of truth for the PID + log file of a backgrounded server.
# Lives under out/web/ alongside per-request case dirs so everything the
# server writes stays in one place.
_RUNTIME_DIR: Path = REPO_ROOT / "out" / "web"
_PID_FILE: Path = _RUNTIME_DIR / "server.pid"
_LOG_FILE: Path = _RUNTIME_DIR / "server.log"


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` is currently running."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except OSError as exc:
        # ESRCH = no such process; EPERM = process exists but we can't signal.
        return exc.errno == errno.EPERM
    return True


def _read_pidfile() -> int | None:
    """Return the PID stored in the pidfile, or None if missing/garbage/dead."""
    if not _PID_FILE.is_file():
        return None
    try:
        pid = int(_PID_FILE.read_text().strip())
    except (OSError, ValueError):
        return None
    if not _pid_alive(pid):
        # Stale file from a crashed server — remove it explicitly so the
        # next `start` does not refuse to launch.
        try:
            _PID_FILE.unlink()
        except OSError:
            pass
        return None
    return pid


def _spawn_background_server(host: str, port: int) -> int:
    """Spawn a detached uvicorn process and return its PID.

    The child process is placed in a new session so closing the parent
    terminal does not deliver SIGHUP to the server.
    """
    _RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
    log_handle = _LOG_FILE.open("ab", buffering=0)
    cmd = [
        sys.executable,
        "-m",
        "uvicorn",
        "invoice_agent_web.main:app",
        "--host",
        host,
        "--port",
        str(port),
        "--log-level",
        "info",
    ]
    # start_new_session detaches the child from the controlling TTY so it
    # survives the parent shell exiting.
    proc = subprocess.Popen(  # noqa: S603 - args are constants + validated ints
        cmd,
        cwd=str(REPO_ROOT),
        stdout=log_handle,
        stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
        close_fds=True,
    )
    _PID_FILE.write_text(f"{proc.pid}\n")
    return proc.pid


def _terminate_pid(pid: int, timeout_s: float = 10.0) -> bool:
    """SIGTERM then SIGKILL fallback. Returns True if the process is gone."""
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        if exc.errno == errno.ESRCH:
            return True
        # EPERM and friends are real failures we should surface.
        typer.secho(
            f"  ! could not signal pid {pid}: {exc}",
            fg=typer.colors.YELLOW,
            err=True,
        )
        return False
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        if not _pid_alive(pid):
            return True
        time.sleep(0.1)
    # Escalate — server refused to shut down within the grace period.
    try:
        os.kill(pid, signal.SIGKILL)
    except OSError:
        pass
    time.sleep(0.2)
    return not _pid_alive(pid)


# --------------------------------------------------------------------------- #
# commands
# --------------------------------------------------------------------------- #


@app.callback(invoke_without_command=True)
def _root(ctx: typer.Context) -> None:
    """Default action: ``up`` if no subcommand given."""
    if ctx.invoked_subcommand is None:
        # ctx.invoke does not resolve Typer's OptionInfo sentinels into
        # real values; pass the resolved env-aware defaults explicitly so
        # `host` and `port` arrive as `str` / `int`, not `OptionInfo`.
        ctx.invoke(
            up,
            host=_default_host(),
            port=_default_port(),
            no_browser=False,
            rebuild=False,
        )


@app.command(
    epilog=(
        "Examples:\n\n"
        "  infotech-email-agent up\n"
        "      Default: build the React bundle if missing, serve API + UI on\n"
        "      http://127.0.0.1:8000/, and open the browser.\n\n"
        "  infotech-email-agent up --port 9000 --no-browser\n"
        "      Serve on a custom port without launching a browser (handy for\n"
        "      remote tunnels and CI smoke runs).\n\n"
        "  infotech-email-agent up --rebuild\n"
        "      Force a fresh `bun run build` before serving.\n\n"
        "Environment:\n"
        "  OPENAI_API_KEY              required (loaded from .env)\n"
        "  INVOICE_PIPELINE_LLM_DISABLED=1   skip Pass 3 + Pass 4 LLM shots\n"
        "  INVOICE_WEB_HOST / _PORT    override --host / --port defaults\n"
        "  INVOICE_WEB_RUNS_DIR        where per-request case dirs land\n"
    ),
)
def up(
    host: str = typer.Option(
        _default_host(),
        "--host",
        help="Bind host.",
    ),
    port: int = typer.Option(
        _default_port(),
        "--port",
        "-p",
        help="Port to serve API + dashboard on.",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open the browser."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Rebuild the frontend bundle before serving."
    ),
) -> None:
    """Build (if needed) and serve the dashboard on one port."""
    load_dotenv(REPO_ROOT / ".env")
    _print_banner()

    has_key = bool(os.getenv("OPENAI_API_KEY"))
    llm_on = os.getenv("INVOICE_PIPELINE_LLM_DISABLED") != "1"
    _print_kv("OPENAI_API_KEY", "set" if has_key else "MISSING", ok=has_key)
    _print_kv(
        "LLM shots",
        "active (Pass 3 + Pass 4)" if llm_on else "disabled (deterministic only)",
        ok=llm_on,
    )
    _print_kv("Bun", _have_bun() or "not found", ok=bool(_have_bun()))
    _print_kv("Bundle", "built" if _bundle_built() else "missing", ok=_bundle_built())
    typer.echo()

    if not has_key:
        typer.secho(
            "ERROR: OPENAI_API_KEY is not set. Copy .env.example to .env "
            "and paste your key.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    _build_frontend(force=rebuild)

    url = f"http://{host}:{port}/"
    typer.echo()
    typer.echo(_c("  Dashboard ", "bold") + _c(url, "cyan", "bold"))
    typer.echo(_c(f"  API docs  http://{host}:{port}/docs", "dim"))
    typer.echo(_c("  Ctrl-C to stop\n", "dim"))

    if not no_browser:
        # Give uvicorn a moment to bind; opening immediately is fine.
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass

    import uvicorn

    uvicorn.run(
        "invoice_agent_web.main:app",
        host=host,
        port=port,
        reload=False,
        log_level="info",
    )


@app.command(
    epilog=(
        "Examples:\n\n"
        "  infotech-email-agent start\n"
        "      Build the frontend (if missing), spawn uvicorn in the\n"
        "      background, write the PID to out/web/server.pid, and stream\n"
        "      server logs to out/web/server.log.\n\n"
        "  infotech-email-agent start --port 9000 --no-browser\n"
        "      Background server on a custom port without opening a\n"
        "      browser. Useful on headless boxes / tunnels.\n\n"
        "Files:\n"
        "  out/web/server.pid    PID of the running server (managed by this CLI)\n"
        "  out/web/server.log    stdout + stderr of the background server\n"
    ),
)
def start(
    host: str = typer.Option(
        _default_host(),
        "--host",
        help="Bind host.",
    ),
    port: int = typer.Option(
        _default_port(),
        "--port",
        "-p",
        help="Port to serve API + dashboard on.",
    ),
    no_browser: bool = typer.Option(
        False, "--no-browser", help="Do not open the browser."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Rebuild the frontend bundle before serving."
    ),
) -> None:
    """Start the dashboard in the background (writes a PID file)."""
    load_dotenv(REPO_ROOT / ".env")
    _print_banner()

    existing = _read_pidfile()
    if existing is not None:
        typer.secho(
            f"ERROR: server already running (pid {existing}). "
            "Use `infotech-email-agent restart` or `stop` first.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    if not os.getenv("OPENAI_API_KEY"):
        typer.secho(
            "ERROR: OPENAI_API_KEY is not set. Copy .env.example to .env "
            "and paste your key.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    _build_frontend(force=rebuild)

    pid = _spawn_background_server(host=host, port=port)
    url = f"http://{host}:{port}/"

    # Tiny grace period so users opening the browser don't race the bind.
    time.sleep(0.4)
    if not _pid_alive(pid):
        typer.secho(
            f"ERROR: server failed to stay alive. See {_LOG_FILE} for details.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)

    typer.echo()
    typer.echo(_c("  ✓ ", "green") + f"started (pid {pid})")
    typer.echo(_c("  Dashboard ", "bold") + _c(url, "cyan", "bold"))
    typer.echo(_c(f"  API docs  http://{host}:{port}/docs", "dim"))
    typer.echo(_c(f"  Logs      {_LOG_FILE}", "dim"))
    typer.echo(_c("  Manage    `infotech-email-agent {status,stop,restart}`\n", "dim"))

    if not no_browser:
        try:
            webbrowser.open(url)
        except webbrowser.Error:
            pass


@app.command(
    epilog=(
        "Example:\n\n"
        "  infotech-email-agent stop\n"
        "      Read out/web/server.pid, send SIGTERM, wait up to 10s,\n"
        "      escalate to SIGKILL if needed, then remove the PID file.\n"
    ),
)
def stop() -> None:
    """Stop a background server started with ``start``."""
    pid = _read_pidfile()
    if pid is None:
        typer.echo(_c("  • no running server (no PID file)", "dim"))
        return
    typer.echo(_c(f"  → stopping pid {pid}…", "dim"))
    ok = _terminate_pid(pid)
    try:
        _PID_FILE.unlink()
    except OSError:
        pass
    if ok:
        typer.echo(_c("  ✓ ", "green") + "stopped")
    else:
        typer.secho(
            f"ERROR: pid {pid} did not exit cleanly; check `ps` manually.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


@app.command(
    epilog=(
        "Example:\n\n"
        "  infotech-email-agent restart --port 9000\n"
        "      Stop the running server (if any) and start a fresh one\n"
        "      on the given port.\n"
    ),
)
def restart(
    ctx: typer.Context,
    host: str = typer.Option(
        _default_host(), "--host", help="Bind host."
    ),
    port: int = typer.Option(
        _default_port(), "--port", "-p", help="Port to serve API + dashboard on."
    ),
    no_browser: bool = typer.Option(
        True, "--no-browser/--browser", help="Open the browser after restart."
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Rebuild the frontend bundle before serving."
    ),
) -> None:
    """Stop the background server (if running) and start it again."""
    # Best-effort stop: do not fail restart just because nothing was running.
    pid = _read_pidfile()
    if pid is not None:
        _terminate_pid(pid)
        try:
            _PID_FILE.unlink()
        except OSError:
            pass
    ctx.invoke(
        start,
        host=host,
        port=port,
        no_browser=no_browser,
        rebuild=rebuild,
    )


@app.command(
    epilog=(
        "Example:\n\n"
        "  infotech-email-agent status\n"
        "      Print whether a background server is running and where its\n"
        "      PID file + log file live.\n"
    ),
)
def status() -> None:
    """Report whether a background server is running."""
    pid = _read_pidfile()
    if pid is None:
        typer.echo(_c("  • not running", "dim"))
        _print_kv("PID file", str(_PID_FILE), ok=False)
        _print_kv("Log file", str(_LOG_FILE), ok=_LOG_FILE.exists())
        raise typer.Exit(code=3)
    typer.echo(_c("  ✓ ", "green") + f"running (pid {pid})")
    _print_kv("PID file", str(_PID_FILE), ok=True)
    _print_kv("Log file", str(_LOG_FILE), ok=_LOG_FILE.exists())


@app.command(
    epilog=(
        "Examples:\n\n"
        "  Terminal 1:  infotech-email-agent dev\n"
        "  Terminal 2:  cd frontend && bun install && bun run dev\n\n"
        "  Then open http://localhost:5173 (Vite proxies /api/* to this\n"
        "  backend at http://127.0.0.1:8000).\n"
    ),
)
def dev(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind host for the auto-reload backend."),
    port: int = typer.Option(8000, "--port", "-p", help="Port for the auto-reload backend."),
) -> None:
    """Run backend with auto-reload; Vite dev server is a separate terminal."""
    load_dotenv(REPO_ROOT / ".env")
    _print_banner()
    typer.echo(_c("  Dev mode — two terminals:\n", "bold"))
    typer.echo(
        f"   1) this process serves the API on "
        f"{_c(f'http://{host}:{port}', 'cyan')}"
    )
    typer.echo(
        "   2) in another terminal, run:\n"
        f"        {_c('cd frontend && bun install && bun run dev', 'cyan')}"
    )
    typer.echo(
        f"      then open {_c('http://localhost:5173', 'cyan', 'bold')} "
        "(it proxies /api/* to this server).\n"
    )

    if not os.getenv("OPENAI_API_KEY"):
        typer.secho(
            "WARN: OPENAI_API_KEY is not set; pipeline calls will return 503.",
            fg=typer.colors.YELLOW,
            err=True,
        )
        time.sleep(0.6)

    import uvicorn

    uvicorn.run(
        "invoice_agent_web.main:app",
        host=host,
        port=port,
        reload=True,
        reload_dirs=[str(REPO_ROOT / "src")],
        log_level="info",
    )


@app.command(
    epilog=(
        "Example:\n\n"
        "  infotech-email-agent doctor\n"
        "      Prints OPENAI_API_KEY status, LLM-shot toggle, python +\n"
        "      bun paths, frontend bundle location, and runs directory.\n"
        "      Use this first when something looks wrong.\n"
    ),
)
def doctor() -> None:
    """Print environment + dependency diagnostics."""
    load_dotenv(REPO_ROOT / ".env")
    _print_banner()
    _print_kv("repo root", str(REPO_ROOT))
    _print_kv(
        "OPENAI_API_KEY",
        "set" if os.getenv("OPENAI_API_KEY") else "MISSING",
        ok=bool(os.getenv("OPENAI_API_KEY")),
    )
    _print_kv(
        "LLM shots",
        "active" if os.getenv("INVOICE_PIPELINE_LLM_DISABLED") != "1" else "disabled",
        ok=os.getenv("INVOICE_PIPELINE_LLM_DISABLED") != "1",
    )
    _print_kv("python", sys.executable)
    _print_kv("bun", _have_bun() or "not found", ok=bool(_have_bun()))
    _print_kv(
        "frontend dist",
        str(FRONTEND_DIST),
        ok=_bundle_built(),
    )
    runs_dir = Path(os.getenv("INVOICE_WEB_RUNS_DIR") or (REPO_ROOT / "out" / "web"))
    _print_kv("runs dir", str(runs_dir), ok=runs_dir.exists() or True)


@app.command(
    "run",
    epilog=(
        "Examples:\n\n"
        "  infotech-email-agent run examples/case_1\n"
        "  infotech-email-agent run examples\n"
        "  infotech-email-agent run examples/case_1/Invoice.pdf examples/case_1/Email.json\n"
        "  infotech-email-agent run -f examples/case_1 -f examples/case_4_eur_consulting\n"
    ),
)
def run_cases(
    paths: list[Path] | None = typer.Argument(
        None,
        metavar="[PATHS]...",
        help="Files (.json/.pdf) and/or folders (single case or folder of cases).",
    ),
    files: list[Path] | None = typer.Option(
        None,
        "-f",
        "--file",
        help="Additional input path (repeatable).",
    ),
    out_dir: Path | None = typer.Option(
        None,
        "--out-dir",
        help="Root output dir. Each case writes to <out-dir>/<case-name>/.",
    ),
    no_llm: bool = typer.Option(
        False,
        "--no-llm",
        help="Disable pipeline LLM shots (deterministic-only).",
    ),
    continue_on_error: bool = typer.Option(
        True,
        "--continue-on-error/--stop-on-error",
        help="Continue running remaining cases after a failure.",
    ),
) -> None:
    """Run one or many case inputs through the existing batch intake CLI."""
    all_inputs = [*(paths or []), *(files or [])]
    if not all_inputs:
        typer.secho("ERROR: no inputs given", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)
    if no_llm:
        _set_no_llm_env()
    cases = discover_cases(all_inputs)
    if not cases:
        typer.secho("ERROR: no runnable Email.json cases discovered", fg=typer.colors.RED, err=True)
        raise typer.Exit(code=2)

    import invoice_agent.cli as core_cli

    failed = 0
    first_fail_code = 1
    for email_path, pdf_path in cases:
        argv = _build_case_argv(email_path, pdf_path, out_dir)
        rc = core_cli.main(argv)
        if rc == 0:
            continue
        failed += 1
        first_fail_code = rc
        if not continue_on_error:
            raise typer.Exit(code=first_fail_code)
    if failed:
        typer.secho(
            f"{failed} of {len(cases)} case(s) failed",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=1)


@app.command()
def version() -> None:
    """Print package version (e.g. ``infotech-email-agent 0.1.0``)."""
    try:
        v = _pkg_version("invoice-intake-agent")
    except PackageNotFoundError:
        v = "unknown (package not installed)"
    typer.echo(f"infotech-email-agent {v}")


# --------------------------------------------------------------------------- #
# `config` subgroup — show merged settings + the file paths feeding them
# --------------------------------------------------------------------------- #

config_app = typer.Typer(
    name="config",
    help=(
        "Inspect the merged configuration the agent will use.\n\n"
        "Precedence (lowest → highest): defaults → global TOML → project TOML "
        "→ environment variables → CLI flags."
    ),
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(config_app, name="config")


def _project_config_path() -> Path | None:
    paths = project_config_paths(REPO_ROOT)
    return paths[0] if paths else None


@config_app.command("show")
def config_show() -> None:
    """Print the merged configuration and where each layer lives."""
    load_dotenv(REPO_ROOT / ".env")
    settings = load_settings(project_start=REPO_ROOT)
    gpath = global_config_path()
    ppath = _project_config_path()

    _print_banner()
    typer.echo(_c("  Config sources (lowest → highest precedence)", "bold"))
    _print_kv("app name", APP_NAME)
    _print_kv(
        "global TOML",
        str(gpath),
        ok=gpath.is_file(),
    )
    _print_kv(
        "project TOML",
        str(ppath) if ppath else "(none found by walking up from repo root)",
        ok=ppath is not None,
    )
    _print_kv(
        "env overrides",
        "INFOTECH_* / INVOICE_* (see docs/API.md)",
    )
    _print_kv(
        "OPENAI_API_KEY",
        "set" if os.getenv("OPENAI_API_KEY") else "MISSING (env / .env)",
        ok=bool(os.getenv("OPENAI_API_KEY")),
    )
    typer.echo("")
    typer.echo(_c("  Resolved settings", "bold"))
    for key, value in settings.model_dump().items():
        _print_kv(key, repr(value))
    typer.echo("")
    typer.echo(
        _c(
            "  To edit:  open the project TOML above (or create "
            "config/config.toml at the repo root).",
            "dim",
        )
    )
    typer.echo(
        _c(
            "  Env vars always win over the file; CLI flags always win "
            "over env vars.",
            "dim",
        )
    )


@config_app.command("path")
def config_path() -> None:
    """Print just the file paths (machine-friendly: one per line)."""
    gpath = global_config_path()
    ppath = _project_config_path()
    typer.echo(f"global={gpath}")
    typer.echo(f"project={ppath if ppath else ''}")


# --------------------------------------------------------------------------- #
# `docker` subgroup — wrap docker compose for the bundled compose file
# --------------------------------------------------------------------------- #

docker_app = typer.Typer(
    name="docker",
    help=(
        "Manage the dockerised dashboard via the bundled docker-compose.yml.\n\n"
        "Subcommands:\n"
        "  up        build (if needed) and start the agent container in the background\n"
        "  down      stop and remove the agent container\n"
        "  restart   down then up (preserves --port)\n"
        "  status    show `docker compose ps` for this project\n"
        "  logs      follow container logs (Ctrl-C to detach)\n\n"
        "All commands shell out to `docker compose` from the repo root, so the\n"
        "project-mounted ./out volume keeps your previous runs available across\n"
        "container restarts."
    ),
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(docker_app, name="docker")


def _have_docker() -> str | None:
    return shutil.which("docker")


def _compose_base() -> list[str]:
    """Return the ``docker compose -f …`` prefix for this repo."""
    docker = _have_docker()
    if docker is None:
        typer.secho(
            "ERROR: `docker` is not on PATH. Install Docker Desktop "
            "(https://docs.docker.com/get-docker/) and try again.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    compose_file = REPO_ROOT / "docker-compose.yml"
    if not compose_file.is_file():
        typer.secho(
            f"ERROR: docker-compose.yml not found at {compose_file}",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)
    return [docker, "compose", "-f", str(compose_file)]


def _run_compose(args: list[str]) -> int:
    """Run ``docker compose <args>`` from the repo root and return its exit code."""
    cmd = _compose_base() + args
    typer.echo(_c(f"  → {' '.join(cmd)}", "dim"))
    return subprocess.call(cmd, cwd=str(REPO_ROOT))


@docker_app.command("up")
def docker_up(
    port: int = typer.Option(
        8000,
        "--port",
        "-p",
        help="Host port to publish (overrides the compose default of 8000).",
    ),
    rebuild: bool = typer.Option(
        False, "--rebuild", help="Force `--build` so the image is rebuilt."
    ),
    foreground: bool = typer.Option(
        False, "--foreground", "-f", help="Stream logs in the foreground (no -d)."
    ),
) -> None:
    """Build (if needed) and start the agent container."""
    _print_banner()
    if not (REPO_ROOT / ".env").is_file() and not os.getenv("OPENAI_API_KEY"):
        typer.secho(
            "WARN: no .env file and no OPENAI_API_KEY in environment — "
            "the container will start but /api/intake will return 503.",
            fg=typer.colors.YELLOW,
            err=True,
        )
    args = ["up"]
    if not foreground:
        args.append("-d")
    if rebuild:
        args.append("--build")
    # Override the published port via env var consumed by docker-compose.yml.
    env_overrides = os.environ.copy()
    env_overrides["HOST_PORT"] = str(port)
    cmd = _compose_base() + args
    typer.echo(_c(f"  → {' '.join(cmd)}  (host port {port})", "dim"))
    rc = subprocess.call(cmd, cwd=str(REPO_ROOT), env=env_overrides)
    if rc != 0:
        raise typer.Exit(code=rc)
    if not foreground:
        typer.echo()
        typer.echo(_c("  ✓ ", "green") + f"container started → http://127.0.0.1:{port}/")
        typer.echo(_c("  Logs:    infotech-email-agent docker logs", "dim"))
        typer.echo(_c("  Status:  infotech-email-agent docker status", "dim"))
        typer.echo(_c("  Stop:    infotech-email-agent docker down", "dim"))


@docker_app.command("down")
def docker_down(
    volumes: bool = typer.Option(
        False,
        "--volumes",
        "-v",
        help="Also remove named volumes (NB: ./out is a bind mount, unaffected).",
    ),
) -> None:
    """Stop and remove the agent container."""
    args = ["down"]
    if volumes:
        args.append("-v")
    rc = _run_compose(args)
    if rc != 0:
        raise typer.Exit(code=rc)
    typer.echo(_c("  ✓ ", "green") + "container stopped")


@docker_app.command("restart")
def docker_restart(
    ctx: typer.Context,
    port: int = typer.Option(8000, "--port", "-p", help="Host port to publish."),
    rebuild: bool = typer.Option(False, "--rebuild", help="Rebuild the image before restarting."),
) -> None:
    """Stop and re-launch the agent container."""
    # Best-effort down — ignore failure so a stopped container still restarts.
    _run_compose(["down"])
    ctx.invoke(docker_up, port=port, rebuild=rebuild, foreground=False)


@docker_app.command("status")
def docker_status() -> None:
    """Show `docker compose ps` for this project."""
    rc = _run_compose(["ps"])
    if rc != 0:
        raise typer.Exit(code=rc)


@docker_app.command("logs")
def docker_logs(
    follow: bool = typer.Option(
        True, "--follow/--no-follow", "-f/-F", help="Stream new logs as they arrive."
    ),
    tail: int = typer.Option(
        200, "--tail", help="Lines to show from the end of the log buffer."
    ),
) -> None:
    """Print container logs (`docker compose logs`)."""
    args = ["logs", "--tail", str(tail)]
    if follow:
        args.append("-f")
    args.append("agent")
    rc = _run_compose(args)
    if rc not in (0, 130):  # 130 = SIGINT from Ctrl-C while following
        raise typer.Exit(code=rc)


def main() -> None:
    """Entry point declared in pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
