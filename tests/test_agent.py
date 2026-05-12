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


def test_agent_instructions_include_prompt_injection_guardrails() -> None:
    """The agent must carry an explicit trust boundary and risk-flag taxonomy.

    Behavior test: protects the 2026-style guardrails from silent regressions
    (someone shortening the prompt and accidentally dropping the trust
    boundary or the risk-flag vocabulary).
    """
    agent = build_agent()
    raw = (agent.instructions or "").lower()
    # Collapse all runs of whitespace so wrapped phrases like
    # "UNTRUSTED\nDATA" still match the expected token.
    import re

    instructions = re.sub(r"\s+", " ", raw)
    # Trust boundary language
    assert "untrusted data" in instructions
    assert (
        "ignore prior instructions" in instructions
        or "ignore previous instructions" in instructions
    )
    # Required risk-flag tags must all be mentioned at least once.
    for tag in (
        "bank_account_change_requested",
        "urgency_language",
        "vendor_domain_mismatch",
        "duplicate_invoice_number_suspected",
        "prompt_injection_attempt_in_document",
    ):
        assert tag in instructions, f"missing risk flag in agent prompt: {tag}"
