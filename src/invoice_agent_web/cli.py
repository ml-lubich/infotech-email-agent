"""Typer CLI for the InfoTech Email Agent dashboard.

Console-script entrypoint: ``infotech-email-agent``.

Subcommands
-----------
* ``up`` (default)  Build the React bundle if needed, then serve the API
                    + the bundle on a single port. Opens the browser.
* ``dev``           Run the FastAPI backend with reload on one port and
                    print Vite-dev instructions for the other.
* ``doctor``        Print env / dependency diagnostics.
* ``version``       Print the package version.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import webbrowser
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

import typer
from dotenv import load_dotenv

from invoice_agent_web.main import FRONTEND_DIST, REPO_ROOT

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
        "  $ infotech-email-agent dev         # backend with auto-reload\n"
        "  $ infotech-email-agent doctor      # env / dependency diagnostics\n"
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
            "in frontend/.",
            fg=typer.colors.RED,
            err=True,
        )
        raise typer.Exit(code=2)

    frontend_dir = REPO_ROOT / "frontend"
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
            host=os.getenv("INVOICE_WEB_HOST", "127.0.0.1"),
            port=int(os.getenv("INVOICE_WEB_PORT", "8000")),
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
        os.getenv("INVOICE_WEB_HOST", "127.0.0.1"),
        "--host",
        help="Bind host.",
    ),
    port: int = typer.Option(
        int(os.getenv("INVOICE_WEB_PORT", "8000")),
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
    load_dotenv()
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
        "  Terminal 1:  infotech-email-agent dev\n"
        "  Terminal 2:  cd frontend && bun install && bun run dev\n\n"
        "  Then open http://localhost:5173 (Vite proxies /api/* to this\n"
        "  backend at http://127.0.0.1:8000).\n"
    ),
)
def dev(
    host: str = typer.Option("127.0.0.1", "--host"),
    port: int = typer.Option(8000, "--port", "-p"),
) -> None:
    """Run backend with auto-reload; Vite dev server is a separate terminal."""
    load_dotenv()
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
    load_dotenv()
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


@app.command()
def version() -> None:
    """Print package version (e.g. ``infotech-email-agent 0.1.0``)."""
    try:
        v = _pkg_version("invoice-intake-agent")
    except PackageNotFoundError:
        v = "unknown (package not installed)"
    typer.echo(f"infotech-email-agent {v}")


def main() -> None:
    """Entry point declared in pyproject.toml."""
    app()


if __name__ == "__main__":
    main()
