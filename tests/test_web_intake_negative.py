"""Negative-input + crash-resilience tests for the web intake surface.

These pin the contract that the FastAPI server NEVER crashes the
process on bad / malicious / unexpected input. Every failure path must
surface as a structured ``HTTPException`` with a documented status code
and a JSON body (so the dashboard renders an error, not a stack trace,
and so a process supervisor never has to "auto-restart" the server).

Covers gaps not exercised by ``test_web_runs_endpoints.py``:

* ``/api/intake``  upload: bad filename, non-JSON bytes, non-UTF-8
  bytes, bad PDF filename, missing ``OPENAI_API_KEY``, and pipeline
  crashes that bubble up as 400 / 422 / 500.
* ``/api/intake/example``: path-traversal names, unknown names, and a
  happy path with the pipeline stubbed (so no OpenAI credit).
* ``/api/health`` and ``/api/examples`` smoke shape.
* ``/api/runs/{case_id}/download`` for an unknown case.
* ``/api/runs/{case_id}`` when ``outbound_email.json`` is malformed
  (must return JSON 500, not crash the worker).

The OpenAI pipeline is never invoked: ``run_intake`` is monkeypatched
and ``INVOICE_PIPELINE_LLM_DISABLED=1`` is set so the OpenAI client
constructor is skipped.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from starlette.testclient import TestClient

from invoice_agent.agent import IntakeResult
from invoice_agent_web import main as web_main


# --------------------------------------------------------------------- #
# fixtures
# --------------------------------------------------------------------- #


@pytest.fixture
def isolated_runs_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    runs = tmp_path / "runs"
    runs.mkdir()
    monkeypatch.setenv(web_main.RUNS_DIR_ENV, str(runs))
    # Skip the real OpenAI client constructor; ``_build_openai_client``
    # honours this flag and returns None without importing the SDK.
    monkeypatch.setenv("INVOICE_PIPELINE_LLM_DISABLED", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key-not-used")
    return runs


@pytest.fixture
def client(isolated_runs_dir: Path) -> TestClient:  # noqa: ARG001 - wiring
    return TestClient(web_main.create_app())


def _valid_email_bytes() -> bytes:
    return json.dumps(
        {"Message": {"Subject": "neg-test", "From": "ap@example.com"}}
    ).encode("utf-8")


def _stub_run_intake_ok(case_dir: Path) -> IntakeResult:
    """Pretend ``run_intake`` succeeded: write the outbound files."""
    (case_dir / "outbound_email.txt").write_text("AP brief", encoding="utf-8")
    (case_dir / "outbound_email.json").write_text(
        json.dumps({"vendor_name": "StubVendor", "pipeline": {"confidence": 0.5}}),
        encoding="utf-8",
    )
    return IntakeResult(agent_reply="ok", artifacts={"outbound": case_dir})


# --------------------------------------------------------------------- #
# /api/intake — input validation
# --------------------------------------------------------------------- #


class TestIntakeUploadValidation:
    def test_rejects_email_with_non_json_filename(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intake",
            files={"email": ("Email.txt", _valid_email_bytes(), "text/plain")},
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "email upload must be a .json file"

    def test_rejects_email_with_invalid_json_payload(
        self, client: TestClient, isolated_runs_dir: Path
    ) -> None:
        resp = client.post(
            "/api/intake",
            files={"email": ("Email.json", b"{not: valid json", "application/json")},
        )
        assert resp.status_code == 400
        assert "not valid JSON" in resp.json()["error"]
        # Crash-resilience invariant: the partially-created case dir
        # must be cleaned up so we don't accumulate empty folders.
        assert list(isolated_runs_dir.iterdir()) == []

    def test_rejects_email_with_non_utf8_bytes(
        self, client: TestClient, isolated_runs_dir: Path
    ) -> None:
        resp = client.post(
            "/api/intake",
            files={"email": ("Email.json", b"\xff\xfe\xfd", "application/json")},
        )
        assert resp.status_code == 400
        assert "not valid JSON" in resp.json()["error"]
        assert list(isolated_runs_dir.iterdir()) == []

    def test_rejects_pdf_with_non_pdf_filename(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Stub the pipeline so we don't need an actual PDF — we only
        # care that the filename guard fires before pipeline dispatch.
        monkeypatch.setattr(
            web_main, "run_intake", lambda **_kw: _stub_run_intake_ok(_kw["out_dir"])
        )
        resp = client.post(
            "/api/intake",
            files={
                "email": ("Email.json", _valid_email_bytes(), "application/json"),
                "pdf": ("Invoice.exe", b"%PDF-1.4 fake", "application/octet-stream"),
            },
        )
        assert resp.status_code == 400
        assert resp.json()["error"] == "pdf upload must be a .pdf"

    def test_returns_503_when_openai_key_missing(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        resp = client.post(
            "/api/intake",
            files={"email": ("Email.json", _valid_email_bytes(), "application/json")},
        )
        assert resp.status_code == 503
        assert "OPENAI_API_KEY" in resp.json()["error"]


# --------------------------------------------------------------------- #
# /api/intake — pipeline-crash translation (server stays alive)
# --------------------------------------------------------------------- #


class TestIntakePipelineCrashes:
    """Each pipeline failure mode must become a JSON HTTP error, not a 500
    that takes down the worker. A test_client request that survives and
    returns a parseable JSON envelope IS the server-alive proof."""

    def _post_valid_email(self, client: TestClient) -> Any:
        return client.post(
            "/api/intake",
            files={"email": ("Email.json", _valid_email_bytes(), "application/json")},
        )

    def test_file_not_found_becomes_400(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(**_kw: Any) -> IntakeResult:
            raise FileNotFoundError("missing PDF: /nope.pdf")

        monkeypatch.setattr(web_main, "run_intake", _boom)
        resp = self._post_valid_email(client)
        assert resp.status_code == 400
        assert "missing PDF" in resp.json()["error"]

    def test_value_error_becomes_422(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(**_kw: Any) -> IntakeResult:
            raise ValueError("invoice payload failed schema validation")

        monkeypatch.setattr(web_main, "run_intake", _boom)
        resp = self._post_valid_email(client)
        assert resp.status_code == 422
        assert "schema validation" in resp.json()["error"]

    def test_unexpected_exception_becomes_500_envelope(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def _boom(**_kw: Any) -> IntakeResult:
            raise RuntimeError("transient OCR engine fault")

        monkeypatch.setattr(web_main, "run_intake", _boom)
        resp = self._post_valid_email(client)
        assert resp.status_code == 500
        body = resp.json()
        # Structured envelope only — no raw stack trace, no HTML page.
        assert set(body) == {"error", "status"}
        assert "transient OCR engine fault" in body["error"]

    def test_server_recovers_after_crash_and_handles_next_request(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A crashing request MUST NOT poison subsequent requests."""
        calls: list[str] = []

        def _flaky(**kw: Any) -> IntakeResult:
            calls.append("hit")
            if len(calls) == 1:
                raise RuntimeError("first call boom")
            return _stub_run_intake_ok(kw["out_dir"])

        monkeypatch.setattr(web_main, "run_intake", _flaky)

        first = self._post_valid_email(client)
        assert first.status_code == 500

        second = self._post_valid_email(client)
        assert second.status_code == 200, second.text
        assert second.json()["outbound_text"] == "AP brief"
        assert len(calls) == 2


# --------------------------------------------------------------------- #
# /api/intake/example — name validation + happy path (no OpenAI)
# --------------------------------------------------------------------- #


class TestIntakeExample:
    @pytest.mark.parametrize(
        "bad_name",
        ["../etc", "case_1/../..", "foo/bar", "..", "."],
    )
    def test_rejects_traversal_names(
        self, client: TestClient, bad_name: str
    ) -> None:
        resp = client.post("/api/intake/example", data={"name": bad_name})
        # 400 for traversal, 404 for "." which has no Email.json.
        assert resp.status_code in (400, 404)
        assert "error" in resp.json()

    def test_unknown_example_name_returns_404(self, client: TestClient) -> None:
        resp = client.post(
            "/api/intake/example", data={"name": "case_does_not_exist"}
        )
        assert resp.status_code == 404
        assert "not found" in resp.json()["error"]

    def test_happy_path_with_stubbed_pipeline(
        self,
        client: TestClient,
        isolated_runs_dir: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        monkeypatch.setattr(
            web_main, "run_intake", lambda **kw: _stub_run_intake_ok(kw["out_dir"])
        )
        # case_1 is a real shipped example with Email.json + Invoice.pdf.
        resp = client.post("/api/intake/example", data={"name": "case_1"})
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["outbound_text"] == "AP brief"
        assert body["outbound_json"]["vendor_name"] == "StubVendor"
        # Case dir was created under the isolated runs dir.
        case_dirs = list(isolated_runs_dir.iterdir())
        assert len(case_dirs) == 1
        assert (case_dirs[0] / "Email.json").is_file()


# --------------------------------------------------------------------- #
# /api/runs/{case_id}/download — unknown id
# --------------------------------------------------------------------- #


def test_download_unknown_case_returns_404(
    client: TestClient, isolated_runs_dir: Path  # noqa: ARG001
) -> None:
    resp = client.get("/api/runs/20990101_000000_ghost_zzzzzz/download")
    assert resp.status_code == 404
    assert "not found" in resp.json()["error"]


@pytest.mark.parametrize("bad", ["..", "../etc", "with space", "a/b"])
def test_download_rejects_invalid_case_id(
    client: TestClient, isolated_runs_dir: Path, bad: str  # noqa: ARG001
) -> None:
    resp = client.get(f"/api/runs/{bad}/download")
    assert resp.status_code in (400, 404)


# --------------------------------------------------------------------- #
# /api/runs/{case_id} — corrupted artefact must not crash worker
# --------------------------------------------------------------------- #


def test_get_run_with_malformed_outbound_json_returns_500_envelope(
    client: TestClient, isolated_runs_dir: Path
) -> None:
    case = isolated_runs_dir / "20260101_000000_corrupt_aaaaaa"
    case.mkdir()
    (case / "Email.json").write_text("{}", encoding="utf-8")
    (case / "outbound_email.txt").write_text("brief", encoding="utf-8")
    # Deliberately corrupt JSON.
    (case / "outbound_email.json").write_text("{not json", encoding="utf-8")

    resp = client.get(f"/api/runs/{case.name}")
    assert resp.status_code == 500
    body = resp.json()
    assert set(body) == {"error", "status"}
    assert "outbound_email.json" in body["error"]


# --------------------------------------------------------------------- #
# /api/health and /api/examples — shape smoke
# --------------------------------------------------------------------- #


def test_health_returns_documented_shape(
    client: TestClient, isolated_runs_dir: Path  # noqa: ARG001
) -> None:
    resp = client.get("/api/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["llm_enabled"] is False  # disabled via fixture
    assert body["has_openai_key"] is True
    assert isinstance(body["runs_dir"], str)


def test_examples_list_returns_shipped_cases(client: TestClient) -> None:
    resp = client.get("/api/examples")
    assert resp.status_code == 200
    cases = resp.json()["cases"]
    assert isinstance(cases, list)
    # case_1 is a fixture every contributor has; every entry must
    # carry the documented shape.
    assert any(c["name"] == "case_1" for c in cases)
    for case in cases:
        assert set(case) >= {"name", "has_pdf", "subject"}
        assert isinstance(case["name"], str)
        assert isinstance(case["has_pdf"], bool)
