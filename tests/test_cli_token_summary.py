"""CLI helpers added by the hardening transaction (logging + token print)."""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from pathlib import Path

from invoice_agent.cli import _print_token_summary


def _write_payload(out_dir: Path, usage: dict) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "outbound_email.json").write_text(
        json.dumps({"vendor_name": "Acme", "usage": usage}),
        encoding="utf-8",
    )


def test_print_token_summary_renders_table(tmp_path: Path) -> None:
    _write_payload(
        tmp_path,
        usage={
            "totals": {
                "input_tokens": 1234,
                "output_tokens": 200,
                "total_tokens": 1434,
                "cached_input_tokens": 500,
                "reasoning_tokens": 50,
            },
            "cache_hit_ratio": 0.4054,
            "shots": [
                {
                    "shot": "agent_loop",
                    "model": "gpt-5-mini",
                    "input_tokens": 800,
                    "output_tokens": 120,
                    "total_tokens": 920,
                    "cached_input_tokens": 300,
                    "reasoning_tokens": 30,
                },
                {
                    "shot": "extract",
                    "model": "gpt-5-mini",
                    "input_tokens": 434,
                    "output_tokens": 80,
                    "total_tokens": 514,
                    "cached_input_tokens": 200,
                    "reasoning_tokens": 20,
                },
            ],
        },
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_token_summary(tmp_path)
    output = buf.getvalue()
    assert "Token usage" in output
    assert "agent_loop" in output
    assert "extract" in output
    assert "TOTAL" in output
    assert "1,434" in output
    assert "40.5%" in output


def test_print_token_summary_silent_when_no_usage(tmp_path: Path) -> None:
    (tmp_path / "outbound_email.json").write_text(
        json.dumps({"vendor_name": "Acme"}), encoding="utf-8"
    )
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_token_summary(tmp_path)
    assert buf.getvalue() == ""


def test_print_token_summary_silent_when_no_file(tmp_path: Path) -> None:
    buf = io.StringIO()
    with redirect_stdout(buf):
        _print_token_summary(tmp_path)
    assert buf.getvalue() == ""
