"""Tests for /api/runs/{case_id}/file/{filename} — the source-packet
viewer endpoint that lets the dashboard render the original Email.json
and invoice PDF the agent worked from.

Pin:
* Email.json + invoice PDF stream back with correct media types and
  inline content-disposition (so browsers render them in place).
* IntakeResponse exposes ``email_filename`` / ``pdf_filename`` so the
  UI knows which artefacts to fetch.
* Path traversal, slashes, missing files, and disallowed extensions
  (run.log, outbound_email.json) are rejected with explicit 4xx errors
  — never fall back to silent 500s, never escape the case dir.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from invoice_agent_web import main as web_main


@pytest.fixture
def isolated_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv(web_main.RUNS_DIR_ENV, str(runs))
    return runs


@pytest.fixture
def client(isolated_runs_dir: Path) -> TestClient:  # noqa: ARG001 - wiring
    return TestClient(web_main.create_app())


# Small non-empty PDF so FileResponse has real bytes to stream and the
# %PDF magic byte check at the start passes if anything ever inspects it.
_MIN_PDF = b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n1 0 obj<<>>endobj\ntrailer<<>>\n%%EOF\n"


def _seed_case_with_sources(runs: Path, name: str) -> Path:
    case = runs / name
    case.mkdir()
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Subject": "Hello", "Body": "Body text"}}),
        encoding="utf-8",
    )
    (case / "Invoice.pdf").write_bytes(_MIN_PDF)
    (case / "outbound_email.txt").write_text("AP brief", encoding="utf-8")
    (case / "outbound_email.json").write_text(
        json.dumps({"vendor_name": "Acme"}), encoding="utf-8"
    )
    (case / "run.log").write_text("INFO log line\n", encoding="utf-8")
    return case


def test_get_run_exposes_source_filenames(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    resp = client.get(f"/api/runs/{case.name}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email_filename"] == "Email.json"
    assert body["pdf_filename"] == "Invoice.pdf"


def test_get_run_handles_missing_pdf(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = isolated_runs_dir / "20260101_000000_text_only_aaaaaa"
    case.mkdir()
    (case / "Email.json").write_text(json.dumps({"Message": {}}), encoding="utf-8")
    (case / "outbound_email.txt").write_text("brief", encoding="utf-8")
    (case / "outbound_email.json").write_text("{}", encoding="utf-8")

    resp = client.get(f"/api/runs/{case.name}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["email_filename"] == "Email.json"
    assert body["pdf_filename"] is None


def test_fetch_email_json_returns_parsed_payload(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    resp = client.get(f"/api/runs/{case.name}/file/Email.json")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("application/json")
    assert 'inline; filename="Email.json"' in resp.headers["content-disposition"]
    assert resp.json()["Message"]["Subject"] == "Hello"


def test_fetch_pdf_streams_with_correct_mime_and_inline_disposition(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    resp = client.get(f"/api/runs/{case.name}/file/Invoice.pdf")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/pdf"
    assert 'inline; filename="Invoice.pdf"' in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")


def test_fetch_rejects_disallowed_extension_for_run_log(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    # run.log lives in the case dir but is NOT a source artefact; the
    # endpoint must refuse it (use the log_tail field instead).
    resp = client.get(f"/api/runs/{case.name}/file/run.log")
    assert resp.status_code == 415, resp.text


def test_fetch_404_for_unknown_filename(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    resp = client.get(f"/api/runs/{case.name}/file/nope.pdf")
    assert resp.status_code == 404, resp.text


def test_fetch_404_for_unknown_case(
    client: TestClient, isolated_runs_dir: Path  # noqa: ARG001
) -> None:
    resp = client.get("/api/runs/20260101_000000_nope_zzzzzz/file/Email.json")
    assert resp.status_code == 404, resp.text


@pytest.mark.parametrize(
    "bad_name",
    [
        "..%2FEmail.json",     # url-encoded traversal
        "sub%2Ffile.json",     # url-encoded slash
    ],
)
def test_fetch_rejects_path_traversal(
    client: TestClient, isolated_runs_dir: Path, bad_name: str
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")
    # An attacker outside file lives next to the case dir.
    (isolated_runs_dir / "Email.json").write_text("{}", encoding="utf-8")

    resp = client.get(f"/api/runs/{case.name}/file/{bad_name}")
    assert resp.status_code in (400, 404), resp.text


def test_fetch_rejects_literal_dotdot_in_filename(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case_with_sources(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")
    # Starlette/httpx will route this; our handler must reject ``..``.
    resp = client.get(f"/api/runs/{case.name}/file/..%2E%2FEmail.json")
    assert resp.status_code in (400, 404), resp.text
