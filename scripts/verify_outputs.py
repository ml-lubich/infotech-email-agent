"""Quick verification: read outbound_email.json for each case and print key fields.

No OpenAI calls — just reads files under ./out/. Used to eyeball whether
the agent populated the headline invoice fields.

Usage:
    uv run python scripts/verify_outputs.py                # default cases
    uv run python scripts/verify_outputs.py case_1 case_5  # explicit list
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

DEFAULT_CASES: list[str] = [
    "case_1",
    "case_4_eur_consulting",
    "case_5_usd_logistics",
    "case_6_gbp_multi_tax",
    "case_7_jpy_no_decimals",
    "case_8_split_invoice_number",
    "case_9_colored_header",
    "case_10_text_only_no_image",
    "case_11_scanned_full_page",
    "case_12_fraud_bank_change",
    "case_13_prompt_injection",
    "case_14_duplicate_number",
    "case_15_saas_subscription",
    "case_16_cloud_services_bill",
    "case_17_freelance_designer",
    "case_18_telecom_enterprise",
    "case_19_minimal_portrait",
    "case_20_architectural_banded",
    "case_21_landscape_panorama",
    "case_22_freelance_compact",
    "case_23_personal_balance_due",
    "case_24_wrong_total_arithmetic",
    "case_25_credit_memo_refund",
    "case_26_partial_refund_discount",
    "case_27_tax_rate_label_mismatch",
    "case_28_terms_due_date_conflict",
]


def _payload(doc: dict) -> dict:
    """Notify tool writes the InvoicePayload directly; tolerate either shape."""
    if not isinstance(doc, dict):
        return {}
    return doc.get("payload", doc)


def main(cases: list[str]) -> int:
    missing = 0
    for case in cases:
        path = Path("out") / case / "outbound_email.json"
        if not path.is_file():
            print(f"=== {case} === MISSING ({path})")
            missing += 1
            continue
        payload = _payload(json.loads(path.read_text(encoding="utf-8")))
        print(f"=== {case} ===")
        print(f"  vendor:             {payload.get('vendor_name')!r}")
        print(f"  invoice_number:     {payload.get('invoice_number')!r}")
        print(f"  currency:           {payload.get('currency')!r}")
        print(f"  total_due:          {payload.get('total_due')!r}")
        print(f"  customer_po_number: {payload.get('customer_po_number')!r}")
        print(f"  line_items:         {len(payload.get('line_items') or [])}")
        warns = payload.get("source_warnings") or []
        if warns:
            print(f"  warnings:           {warns}")
        risks = payload.get("risk_flags") or []
        if risks:
            print(f"  risk_flags:         {risks}")
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or DEFAULT_CASES))
