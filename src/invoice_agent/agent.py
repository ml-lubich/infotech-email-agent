"""Agents SDK wiring for the invoice intake workflow."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from agents import Agent, Runner

from invoice_agent.models import DEFAULT_AGENT_MODEL, resolve_model
from invoice_agent.tools import (
    OUT_DIR_ENV,
    extract_invoice_from_pdf,
    send_customer_service_notification,
)

if TYPE_CHECKING:
    from openai import OpenAI


@dataclass(frozen=True)
class IntakeResult:
    agent_reply: str
    artifacts: dict[str, Path]

# Allow-listed: only `gpt-5-mini` or `gpt-5-nano` (assignment constraint).
_AGENT_MODEL = resolve_model(os.getenv("INVOICE_AGENT_MODEL"), DEFAULT_AGENT_MODEL)

_INSTRUCTIONS = """\
You are an invoice-intake agent for an Accounts Payable team.

Workflow (do this once, then stop):
1. Read the inbound email JSON the user provides. Note the attachment filename
   and the absolute path the user gives you.
2. Call `extract_invoice_from_pdf` exactly once with that PDF path. It returns
   a JSON string of structured invoice fields (including any data that was
   only visible inside images, e.g. the invoice number stamp).
3. Combine the email context (PO number, cost centres, delivery notes,
   duplicate warnings) with the extracted invoice JSON.
4. Call `send_customer_service_notification` exactly once with:
     - summary_markdown: a clean human-readable briefing for AP. Use sections
       for Vendor, Invoice, PO match, Totals & taxes, Line items, Ship-to /
       allocations, Notes & flags.
     - payload_json: a single JSON string merging the extracted invoice
       payload plus an "email_context" object containing PO number, cost
       centres, ship-to sites mentioned in the email, and any duplicate /
       receiving notes.

Rules:
- Do NOT call any tool more than once.
- Do NOT fabricate values. If a field is missing, leave it null and add a
  short note in the summary.
- Keep prompts and outputs concise to conserve tokens.

When done, reply with a one-line confirmation that the notification was sent.
"""


def build_agent() -> Agent:
    return Agent(
        name="InvoiceIntakeAgent",
        model=_AGENT_MODEL,
        instructions=_INSTRUCTIONS,
        tools=[extract_invoice_from_pdf, send_customer_service_notification],
    )


def run_intake(
    email_path: Path,
    pdf_path: Path | None = None,
    out_dir: Path = Path("."),
    openai_client: "OpenAI | None" = None,
) -> IntakeResult:
    """Run the agent against an email JSON file and its PDF attachment.

    Args:
        email_path: Path to the inbound email JSON file.
        pdf_path: Path to the invoice PDF. If None, resolved from the email's
            ``Attachments`` array (looking for a sibling ``*.pdf``).
        out_dir: Directory to write ``outbound_email.{txt,json}``.
        openai_client: Optional pre-built OpenAI client. Currently unused at
            this layer (tools instantiate their own client from env), but kept
            so the caller can validate credentials before invoking the agent.

    Returns:
        IntakeResult with the agent's final text and the written artifacts.
    """
    del openai_client  # tools build their own client from OPENAI_API_KEY
    email_path = email_path.expanduser().resolve()
    if not email_path.is_file():
        raise FileNotFoundError(f"Email file not found: {email_path}")

    email_data = json.loads(email_path.read_text(encoding="utf-8"))
    message = email_data.get("Message", email_data)

    if pdf_path is None:
        attachments = message.get("Attachments", []) or []
        pdf_name = next(
            (
                a.get("Name")
                for a in attachments
                if (a.get("Name") or "").lower().endswith(".pdf")
            ),
            None,
        )
        if not pdf_name:
            raise ValueError("No PDF attachment found in email.")
        pdf_path = (email_path.parent / pdf_name).resolve()

    pdf_path = pdf_path.expanduser().resolve()
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF attachment not found: {pdf_path}")

    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    # Tools reaOUT_DIR_ENVrtifacts; scoped to this run.
    os.environ["INVOICE_OUT_DIR"] = str(out_dir)

    user_prompt = (
        "Inbound email JSON (verbatim):\n"
        f"{json.dumps(message, ensure_ascii=False)}\n\n"
        f"The PDF attachment is available locally at: {pdf_path}\n"
        "Run the intake workflow."
    )

    agent = build_agent()
    result = Runner.run_sync(agent, user_prompt)

    return IntakeResult(
        agent_reply=result.final_output or "",
        artifacts={
            "outbound_email.txt": out_dir / "outbound_email.txt",
            "outbound_email.json": out_dir / "outbound_email.json",
        },
    )
