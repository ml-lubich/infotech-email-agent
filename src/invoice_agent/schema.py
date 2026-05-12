"""Pydantic models for the structured invoice payload."""

from __future__ import annotations

from pydantic import BaseModel, Field


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
