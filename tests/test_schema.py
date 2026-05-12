"""InvoicePayload schema sanity (defaults, round-trip)."""

from __future__ import annotations

import json

from invoice_agent.schema import InvoicePayload, LineItem, ShipTo, TaxBreakdown


def test_invoice_payload_defaults_are_empty_and_null() -> None:
    p = InvoicePayload()
    assert p.vendor_name is None
    assert p.invoice_number is None
    assert p.total_due is None
    assert p.line_items == []
    assert p.taxes == []
    assert p.ship_to == []
    assert p.notes == []
    assert p.source_warnings == []
    assert p.risk_flags == []


def test_invoice_payload_round_trip_preserves_fields() -> None:
    original = InvoicePayload(
        vendor_name="Acme Co",
        invoice_number="INV-001",
        currency="USD",
        subtotal=100.0,
        taxes=[TaxBreakdown(label="GST", amount=5.0, rate="5%")],
        total_due=105.0,
        line_items=[
            LineItem(sku="A1", description="Widget", quantity=2, unit_price=50.0, line_total=100.0)
        ],
        ship_to=[ShipTo(location="Toronto HQ", allocation="100%")],
        notes=["net 30"],
        source_warnings=["image vs text mismatch on invoice_number"],
        risk_flags=[
            "bank_account_change_requested",
            "prompt_injection_attempt_in_document",
        ],
    )
    blob = original.model_dump_json()
    rebuilt = InvoicePayload.model_validate(json.loads(blob))
    assert rebuilt == original


def test_invoice_payload_accepts_partial_dict() -> None:
    rebuilt = InvoicePayload.model_validate(
        {"vendor_name": "X", "line_items": [{"sku": "S"}]}
    )
    assert rebuilt.vendor_name == "X"
    assert rebuilt.line_items[0].sku == "S"
    assert rebuilt.line_items[0].quantity is None
