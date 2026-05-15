"""Centralized logging configuration for the invoice-intake project.

Single source of truth for *where* logs go. Both the batch CLI
(``invoice_agent.cli``) and the FastAPI adapter
(``invoice_agent_web.main``) call :func:`configure` once at startup.

Layout (under repo root by default — overridable via
``INFOTECH_LOG_DIR`` / ``INVOICE_LOG_DIR``):

    logs/
    ├── cli/                cli-YYYYMMDD.log   (TimedRotatingFileHandler, 14 backups)
    ├── web/                web-YYYYMMDD.log   (TimedRotatingFileHandler, 14 backups)
    └── runs/               <case_id>.log      (one mirror per intake run)

Per-run ``out/<case>/run.log`` and ``out/web/server.log`` are NOT
removed — they remain the authoritative "everything for this one run /
this server boot" file. The ``logs/runs/<case_id>.log`` mirror gives
operations a *single* directory to grep across runs without walking
``out/``.

Architectural rules respected:
- DIP: callers depend on :func:`configure` and :func:`mirror_run_log`,
  never on ``logging`` internals beyond the standard handlers.
- No silent fallbacks: a permissions error on ``logs/`` is logged
  (WARNING) and the process continues — observability must never
  break the pipeline. If even ``logs/`` itself can't be created we
  log to stderr only.
- Idempotent: re-invoking :func:`configure` does not stack handlers;
  a sentinel attribute on the root logger tracks installed sinks.
"""

from __future__ import annotations

import logging
import logging.handlers
import os
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Final

_FORMAT: Final[str] = "%(asctime)s %(levelname)s %(name)s: %(message)s"
_SENTINEL: Final[str] = "_infotech_logging_installed"
_DEFAULT_BACKUP_DAYS: Final[int] = 14


def _resolve_logs_dir(override: Path | None) -> Path:
    """Pick the ``logs/`` root, honoring env then default to repo-root."""
    if override is not None:
        return override.expanduser().resolve()
    env = os.getenv("INFOTECH_LOG_DIR") or os.getenv("INVOICE_LOG_DIR")
    if env:
        return Path(env).expanduser().resolve()
    # logging_setup.py lives at src/invoice_agent/logging_setup.py;
    # repo root is two parents up.
    repo_root = Path(__file__).resolve().parents[2]
    return repo_root / "logs"


@dataclass(frozen=True)
class LogPaths:
    """Resolved on-disk locations of the centralized log sinks."""

    root: Path
    cli_dir: Path
    web_dir: Path
    runs_dir: Path


def _ensure_layout(root: Path) -> LogPaths | None:
    """Create the ``logs/{cli,web,runs}/`` tree. Returns ``None`` on failure."""
    try:
        cli_dir = root / "cli"
        web_dir = root / "web"
        runs_dir = root / "runs"
        for d in (cli_dir, web_dir, runs_dir):
            d.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        # Stderr only — no logger is configured yet.
        print(
            f"WARNING: cannot create logs directory under {root}: {exc}",
            file=sys.stderr,
        )
        return None
    return LogPaths(root=root, cli_dir=cli_dir, web_dir=web_dir, runs_dir=runs_dir)


def _build_rotating_handler(path: Path) -> logging.Handler | None:
    """A daily-rotated INFO-level file handler at ``path``."""
    try:
        handler = logging.handlers.TimedRotatingFileHandler(
            filename=path,
            when="midnight",
            backupCount=_DEFAULT_BACKUP_DAYS,
            encoding="utf-8",
            utc=False,
        )
    except OSError as exc:
        print(
            f"WARNING: cannot open log file {path}: {exc}",
            file=sys.stderr,
        )
        return None
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_FORMAT))
    return handler


def _mute_third_party() -> None:
    """Quiet noisy upstream loggers; keep our own decision trail loud."""
    for name in ("httpx", "openai", "urllib3", "uvicorn.access"):
        logging.getLogger(name).setLevel(logging.WARNING)


def configure(
    *,
    surface: str,
    extra_file: Path | None = None,
    logs_dir: Path | None = None,
    level: int = logging.INFO,
) -> LogPaths | None:
    """Install the project's logging sinks. Idempotent.

    Args:
        surface: ``"cli"`` or ``"web"`` — selects which centralized
            daily-rotated file under ``logs/`` to attach.
        extra_file: Optional additional file sink (per-run
            ``out/<case>/run.log`` for the CLI). Always created if
            given; failures are logged to stderr.
        logs_dir: Override the ``logs/`` root (mainly for tests).
        level: Root logger level (default INFO).

    Returns:
        The resolved :class:`LogPaths` if the centralized layout was
        created, else ``None``. The function never raises — observability
        is best-effort.
    """
    if surface not in {"cli", "web"}:
        raise ValueError(f"surface must be 'cli' or 'web', got {surface!r}")

    root_logger = logging.getLogger()
    already = getattr(root_logger, _SENTINEL, None)
    if already is not None:
        # Already configured for this process: do not stack handlers,
        # but DO attach extra_file (per-run sinks are scoped).
        _attach_extra(root_logger, extra_file)
        return already if isinstance(already, LogPaths) else None

    root_logger.setLevel(level)
    # Console (stderr) handler — always.
    if not any(
        isinstance(h, logging.StreamHandler) and h.stream is sys.stderr
        for h in root_logger.handlers
    ):
        console = logging.StreamHandler(sys.stderr)
        console.setLevel(level)
        console.setFormatter(logging.Formatter(_FORMAT))
        root_logger.addHandler(console)

    paths = _ensure_layout(_resolve_logs_dir(logs_dir))
    if paths is not None:
        target_dir = paths.cli_dir if surface == "cli" else paths.web_dir
        rot = _build_rotating_handler(target_dir / f"{surface}.log")
        if rot is not None:
            root_logger.addHandler(rot)

    _attach_extra(root_logger, extra_file)
    _mute_third_party()
    setattr(root_logger, _SENTINEL, paths if paths is not None else True)
    return paths


def _attach_extra(root_logger: logging.Logger, extra_file: Path | None) -> None:
    """Attach a per-run/per-server FileHandler if one was requested."""
    if extra_file is None:
        return
    try:
        extra_file.parent.mkdir(parents=True, exist_ok=True)
        handler = logging.FileHandler(extra_file, encoding="utf-8")
    except OSError as exc:
        print(
            f"WARNING: cannot open extra log file {extra_file}: {exc}",
            file=sys.stderr,
        )
        return
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter(_FORMAT))
    root_logger.addHandler(handler)


def mirror_run_log(case_run_log: Path, case_id: str, logs_dir: Path | None = None) -> Path | None:
    """Copy the per-run log into ``logs/runs/<case_id>.log``.

    Called by the CLI / web adapter at the end of a run so operators
    have a flat directory to grep historical runs without walking
    ``out/``. Failures are logged (WARNING) and swallowed.

    Returns the destination path on success, else ``None``.
    """
    if not case_run_log.is_file():
        return None
    paths = _ensure_layout(_resolve_logs_dir(logs_dir))
    if paths is None:
        return None
    dest = paths.runs_dir / f"{case_id}.log"
    log = logging.getLogger(__name__)
    try:
        shutil.copyfile(case_run_log, dest)
    except OSError as exc:
        log.warning("logs: could not mirror run log to %s: %s", dest, exc)
        return None
    return dest


__all__ = ["LogPaths", "configure", "mirror_run_log"]
