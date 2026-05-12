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
    return 1 if missing else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:] or DEFAULT_CASES))
