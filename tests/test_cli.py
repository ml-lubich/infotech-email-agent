"""CLI argument parsing, out-dir resolution, and error-path exit codes."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from invoice_agent.cli import _parse_args, _resolve_out_dir, main

REPO_ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = REPO_ROOT / "examples"


def test_parse_args_minimal() -> None:
    ns = _parse_args(["--email", "examples/case_1/Email.json"])
    assert ns.email == Path("examples/case_1/Email.json")
    assert ns.pdf is None
    assert ns.out_dir is None
    assert ns.log_file is None


def test_parse_args_all_fields() -> None:
    ns = _parse_args(
        [
            "--email", "e.json",
            "--pdf", "p.pdf",
            "--out-dir", "o",
            "--log-file", "l.log",
        ]
    )
    assert ns.pdf == Path("p.pdf")
    assert ns.out_dir == Path("o")
    assert ns.log_file == Path("l.log")


def test_parse_args_requires_email() -> None:
    with pytest.raises(SystemExit):
        _parse_args([])


def test_resolve_out_dir_uses_email_parent_folder_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    email = tmp_path / "case_foo" / "Email.json"
    email.parent.mkdir()
    email.write_text("{}", encoding="utf-8")
    assert _resolve_out_dir(email, None) == tmp_path / "out" / "case_foo"


def test_resolve_out_dir_honors_override(tmp_path: Path) -> None:
    override = tmp_path / "anywhere"
    email = tmp_path / "case_x" / "Email.json"
    assert _resolve_out_dir(email, override) == override


def test_main_exits_2_when_api_key_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    # Block .env auto-loading from masking the missing-key path.
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)
    rc = main(["--email", str(EXAMPLES / "case_1" / "Email.json")])
    assert rc == 2


def test_main_exits_2_when_email_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)
    rc = main(["--email", str(tmp_path / "nope.json")])
    assert rc == 2


def test_main_exits_1_when_no_attachment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)
    rc = main(["--email", str(EXAMPLES / "case_3_no_attachment" / "Email.json")])
    assert rc == 1


def test_main_exits_1_when_pdf_missing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)
    rc = main(["--email", str(EXAMPLES / "case_2_missing_pdf" / "Email.json")])
    assert rc == 1
