"""Per-request run.log capture in the web adapter.

Regression test for the dashboard "Run log" tab returning "(no log)":
the FastAPI adapter must attach a per-run FileHandler so pipeline log
records land in ``<case_dir>/run.log`` (mirroring the CLI), and the
``log_tail`` field must surface that file's tail.
"""

from __future__ import annotations

import logging
from pathlib import Path

from invoice_agent_web.main import _attach_run_log_handler, _read_log_tail


def test_attach_run_log_handler_writes_pipeline_logs(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_x"
    case_dir.mkdir()

    handler = _attach_run_log_handler(case_dir)
    try:
        # Emit through a logger name the pipeline actually uses ("invoice_agent.*").
        logging.getLogger("invoice_agent.test").info("pipeline-shot-fired")
    finally:
        logging.getLogger().removeHandler(handler)
        handler.close()

    log_file = case_dir / "run.log"
    assert log_file.is_file(), "run.log was not created by the per-run handler"
    contents = log_file.read_text(encoding="utf-8")
    assert "pipeline-shot-fired" in contents
    assert "invoice_agent.test" in contents


def test_read_log_tail_returns_recent_lines(tmp_path: Path) -> None:
    case_dir = tmp_path / "case_y"
    case_dir.mkdir()
    (case_dir / "run.log").write_text(
        "\n".join(f"line-{i}" for i in range(1, 11)) + "\n",
        encoding="utf-8",
    )
    tail = _read_log_tail(case_dir, lines=3)
    assert tail.splitlines() == ["line-8", "line-9", "line-10"]


def test_read_log_tail_missing_file_returns_empty(tmp_path: Path) -> None:
    assert _read_log_tail(tmp_path) == ""
