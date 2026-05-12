"""Agent wiring: name, allow-listed model, and the two required tools."""

from __future__ import annotations

from invoice_agent.agent import build_agent
from invoice_agent.models import ALLOWED_MODELS


def test_build_agent_has_expected_name_and_model() -> None:
    agent = build_agent()
    assert agent.name == "InvoiceIntakeAgent"
    assert agent.model in ALLOWED_MODELS


def test_build_agent_registers_both_tools() -> None:
    agent = build_agent()
    tool_names = {t.name for t in agent.tools}
    assert tool_names == {
        "extract_invoice_from_pdf",
        "send_customer_service_notification",
    }
