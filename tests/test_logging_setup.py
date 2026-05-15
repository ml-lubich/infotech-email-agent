"""Tests for the centralized logging setup module."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from invoice_agent import logging_setup


@pytest.fixture(autouse=True)
def _reset_logging():
    """Wipe root-logger state between tests so handlers do not stack."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    sentinel = getattr(root, logging_setup._SENTINEL, None)
    root.handlers.clear()
    if sentinel is not None:
        delattr(root, logging_setup._SENTINEL)
    yield
    root.handlers.clear()
    root.handlers.extend(saved_handlers)
    root.setLevel(saved_level)
    if hasattr(root, logging_setup._SENTINEL):
        delattr(root, logging_setup._SENTINEL)


def test_configure_creates_layout(tmp_path: Path) -> None:
    paths = logging_setup.configure(surface="cli", logs_dir=tmp_path)
    assert paths is not None
    assert paths.cli_dir.is_dir()
    assert paths.web_dir.is_dir()
    assert paths.runs_dir.is_dir()


def test_configure_attaches_extra_file(tmp_path: Path) -> None:
    extra = tmp_path / "case" / "run.log"
    logging_setup.configure(surface="cli", extra_file=extra, logs_dir=tmp_path)
    logging.getLogger("invoice_agent.test").info("hello world")
    for h in logging.getLogger().handlers:
        h.flush()
    assert extra.is_file()
    assert "hello world" in extra.read_text(encoding="utf-8")


def test_configure_is_idempotent(tmp_path: Path) -> None:
    logging_setup.configure(surface="cli", logs_dir=tmp_path)
    n_after_first = len(logging.getLogger().handlers)
    logging_setup.configure(surface="cli", logs_dir=tmp_path)
    n_after_second = len(logging.getLogger().handlers)
    assert n_after_first == n_after_second, "configure() must not stack handlers"


def test_configure_rejects_unknown_surface(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        logging_setup.configure(surface="bogus", logs_dir=tmp_path)


def test_mirror_run_log_copies_into_runs_dir(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()
    src = case_dir / "run.log"
    src.write_text("line one\nline two\n", encoding="utf-8")

    dest = logging_setup.mirror_run_log(src, "case_x", logs_dir=tmp_path)
    assert dest is not None
    assert dest == tmp_path / "runs" / "case_x.log"
    assert dest.read_text(encoding="utf-8") == "line one\nline two\n"


def test_mirror_run_log_missing_source_returns_none(tmp_path: Path) -> None:
    out = logging_setup.mirror_run_log(tmp_path / "nope.log", "case_x", logs_dir=tmp_path)
    assert out is None


def test_resolve_logs_dir_env_override(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("INFOTECH_LOG_DIR", str(tmp_path / "custom"))
    resolved = logging_setup._resolve_logs_dir(None)
    assert resolved == tmp_path / "custom"
