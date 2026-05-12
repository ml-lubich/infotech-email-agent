"""Structural check: confirm invoice numbers live in PDF images, not raw text.

Run after regenerating fixtures to make sure the image-extraction path is
still the only way to recover the invoice number for the synthetic cases.

Usage:
    uv run python scripts/check_pdf_structure.py
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from invoice_agent.pdf_extract import extract_pdf_content


@dataclass(frozen=True)
class StructureCheck:
    pdf_path: str
    invoice_number: str
    # True if the invoice number (or a recognizable prefix) is allowed to
    # appear in raw PDF text — e.g. case_8 prints the prefix in text and
    # the full number only in an image.
    allow_in_text: bool


CHECKS: list[StructureCheck] = [
    StructureCheck("examples/case_4_eur_consulting/Invoice.pdf",   "HCC-2026-0431",     False),
    StructureCheck("examples/case_5_usd_logistics/Invoice.pdf",    "PNL-INV-77 401",    False),
    StructureCheck("examples/case_6_gbp_multi_tax/Invoice.pdf",    "APS-2026-04-118",   False),
    StructureCheck("examples/case_7_jpy_no_decimals/Invoice.pdf",  "SPC-2026-Q2-0098",  False),
    StructureCheck("examples/case_8_split_invoice_number/Invoice.pdf", "NWP-2026-RX-04498", True),
]


def main() -> int:
    failures = 0
    for check in CHECKS:
        result = extract_pdf_content(Path(check.pdf_path))
        present = check.invoice_number in result.text
        status = "ok" if (not present or check.allow_in_text) else "FAIL"
        print(
            f"{status}  {check.pdf_path}  pages={len(result.page_texts)}  "
            f"images={len(result.images)}  full_number_in_text={present}"
        )
        if status == "FAIL":
            failures += 1
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
