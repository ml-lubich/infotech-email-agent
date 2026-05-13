"""Pass-2 verifier: structured critique of an extracted invoice payload.

Tests pin the contract before implementation lands (TDD). Network is
never touched — the OpenAI client is injected.
"""

from __future__ import annotations

import json
import logging

import pytest

from invoice_agent import verifier as v


# --------------------------------------------------------------- model shape


class TestVerificationReportModel:
    def test_default_fields(self) -> None:
        r = v.VerificationReport()
        assert r.field_confidence == []
        assert r.disagreements == []
        assert r.verifier_notes == []

    def test_disagreement_required_fields(self) -> None:
        d = v.Disagreement(
            field="total_due",
            v1_value="1234.56",
            suggested_value="1243.56",
            reason="text PDF shows 1243.56",
        )
        assert d.field == "total_due"
        assert d.reason

    def test_field_confidence_rejects_unknown_level(self) -> None:
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            v.VerificationReport(
                field_confidence=[v.FieldScore(field="vendor_name", level="perfect")]  # type: ignore[arg-type]
            )


# ----------------------------------------------------------- prompt builder


class TestPromptBuilder:
    def test_user_payload_contains_json_and_text(self) -> None:
        text = v.build_verifier_user_payload(
            payload_json='{"vendor_name":"Acme"}',
            pdf_text="Acme Inc.\nInvoice INV-1\nTotal: 100.00",
        )
        assert "Acme" in text
        assert "INV-1" in text
        assert '{"vendor_name":"Acme"}' in text

    def test_system_prompt_forbids_re_extraction(self) -> None:
        # Verifier must be instructed NOT to overwrite, only annotate.
        sp = v.VERIFIER_SYSTEM
        low = sp.lower()
        assert "verifier" in low or "verify" in low
        # Must mention the three confidence buckets we model.
        assert "high" in low and "medium" in low and "low" in low
        # Must forbid re-extraction / value rewrites.
        assert any(
            phrase in low
            for phrase in (
                "do not re-extract",
                "do not overwrite",
                "never overwrite",
                "annotate",
            )
        ), "verifier system prompt must forbid re-extraction / overwrites"


# ------------------------------------------------------------------ runtime


class _FakeResp:
    def __init__(self, parsed: v.VerificationReport | None) -> None:
        self.output_parsed = parsed
        self.output_text = ""


class _FakeOpenAI:
    def __init__(self, parsed: v.VerificationReport | None) -> None:
        captured: dict[str, object] = {}

        def _parse(**kwargs: object) -> _FakeResp:
            captured.update(kwargs)
            return _FakeResp(parsed)

        self.captured = captured
        self.responses = type("R", (), {"parse": lambda _self, **kw: _parse(**kw)})()


class TestVerifyExtraction:
    def test_returns_parsed_report(self) -> None:
        report = v.VerificationReport(
            field_confidence=[
                v.FieldScore(field="vendor_name", level="high"),
                v.FieldScore(field="total_due", level="low"),
            ],
            disagreements=[
                v.Disagreement(
                    field="total_due",
                    v1_value="100.00",
                    suggested_value="110.00",
                    reason="PDF shows 110.00",
                )
            ],
            verifier_notes=["totals row appears in image, not text"],
        )
        client = _FakeOpenAI(report)
        out = v.verify_extraction(
            payload_json='{"vendor_name":"Acme","total_due":100.0}',
            pdf_text="Acme Inc.\nTotal: 110.00",
            client=client,
        )
        levels = {s.field: s.level for s in out.field_confidence}
        assert levels["total_due"] == "low"
        assert out.disagreements[0].field == "total_due"
        # Confirm it actually called the SDK with our model + text_format.
        assert client.captured["model"] == v.DEFAULT_VERIFIER_MODEL
        assert client.captured["text_format"] is v.VerificationReport

    def test_uses_explicit_model_override(self) -> None:
        client = _FakeOpenAI(v.VerificationReport())
        v.verify_extraction(
            payload_json="{}",
            pdf_text="x",
            client=client,
            model="gpt-5-mini",  # allow-listed override
        )
        assert client.captured["model"] == "gpt-5-mini"

    def test_rejects_non_allowlisted_model(self) -> None:
        with pytest.raises(ValueError, match="not allow-listed"):
            v.verify_extraction(
                payload_json="{}",
                pdf_text="x",
                client=_FakeOpenAI(v.VerificationReport()),
                model="gpt-4o",
            )

    def test_raises_when_parsed_is_none(self) -> None:
        with pytest.raises(RuntimeError, match="no parsed"):
            v.verify_extraction(
                payload_json="{}",
                pdf_text="x",
                client=_FakeOpenAI(None),
            )

    def test_logs_decision_lines(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        client = _FakeOpenAI(
            v.VerificationReport(
                field_confidence=[
                    v.FieldScore(field="a", level="high"),
                    v.FieldScore(field="b", level="medium"),
                    v.FieldScore(field="c", level="low"),
                ],
                disagreements=[
                    v.Disagreement(
                        field="invoice_number",
                        v1_value="INV-1",
                        suggested_value="INV-001",
                        reason="leading zeros differ",
                    )
                ],
            )
        )
        caplog.set_level(logging.INFO, logger="invoice_agent.verifier")
        v.verify_extraction(
            payload_json='{"x":1}',
            pdf_text="some pdf text",
            client=client,
        )
        text = "\n".join(r.message for r in caplog.records)
        assert "decision step=verify action=invoke model=gpt-5-nano" in text
        assert "decision step=verify action=report high=1 medium=1 low=1 disagreements=1" in text
        assert "decision step=verify action=disagreement field=invoice_number" in text
