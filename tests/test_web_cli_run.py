"""Tests for the ``infotech-email-agent run`` subcommand.

The ``run`` subcommand is the minimal "intelligent" CLI: it accepts a
free-form list of files (.json / .pdf) and folders in any order, figures
out which inputs form a runnable case, and dispatches each one through
``invoice_agent.cli.main``. These tests pin that classification and the
dispatch contract — they NEVER call OpenAI; the underlying intake is
patched to a recording stub.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from invoice_agent_web import cli as web_cli


# --------------------------------------------------------------------------- #
# pure-function unit tests for discover_cases
# --------------------------------------------------------------------------- #


def _make_case(root: Path, name: str, *, with_pdf: bool = True) -> Path:
    case_dir = root / name
    case_dir.mkdir(parents=True)
    (case_dir / "Email.json").write_text("{}", encoding="utf-8")
    if with_pdf:
        (case_dir / "Invoice.pdf").write_bytes(b"%PDF-1.4\n%stub\n")
    return case_dir


def test_discover_cases_single_case_folder(tmp_path: Path) -> None:
    c = _make_case(tmp_path, "case_a")
    cases = web_cli.discover_cases([c])
    assert cases == [(c / "Email.json", None)]


def test_discover_cases_folder_of_cases(tmp_path: Path) -> None:
    a = _make_case(tmp_path, "case_a")
    b = _make_case(tmp_path, "case_b")
    # Stray non-case folder must be ignored.
    (tmp_path / "not_a_case").mkdir()
    cases = web_cli.discover_cases([tmp_path])
    emails = [em for em, _ in cases]
    assert emails == [a / "Email.json", b / "Email.json"]


def test_discover_cases_explicit_email_json(tmp_path: Path) -> None:
    c = _make_case(tmp_path, "case_a")
    cases = web_cli.discover_cases([c / "Email.json"])
    assert cases == [(c / "Email.json", None)]


def test_discover_cases_pdf_pairs_with_sibling_email(tmp_path: Path) -> None:
    c = _make_case(tmp_path, "case_a")
    cases = web_cli.discover_cases([c / "Invoice.pdf"])
    assert cases == [(c / "Email.json", c / "Invoice.pdf")]


def test_discover_cases_email_and_pdf_in_any_order(tmp_path: Path) -> None:
    c = _make_case(tmp_path, "case_a")
    forward = web_cli.discover_cases([c / "Email.json", c / "Invoice.pdf"])
    reverse = web_cli.discover_cases([c / "Invoice.pdf", c / "Email.json"])
    assert forward == [(c / "Email.json", c / "Invoice.pdf")]
    assert reverse == forward


def test_discover_cases_dedupes(tmp_path: Path) -> None:
    c = _make_case(tmp_path, "case_a")
    cases = web_cli.discover_cases([c, c / "Email.json", c / "Invoice.pdf"])
    assert len(cases) == 1
    assert cases[0][0] == c / "Email.json"


def test_discover_cases_rejects_unknown_extension(tmp_path: Path) -> None:
    bogus = tmp_path / "weird.txt"
    bogus.write_text("nope")
    import typer

    with pytest.raises(typer.BadParameter):
        web_cli.discover_cases([bogus])


def test_discover_cases_rejects_missing_path(tmp_path: Path) -> None:
    import typer

    with pytest.raises(typer.BadParameter):
        web_cli.discover_cases([tmp_path / "does_not_exist.json"])


# --------------------------------------------------------------------------- #
# CLI integration: stub out the real intake and assert dispatch
# --------------------------------------------------------------------------- #


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def stub_intake(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """Replace ``invoice_agent.cli.main`` with a recording no-op (rc=0)."""
    calls: list[list[str]] = []

    def _fake_main(argv: list[str] | None = None) -> int:
        calls.append(list(argv or []))
        return 0

    import invoice_agent.cli as core_cli

    monkeypatch.setattr(core_cli, "main", _fake_main)
    return calls


def test_run_no_inputs_exits_2(
    runner: CliRunner, stub_intake: list[list[str]]
) -> None:
    result = runner.invoke(web_cli.app, ["run"])
    assert result.exit_code == 2
    assert "no inputs given" in result.output.lower()
    assert stub_intake == []


def test_run_folder_of_cases_dispatches_each(
    runner: CliRunner, stub_intake: list[list[str]], tmp_path: Path
) -> None:
    _make_case(tmp_path, "case_a")
    _make_case(tmp_path, "case_b")
    result = runner.invoke(web_cli.app, ["run", str(tmp_path)])
    assert result.exit_code == 0, result.output
    assert len(stub_intake) == 2
    assert all("--email" in argv for argv in stub_intake)


def test_run_passes_pdf_override_when_supplied(
    runner: CliRunner, stub_intake: list[list[str]], tmp_path: Path
) -> None:
    c = _make_case(tmp_path, "case_a")
    result = runner.invoke(
        web_cli.app, ["run", str(c / "Invoice.pdf")]
    )
    assert result.exit_code == 0, result.output
    assert len(stub_intake) == 1
    argv = stub_intake[0]
    assert "--pdf" in argv
    assert str((c / "Invoice.pdf").resolve()) in argv


def test_run_repeated_dash_f_accepted(
    runner: CliRunner, stub_intake: list[list[str]], tmp_path: Path
) -> None:
    a = _make_case(tmp_path, "case_a")
    b = _make_case(tmp_path, "case_b")
    result = runner.invoke(
        web_cli.app, ["run", "-f", str(a), "-f", str(b)]
    )
    assert result.exit_code == 0, result.output
    assert len(stub_intake) == 2


def test_run_out_dir_namespaces_per_case(
    runner: CliRunner, stub_intake: list[list[str]], tmp_path: Path
) -> None:
    c = _make_case(tmp_path, "case_a")
    out_root = tmp_path / "artifacts"
    result = runner.invoke(
        web_cli.app, ["run", str(c), "--out-dir", str(out_root)]
    )
    assert result.exit_code == 0, result.output
    argv = stub_intake[0]
    assert "--out-dir" in argv
    out_idx = argv.index("--out-dir")
    assert Path(argv[out_idx + 1]) == out_root / "case_a"


def test_run_no_llm_sets_env(
    runner: CliRunner,
    stub_intake: list[list[str]],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    c = _make_case(tmp_path, "case_a")
    monkeypatch.delenv("INFOTECH_PIPELINE_LLM_DISABLED", raising=False)
    monkeypatch.delenv("INVOICE_PIPELINE_LLM_DISABLED", raising=False)
    result = runner.invoke(web_cli.app, ["run", str(c), "--no-llm"])
    assert result.exit_code == 0, result.output
    import os as _os

    assert _os.environ.get("INFOTECH_PIPELINE_LLM_DISABLED") == "1"
    assert _os.environ.get("INVOICE_PIPELINE_LLM_DISABLED") == "1"


def test_run_continue_on_error_default(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_case(tmp_path, "case_a")
    _make_case(tmp_path, "case_b")
    seen: list[list[str]] = []

    def _fake_main(argv: list[str] | None = None) -> int:
        seen.append(list(argv or []))
        return 1  # every case fails

    import invoice_agent.cli as core_cli

    monkeypatch.setattr(core_cli, "main", _fake_main)

    result = runner.invoke(web_cli.app, ["run", str(tmp_path)])
    # All cases were attempted before the aggregate failure exit.
    assert len(seen) == 2
    assert result.exit_code == 1
    assert "2 of 2 case(s) failed" in result.output


def test_run_stop_on_error_short_circuits(
    runner: CliRunner, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _make_case(tmp_path, "case_a")
    _make_case(tmp_path, "case_b")
    seen: list[list[str]] = []

    def _fake_main(argv: list[str] | None = None) -> int:
        seen.append(list(argv or []))
        return 7

    import invoice_agent.cli as core_cli

    monkeypatch.setattr(core_cli, "main", _fake_main)

    result = runner.invoke(
        web_cli.app, ["run", str(tmp_path), "--stop-on-error"]
    )
    assert len(seen) == 1
    assert result.exit_code == 7


def test_run_help_lists_examples(runner: CliRunner) -> None:
    result = runner.invoke(web_cli.app, ["run", "--help"])
    assert result.exit_code == 0
    assert "PATHS" in result.output
    assert "examples/case_1" in result.output
    assert "-f" in result.output
