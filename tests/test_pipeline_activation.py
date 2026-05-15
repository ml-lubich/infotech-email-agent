"""End-to-end activation of the LLM pipeline shots (critic + injection).

These tests prove the existing 5-shot pipeline (in ``agent.run_intake``
+ ``pipeline.PipelineState``) actually runs in production once the CLI
hands an OpenAI client through. No network: every LLM seam is patched.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pytest

from invoice_agent import agent as agent_mod
from invoice_agent import cli as cli_mod
from invoice_agent.verifier import (
    Disagreement,
    FieldScore,
    VerificationReport,
)


# --- helpers ----------------------------------------------------------


class _R:
    final_output = "done"
    new_items: list[Any] = []
    raw_responses: list[Any] = []


def _seed_case(tmp_path: Path) -> Path:
    """Seed a working case dir with a REAL PDF (so post-agent shots can
    re-parse it through pdf_extract). We copy the case_1 fixture to keep
    the test offline and deterministic.
    """
    src_pdf = Path(__file__).resolve().parents[1] / "examples" / "case_1" / "Invoice.pdf"
    if not src_pdf.is_file():
        pytest.skip("case_1 fixture missing")
    case = tmp_path / "case_x"
    case.mkdir()
    (case / "Invoice.pdf").write_bytes(src_pdf.read_bytes())
    (case / "Email.json").write_text(
        json.dumps(
            {
                "Message": {
                    "From": "ap@vendor.example",
                    "Subject": "Inv",
                    "Body": "Please process. Net 30.",
                    "Attachments": [{"Name": "Invoice.pdf"}],
                }
            }
        ),
        encoding="utf-8",
    )
    return case


def _patch_runner_writes_outbound(monkeypatch: pytest.MonkeyPatch, out_dir: Path) -> None:
    """Make Runner.run_sync simulate the agent: write outbound files."""

    def _runner(_agent: Any, _prompt: str) -> _R:
        from invoice_agent.tools import write_notification_files

        write_notification_files(
            "## summary\nok\n",
            json.dumps(
                {
                    "vendor_name": "Acme",
                    "invoice_number": "INV-1",
                    "subtotal": 100.0,
                    "total_due": 110.0,
                    "taxes": [{"amount": 10.0}],
                    "risk_flags": [],
                }
            ),
            out_dir,
        )
        return _R()

    monkeypatch.setattr(agent_mod.Runner, "run_sync", staticmethod(_runner))


# --- CLI builds + passes a client ------------------------------------


class TestCliBuildsClient:
    def test_cli_builds_client_when_not_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        case = _seed_case(tmp_path)
        out_dir = tmp_path / "out" / "case_x"
        _patch_runner_writes_outbound(monkeypatch, out_dir)

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
        monkeypatch.delenv("INVOICE_PIPELINE_LLM_DISABLED", raising=False)
        monkeypatch.setattr(cli_mod, "load_dotenv", lambda *a, **k: False)
        monkeypatch.chdir(tmp_path)

        captured: dict[str, object] = {}

        def _spy_run_intake(**kwargs: object) -> Any:
            captured.update(kwargs)
            from invoice_agent.agent import IntakeResult

            return IntakeResult(
                agent_reply="done",
                artifacts={
                    "outbound_email.txt": out_dir / "outbound_email.txt",
                    "outbound_email.json": out_dir / "outbound_email.json",
                },
            )

        # Patch run_intake import as used in cli.py
        monkeypatch.setattr(cli_mod, "run_intake", _spy_run_intake)
        # Patch OpenAI to avoid real construction
        sentinel = object()
        monkeypatch.setattr(
            "invoice_agent.cli._build_openai_client", lambda log: sentinel
        )

        rc = cli_mod.main(["--email", str(case / "Email.json")])
        assert rc == 0
        # The client we built must reach run_intake.
        assert captured.get("openai_client") is sentinel

    def test_cli_skips_client_when_disabled(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        case = _seed_case(tmp_path)
        out_dir = tmp_path / "out" / "case_x"
        _patch_runner_writes_outbound(monkeypatch, out_dir)

        monkeypatch.setenv("OPENAI_API_KEY", "sk-test-not-real")
        monkeypatch.setenv("INVOICE_PIPELINE_LLM_DISABLED", "1")
        monkeypatch.setattr(cli_mod, "load_dotenv", lambda *a, **k: False)
        monkeypatch.chdir(tmp_path)

        captured: dict[str, object] = {}

        def _spy_run_intake(**kwargs: object) -> Any:
            captured.update(kwargs)
            from invoice_agent.agent import IntakeResult

            return IntakeResult(
                agent_reply="done",
                artifacts={
                    "outbound_email.txt": out_dir / "outbound_email.txt",
                    "outbound_email.json": out_dir / "outbound_email.json",
                },
            )

        monkeypatch.setattr(cli_mod, "run_intake", _spy_run_intake)
        rc = cli_mod.main(["--email", str(case / "Email.json")])
        assert rc == 0
        # Disabled → no client.
        assert captured.get("openai_client") is None

    def test_build_openai_client_returns_none_when_constructor_fails(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        monkeypatch.delenv("INVOICE_PIPELINE_LLM_DISABLED", raising=False)

        # Force the lazy `from openai import OpenAI` to blow up.
        import builtins

        real_import = builtins.__import__

        def _kaboom(name: str, *args: Any, **kwargs: Any) -> Any:
            if name == "openai":
                raise RuntimeError("simulated import failure")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _kaboom)

        log = logging.getLogger("invoice_agent.cli")
        caplog.set_level(logging.WARNING, logger="invoice_agent.cli")
        result = cli_mod._build_openai_client(log)
        assert result is None
        assert any("LLM shots will be SKIPPED" in r.message for r in caplog.records)


# --- run_intake actually invokes verifier + injection_screen ---------


class TestPipelineLlmShotsFire:
    def test_critic_and_injection_fire_when_client_provided(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        case = _seed_case(tmp_path)
        out_dir = tmp_path / "out" / "case_x"
        _patch_runner_writes_outbound(monkeypatch, out_dir)

        # Patch the LLM seams used by the post-agent shots.
        verify_calls: list[dict[str, object]] = []

        def _fake_verify(**kwargs: object) -> VerificationReport:
            verify_calls.append(kwargs)
            return VerificationReport(
                field_confidence=[
                    FieldScore(field="vendor_name", level="high"),
                    FieldScore(field="total_due", level="low"),
                ],
                disagreements=[
                    Disagreement(
                        field="total_due",
                        v1_value="110.0",
                        suggested_value="120.0",
                        reason="text shows 120",
                    )
                ],
                verifier_notes=["text vs image differ"],
            )

        injection_calls: list[dict[str, object]] = []

        def _fake_injection(**kwargs: object) -> list[str]:
            injection_calls.append(kwargs)
            return ["prompt_injection_attempt_in_document"]

        monkeypatch.setattr(agent_mod, "verify_extraction", _fake_verify)
        monkeypatch.setattr(agent_mod, "injection_screen", _fake_injection)

        sentinel_client = object()
        caplog.set_level(logging.INFO)

        result = agent_mod.run_intake(
            email_path=case / "Email.json",
            pdf_path=case / "Invoice.pdf",
            out_dir=out_dir,
            openai_client=sentinel_client,
        )

        # Both LLM shots ran and received our client.
        assert len(verify_calls) == 1
        assert verify_calls[0]["client"] is sentinel_client
        assert len(injection_calls) == 1
        assert injection_calls[0]["client"] is sentinel_client

        # Decision lines for every shot landed in the log.
        text = "\n".join(r.message for r in caplog.records)
        for fragment in (
            "name=pre_flight",
            "name=extract",
            "name=arithmetic_check",
            "name=critic_review",
            "name=injection_screen",
            "name=synthesis_finalise",
            "pipeline complete confidence=",
        ):
            assert fragment in text, f"missing log fragment: {fragment!r}"

        # The finalise step rewrote outbound_email.json with the envelope
        # and merged risk_flags additively.
        written = json.loads(
            (out_dir / "outbound_email.json").read_text(encoding="utf-8")
        )
        assert "pipeline" in written
        assert "confidence" in written["pipeline"]
        assert "shots" in written["pipeline"]
        assert any(s["name"] == "critic_review" for s in written["pipeline"]["shots"])
        # Critic + injection findings made it into risk_flags.
        assert "verifier_disagreement_total_due" in written["risk_flags"]
        # Citable-evidence gate: low_confidence grades and unanchored
        # injection aggregate tags are dropped from risk_flags. They
        # still surface as INFO log lines on the relevant shot.
        assert "low_confidence_total_due" not in written["risk_flags"]
        assert "prompt_injection_attempt_in_document" not in written["risk_flags"]

        # Banner prepended to outbound_email.txt.
        txt = (out_dir / "outbound_email.txt").read_text(encoding="utf-8")
        assert txt.startswith("Confidence: ")

        # Sanity: one of the artifacts the IntakeResult points to exists.
        assert result.artifacts["outbound_email.json"].is_file()

    def test_critic_skipped_when_no_client(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        case = _seed_case(tmp_path)
        out_dir = tmp_path / "out" / "case_x"
        _patch_runner_writes_outbound(monkeypatch, out_dir)

        # Sentinel: must NOT be called.
        def _boom(**_kw: object) -> Any:
            raise AssertionError("LLM shot must be SKIPPED when client is None")

        monkeypatch.setattr(agent_mod, "verify_extraction", _boom)
        monkeypatch.setattr(agent_mod, "injection_screen", _boom)

        caplog.set_level(logging.INFO)
        agent_mod.run_intake(
            email_path=case / "Email.json",
            pdf_path=case / "Invoice.pdf",
            out_dir=out_dir,
            openai_client=None,
        )
        text = "\n".join(r.message for r in caplog.records)
        assert "decision=SKIPPED" in text
        assert "name=critic_review" in text and "name=injection_screen" in text

    def test_critic_failure_recorded_as_FAIL_not_silent(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        case = _seed_case(tmp_path)
        out_dir = tmp_path / "out" / "case_x"
        _patch_runner_writes_outbound(monkeypatch, out_dir)

        def _boom(**_kw: object) -> Any:
            raise RuntimeError("verifier exploded")

        monkeypatch.setattr(agent_mod, "verify_extraction", _boom)
        monkeypatch.setattr(agent_mod, "injection_screen", lambda **_kw: [])

        caplog.set_level(logging.INFO)
        agent_mod.run_intake(
            email_path=case / "Email.json",
            pdf_path=case / "Invoice.pdf",
            out_dir=out_dir,
            openai_client=object(),
        )
        text = "\n".join(r.message for r in caplog.records)
        # Confirm FAIL surfaces; pipeline keeps going.
        assert "name=critic_review" in text and "decision=FAIL" in text
        assert "pipeline complete" in text
