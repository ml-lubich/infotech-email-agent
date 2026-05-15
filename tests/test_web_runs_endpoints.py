"""Tests for /api/runs, /api/runs/{id}, and /api/runs/{id}/download.

These pin the persisted-runs surface added so that the dashboard can:
* list previous runs that survived a container restart,
* re-hydrate one into the result panel without re-running the pipeline,
* and download the whole case folder as a single zip.

The tests do not exercise the OpenAI pipeline; they pre-seed the runs
directory with a synthetic case folder and hit the endpoints through
``starlette.testclient``.
"""

from __future__ import annotations

import io
import json
import zipfile
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from invoice_agent_web import main as web_main


@pytest.fixture
def isolated_runs_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv(web_main.RUNS_DIR_ENV, str(runs))
    return runs


@pytest.fixture
def client(isolated_runs_dir: Path) -> TestClient:  # noqa: ARG001 - fixture wiring
    return TestClient(web_main.create_app())


def _seed_case(runs: Path, name: str) -> Path:
    case = runs / name
    case.mkdir()
    (case / "Email.json").write_text(
        json.dumps({"Message": {"Subject": "test"}}), encoding="utf-8"
    )
    (case / "outbound_email.txt").write_text("AP brief here", encoding="utf-8")
    (case / "outbound_email.json").write_text(
        json.dumps({"vendor_name": "Acme", "pipeline": {"confidence": 0.9}}),
        encoding="utf-8",
    )
    (case / "run.log").write_text("INFO line one\nINFO line two\n", encoding="utf-8")
    return case


def test_list_runs_returns_seeded_cases_newest_first(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    older = _seed_case(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")
    newer = _seed_case(isolated_runs_dir, "20260202_000000_beta_bbbbbb")
    # Force mtime ordering — alpha older than beta.
    import os

    os.utime(older, (1, 1))
    os.utime(newer, (2, 2))

    resp = client.get("/api/runs")
    assert resp.status_code == 200, resp.text
    runs = resp.json()["runs"]
    assert [r["case_id"] for r in runs] == [newer.name, older.name]
    assert all(r["has_outbound"] for r in runs)
    assert all(r["file_count"] >= 4 for r in runs)
    assert all(r["size_bytes"] > 0 for r in runs)


def test_get_run_rehydrates_outbound(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    resp = client.get(f"/api/runs/{case.name}")
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["case_id"] == case.name
    assert body["outbound_text"] == "AP brief here"
    assert body["outbound_json"]["vendor_name"] == "Acme"
    assert "INFO line two" in body["log_tail"]
    # artefact map should contain every file we seeded.
    assert set(body["artifacts"]) >= {
        "Email.json",
        "outbound_email.txt",
        "outbound_email.json",
        "run.log",
    }


def test_download_run_streams_zip_with_all_files(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = _seed_case(isolated_runs_dir, "20260101_000000_alpha_aaaaaa")

    resp = client.get(f"/api/runs/{case.name}/download")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"] == "application/zip"
    assert f'filename="{case.name}.zip"' in resp.headers["content-disposition"]

    with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
        names = set(zf.namelist())
    assert names >= {
        "Email.json",
        "outbound_email.txt",
        "outbound_email.json",
        "run.log",
    }


@pytest.mark.parametrize(
    "bad",
    ["..", "../etc", "with space"],
)
def test_get_run_rejects_invalid_case_id(
    client: TestClient, isolated_runs_dir: Path, bad: str  # noqa: ARG001
) -> None:
    resp = client.get(f"/api/runs/{bad}")
    # FastAPI itself rejects empty path segments with 404 before we run.
    assert resp.status_code in (400, 404)


def test_get_run_404_for_unknown_case(
    client: TestClient, isolated_runs_dir: Path  # noqa: ARG001
) -> None:
    resp = client.get("/api/runs/20260101_000000_nope_zzzzzz")
    assert resp.status_code == 404
