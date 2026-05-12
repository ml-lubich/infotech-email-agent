"""End-to-end happy path for `agent.run_intake` and `cli.main` with mocked SDK calls.

No network. `Runner.run_sync` is patched to drive the two tool side-effects
synchronously so we exercise every branch of the orchestration layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from invoice_agent import agent as agent_mod
from invoice_agent.cli import main


class _FakeRunResult:
    def __init__(self, final_output: str) -> None:
        self.final_output = final_output


def _fake_runner_factory(out_dir: Path, *, write_artifacts: bool = True):
    """Build a Runner.run_sync stand-in.

    When ``write_artifacts`` is True we actually invoke the notification
    helper so the artifact paths exist on disk — mirrors what the real
    agent would do.
    """

    def _runner(_agent: Any, _prompt: str) -> _FakeRunResult:
        if write_artifacts:
            from invoice_agent.tools import write_notification_files

            write_notification_files(
                "## summary\nok\n",
                json.dumps({"vendor_name": "FakeCo", "invoice_number": "INV-1"}),
                out_dir,
            )
        return _FakeRunResult("done")

    return _runner


def test_run_intake_happy_path_resolves_sibling_pdf(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = tmp_path / "case_x"
    case.mkdir()
    (case / "Invoice.pdf").write_bytes(b"%PDF-1.4 stub")  # presence-only
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Attachments": [{"Name": "Invoice.pdf"}]}}),
        encoding="utf-8",
    )

    out_dir = tmp_path / "out" / "case_x"
    monkeypatch.setattr(
        agent_mod.Runner, "run_sync", staticmethod(_fake_runner_factory(out_dir))
    )

    result = agent_mod.run_intake(
        email_path=case / "Email.json",
        pdf_path=None,
        out_dir=out_dir,
    )
    assert result.agent_reply == "done"
    assert result.artifacts["outbound_email.txt"].is_file()
    assert result.artifacts["outbound_email.json"].is_file()


def test_run_intake_honours_explicit_pdf_and_unused_client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    case = tmp_path / "case_y"
    case.mkdir()
    pdf = case / "Other.pdf"
    pdf.write_bytes(b"%PDF-1.4 stub")
    (case / "Email.json").write_text(
        # Flat shape (no "Message" wrapper) + non-PDF attachment ignored.
        json.dumps(
            {"Attachments": [{"Name": "notes.txt"}, {"Name": "Other.pdf"}]}
        ),
        encoding="utf-8",
    )
    out_dir = tmp_path / "out" / "case_y"
    monkeypatch.setattr(
        agent_mod.Runner, "run_sync", staticmethod(_fake_runner_factory(out_dir))
    )

    # `openai_client` kwarg is documented as currently unused; pass a sentinel
    # to cover that branch.
    result = agent_mod.run_intake(
        email_path=case / "Email.json",
        pdf_path=pdf,
        out_dir=out_dir,
        openai_client="sentinel",  # type: ignore[arg-type]
    )
    assert result.agent_reply == "done"


def test_run_intake_attachment_without_name_is_ignored(
    tmp_path: Path,
) -> None:
    case = tmp_path / "case_z"
    case.mkdir()
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Attachments": [{"Name": None}, {}]}}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="No PDF attachment"):
        agent_mod.run_intake(
            email_path=case / "Email.json", out_dir=tmp_path / "out"
        )


def test_run_intake_resolves_pdf_but_pdf_missing_on_disk(tmp_path: Path) -> None:
    case = tmp_path / "case_m"
    case.mkdir()
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Attachments": [{"Name": "Gone.pdf"}]}}),
        encoding="utf-8",
    )
    with pytest.raises(FileNotFoundError, match="PDF attachment not found"):
        agent_mod.run_intake(
            email_path=case / "Email.json", out_dir=tmp_path / "out"
        )


def test_run_intake_missing_email_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError, match="Email file not found"):
        agent_mod.run_intake(
            email_path=tmp_path / "nope.json", out_dir=tmp_path / "out"
        )


def test_cli_main_success_prints_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case = tmp_path / "case_cli"
    case.mkdir()
    (case / "Invoice.pdf").write_bytes(b"%PDF-1.4 stub")
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Attachments": [{"Name": "Invoice.pdf"}]}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)

    out_dir = tmp_path / "out" / "case_cli"
    monkeypatch.setattr(
        agent_mod.Runner, "run_sync", staticmethod(_fake_runner_factory(out_dir))
    )

    rc = main(["--email", str(case / "Email.json")])
    captured = capsys.readouterr()
    assert rc == 0
    assert "done" in captured.out
    assert "outbound_email.txt" in captured.out
    assert "(NOT WRITTEN)" not in captured.out


def test_cli_main_success_marks_missing_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case = tmp_path / "case_cli2"
    case.mkdir()
    (case / "Invoice.pdf").write_bytes(b"%PDF-1.4 stub")
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Attachments": [{"Name": "Invoice.pdf"}]}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)

    out_dir = tmp_path / "out" / "case_cli2"
    # Runner that does NOT write artifacts → CLI must show (NOT WRITTEN).
    monkeypatch.setattr(
        agent_mod.Runner,
        "run_sync",
        staticmethod(_fake_runner_factory(out_dir, write_artifacts=False)),
    )

    rc = main(["--email", str(case / "Email.json")])
    captured = capsys.readouterr()
    assert rc == 0
    assert "(NOT WRITTEN)" in captured.out


def test_cli_main_unexpected_exception_returns_1(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    case = tmp_path / "case_crash"
    case.mkdir()
    (case / "Invoice.pdf").write_bytes(b"%PDF-1.4 stub")
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Attachments": [{"Name": "Invoice.pdf"}]}}),
        encoding="utf-8",
    )

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
    monkeypatch.setattr("invoice_agent.cli.load_dotenv", lambda *a, **k: False)
    monkeypatch.chdir(tmp_path)

    def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("kaboom")

    monkeypatch.setattr("invoice_agent.cli.run_intake", _boom)

    rc = main(["--email", str(case / "Email.json")])
    err = capsys.readouterr().err
    assert rc == 1
    assert "kaboom" in err


def test_main_module_smoke() -> None:
    """`python main.py` invokes the package CLI; SystemExit on no args."""
    # Importing main.py executes nothing at import (guard inside __main__ block).
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "_repo_main_entry",
        Path(__file__).resolve().parents[1] / "main.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert hasattr(module, "main")


def test_main_entrypoint_uses_invoice_agent_cli() -> None:
    """Argparse should reject the empty argv at the top-level entry point."""
    from invoice_agent.cli import main as cli_main

    with patch("sys.argv", ["main.py"]):
        with pytest.raises(SystemExit):
            cli_main([])
