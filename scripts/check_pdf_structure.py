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
    # case_9: colored header, image-only invoice number (stamp).
    StructureCheck("examples/case_9_colored_header/Invoice.pdf",   "AUR-2026-SE-0231",  False),
    # case_10: text-only; invoice number IS expected in PDF text.
    StructureCheck("examples/case_10_text_only_no_image/Invoice.pdf", "BLS-2026-04-7720", True),
    # case_11: scanned page — invoice number lives in the rasterized image,
    # PDF text is intentionally near-empty.
    StructureCheck("examples/case_11_scanned_full_page/Invoice.pdf", "CLI-2026-LAB-0512", False),
    # case_12: fraud-style invoice; number is on the stamp.
    StructureCheck("examples/case_12_fraud_bank_change/Invoice.pdf", "HCC-2026-0588",    False),
    # case_13: prompt-injection attempt in notes; number on the stamp.
    StructureCheck("examples/case_13_prompt_injection/Invoice.pdf", "SKY-2026-NET-0339", False),
    # case_14: duplicate invoice-number scenario; number on the stamp AND
    # intentionally repeated in the notes (the duplicate trap is the point).
    StructureCheck("examples/case_14_duplicate_number/Invoice.pdf", "GFS-2026-FAC-0207", True),
    # Showcase cases (15-18): polished real-template-inspired layouts.
    # Invoice number is rendered in the header bar TEXT, so it is expected
    # to be recoverable from the PDF text path.
    StructureCheck("examples/case_15_saas_subscription/Invoice.pdf", "LTC-2026-SUB-014872", True),
    StructureCheck("examples/case_16_cloud_services_bill/Invoice.pdf", "NCS-2026-04-USAGE-7728190", True),
    StructureCheck("examples/case_17_freelance_designer/Invoice.pdf", "MO-2026-0419", True),
    StructureCheck("examples/case_18_telecom_enterprise/Invoice.pdf", "VTE-2026-04-ENT-9921", True),
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
