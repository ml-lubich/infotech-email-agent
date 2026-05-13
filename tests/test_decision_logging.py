"""Decision-trail logging coverage for `agent._log_run_decisions` and tools.

These tests exercise the comprehensive logging added to capture every
agent decision (tool call, tool output, assistant message, reasoning,
unknown items, final reply) without going through the live Agents SDK.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from invoice_agent import agent as agent_mod
from invoice_agent import tools as tools_mod
from invoice_agent.schema import InvoicePayload


# --- Minimal RunResult stand-ins (mirror SDK item class names) ----------


@dataclass
class _ToolCallItem:
    raw_item: Any
    tool_name: str | None = None
    call_id: str | None = None


@dataclass
class _ToolCallOutputItem:
    output: Any
    call_id: str | None = None


@dataclass
class _MessageOutputItem:
    raw_item: Any


@dataclass
class _ReasoningItem:
    raw_item: Any = None


@dataclass
class _UnknownItem:
    raw_item: Any = None


@dataclass
class _MsgPart:
    text: str | None


@dataclass
class _Msg:
    content: list[_MsgPart] = field(default_factory=list)


class _FakeResult:
    def __init__(
        self,
        new_items: list[Any],
        final_output: str = "ok",
        raw_responses: list[Any] | None = None,
    ) -> None:
        self.new_items = new_items
        self.final_output = final_output
        self.raw_responses = raw_responses if raw_responses is not None else [object()]


def test_log_run_decisions_covers_all_item_kinds(
    caplog: pytest.LogCaptureFixture,
) -> None:
    long_args = "x" * 700
    long_output_val = "y" * 700
    long_msg = "z" * 500
    items: list[Any] = [
        _ToolCallItem(
            raw_item={"name": "extract_invoice_from_pdf", "arguments": long_args},
            tool_name="extract_invoice_from_pdf",
            call_id="call_1",
        ),
        _ToolCallOutputItem(output={"big": long_output_val}, call_id="call_1"),
        _ToolCallItem(raw_item=type("R", (), {"arguments": "{}"})(), tool_name="t2"),
        _ToolCallOutputItem(output="short string output", call_id="unmatched"),
        _MessageOutputItem(
            raw_item=_Msg(content=[_MsgPart(text=long_msg), _MsgPart(text=None)])
        ),
        _MessageOutputItem(raw_item=_Msg(content=[])),
        _ReasoningItem(),
        _UnknownItem(),
    ]
    result = _FakeResult(items, final_output="x" * 500)

    caplog.set_level(logging.INFO, logger="invoice_agent.agent")
    agent_mod._log_run_decisions(result)

    text = "\n".join(r.message for r in caplog.records)
    assert "agent run completed turns=1 items=8" in text
    assert "tool_call name=extract_invoice_from_pdf call_id=call_1" in text
    assert "[+100 chars]" in text  # 700 - 600
    assert "tool_output name=extract_invoice_from_pdf call_id=call_1" in text
    assert "tool_call name=t2 call_id=#2" in text  # idx fallback
    assert "tool_output name=(unknown) call_id=unmatched" in text
    assert "assistant_message" in text
    assert "reasoning_item idx=6" in text
    assert "decision item kind=_UnknownItem idx=7" in text
    assert "agent final_reply=" in text


def test_log_run_decisions_handles_empty_and_missing_attrs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class _Bare:
        final_output = ""

    caplog.set_level(logging.INFO, logger="invoice_agent.agent")
    agent_mod._log_run_decisions(_Bare())
    text = "\n".join(r.message for r in caplog.records)
    assert "agent run completed turns=0 items=0" in text
    assert "final_reply" not in text


def test_run_intake_logs_email_decisions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = tmp_path / "case_log"
    case.mkdir()
    (case / "Invoice.pdf").write_bytes(b"%PDF-1.4 stub")
    (case / "Email.json").write_text(
        json.dumps(
            {
                "Message": {
                    "From": "ap@vendor.example",
                    "Subject": "Invoice",
                    "Body": "Please process\nurgent",
                    "PO": "PO-987",
                    "Attachments": [{"Name": "Invoice.pdf"}],
                }
            }
        ),
        encoding="utf-8",
    )

    class _R:
        final_output = "done"
        new_items: list[Any] = []
        raw_responses: list[Any] = []

    monkeypatch.setattr(
        agent_mod.Runner, "run_sync", staticmethod(lambda *_a, **_k: _R())
    )

    caplog.set_level(logging.INFO, logger="invoice_agent.agent")
    agent_mod.run_intake(
        email_path=case / "Email.json", pdf_path=None, out_dir=tmp_path / "out" / "case_log"
    )
    text = "\n".join(r.message for r in caplog.records)
    assert "email parsed sender='ap@vendor.example'" in text
    assert "email body_preview=" in text
    assert "PO_hint='PO-987'" in text
    assert "decision pdf_resolution=auto chose='Invoice.pdf'" in text


def test_run_intake_logs_explicit_pdf_branch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    case = tmp_path / "case_explicit"
    case.mkdir()
    pdf = case / "X.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    (case / "Email.json").write_text(json.dumps({"Attachments": []}), encoding="utf-8")

    class _R:
        final_output = "done"
        new_items: list[Any] = []
        raw_responses: list[Any] = []

    monkeypatch.setattr(
        agent_mod.Runner, "run_sync", staticmethod(lambda *_a, **_k: _R())
    )
    caplog.set_level(logging.INFO, logger="invoice_agent.agent")
    agent_mod.run_intake(
        email_path=case / "Email.json", pdf_path=pdf, out_dir=tmp_path / "out"
    )
    text = "\n".join(r.message for r in caplog.records)
    assert "pdf_resolution=explicit" in text


# --- tools.py: extraction + notify decision branches --------------------


class _FakeResp:
    def __init__(self, parsed: InvoicePayload | None) -> None:
        self.output_parsed = parsed
        self.output_text = ""


class _FakeOpenAI:
    def __init__(self, parsed: InvoicePayload | None) -> None:
        self.responses = type("R", (), {"parse": lambda _self, **_k: _FakeResp(parsed)})()


def _case1_pdf() -> Path:
    p = Path(__file__).resolve().parents[1] / "examples" / "case_1" / "Invoice.pdf"
    if not p.is_file():
        pytest.skip("case_1 fixture missing")
    return p


def test_extract_logs_risk_flags_and_warnings(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payload = InvoicePayload(
        vendor_name="Acme",
        invoice_number="INV-1",
        risk_flags=["urgency_language"],
        source_warnings=["totals_disagree"],
    )
    monkeypatch.setattr(tools_mod, "OpenAI", lambda: _FakeOpenAI(payload))
    caplog.set_level(logging.INFO, logger="invoice_agent.tools")
    tools_mod._extract_invoice_from_pdf_impl(str(_case1_pdf()))
    text = "\n".join(r.message for r in caplog.records)
    assert "RISK FLAGS=['urgency_language']" in text
    assert "source_warnings=['totals_disagree']" in text


def test_extract_logs_when_no_risk_flags(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    payload = InvoicePayload(vendor_name="Acme", invoice_number="INV-2")
    monkeypatch.setattr(tools_mod, "OpenAI", lambda: _FakeOpenAI(payload))
    caplog.set_level(logging.INFO, logger="invoice_agent.tools")
    tools_mod._extract_invoice_from_pdf_impl(str(_case1_pdf()))
    text = "\n".join(r.message for r in caplog.records)
    assert "extract risk_flags=[] (none raised by extractor)" in text


def test_notify_logs_decision_summary_with_flags_and_email_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setenv(tools_mod.OUT_DIR_ENV, str(tmp_path))
    payload = json.dumps(
        {
            "vendor_name": "Acme",
            "invoice_number": "INV-9",
            "currency": "EUR",
            "total_due": 123.45,
            "risk_flags": ["bank_account_change_requested"],
            "source_warnings": ["image_only_field"],
            "email_context": {"po_number": "PO-1", "sender_domain": "vendor.example"},
        }
    )
    caplog.set_level(logging.INFO, logger="invoice_agent.tools")
    tools_mod._send_customer_service_notification_impl("# summary\n", payload)
    text = "\n".join(r.message for r in caplog.records)
    assert "notify decision vendor='Acme'" in text
    assert "FORWARDED risk_flags=['bank_account_change_requested']" in text
    assert "forwarded source_warnings=['image_only_field']" in text


def test_notify_handles_invalid_payload_json_preview_branch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tools_mod.OUT_DIR_ENV, str(tmp_path))
    # Invalid JSON: preview branch sets parsed_preview=None, then
    # write_notification_files raises ValueError on the same input.
    with pytest.raises(ValueError, match="payload_json is not valid JSON"):
        tools_mod._send_customer_service_notification_impl("s", "{not json")
