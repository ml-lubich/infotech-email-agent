"""Pydantic models for the structured invoice payload."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


EvidenceSource = Literal[
    "email",
    "pdf_text",
    "extracted_payload",
    "verifier",
    "summary",
]


class Evidence(BaseModel):
    """One AP-facing pointer back to the substring that triggered a finding.

    Additive, optional, never required. Old consumers that ignore the
    field continue to work. Per `docs/API.md`, ``quote`` is a short
    (≤ 240 chars) substring from ``source``; ``location`` is a human
    hint such as ``"PDF page 1"``, ``"email.body"``, or
    ``"field: total_due"``.
    """

    finding: str = Field(description="Snake_case finding tag this evidence supports.")
    source: EvidenceSource = Field(description="Which source the quote came from.")
    quote: str = Field(description="Short substring from `source` (≤ 240 chars).")
    location: str | None = Field(
        default=None,
        description="Human hint like 'PDF page 1' or 'field: total_due'.",
    )


class LineItem(BaseModel):
    sku: str | None = None
    description: str | None = None
    quantity: float | None = None
    unit_price: float | None = None
    line_total: float | None = None


class TaxBreakdown(BaseModel):
    label: str
    amount: float | None = None
    rate: str | None = None


class ShipTo(BaseModel):
    location: str
    allocation: str | None = None


class InvoicePayload(BaseModel):
    vendor_name: str | None = None
    invoice_number: str | None = None
    invoice_date: str | None = None
    due_date: str | None = None
    payment_terms: str | None = None
    currency: str | None = None
    customer_po_number: str | None = None
    subtotal: float | None = None
    taxes: list[TaxBreakdown] = Field(default_factory=list)
    total_due: float | None = None
    line_items: list[LineItem] = Field(default_factory=list)
    ship_to: list[ShipTo] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source_warnings: list[str] = Field(default_factory=list)
    # Risk / fraud / duplicate / prompt-injection signals raised by the
    # extraction step. Free-form short strings — additive, never removed.
    # Examples: "bank_account_change_requested", "urgency_language",
    # "vendor_domain_mismatch", "duplicate_invoice_number_suspected",
    # "prompt_injection_attempt_in_document".
    risk_flags: list[str] = Field(default_factory=list)
