"""Generate synthetic invoice PDF + email fixtures for testing.

Each generated case writes:
  examples/<case>/Email.json
  examples/<case>/Invoice.pdf

The PDF embeds an image stamp that carries the invoice number, exercising
the agent's image-OCR path (the assignment's core requirement).

Run:
    uv run python scripts/generate_examples.py
"""

from __future__ import annotations

import io
import json
from dataclasses import dataclass
from pathlib import Path

import fitz  # pymupdf
from PIL import Image, ImageDraw, ImageFont

REPO = Path(__file__).resolve().parents[1]
EXAMPLES = REPO / "examples"


@dataclass(frozen=True)
class LineItem:
    sku: str
    description: str
    qty: int
    unit_price: float

    @property
    def total(self) -> float:
        return round(self.qty * self.unit_price, 2)


@dataclass(frozen=True)
class InvoiceSpec:
    case_dir: str
    vendor: str
    vendor_address: str
    invoice_number: str          # rendered ONLY inside the image stamp
    invoice_date: str
    due_date: str
    terms: str
    currency: str
    currency_symbol: str
    po_number: str
    bill_to: str
    ship_to: list[str]
    line_items: list[LineItem]
    tax_rate: float              # single line tax for simplicity
    tax_label: str
    notes: list[str]
    email_subject: str
    email_from_name: str
    email_from_addr: str
    email_body: str
    sent_at: str
    # Optional: render a partial invoice-number hint in PDF text (e.g. just
    # the prefix). Forces the agent to merge text + image to recover the
    # full number. Default None = invoice number is image-only.
    partial_invoice_text: str | None = None


def _make_stamp(invoice_number: str) -> bytes:
    """Render the invoice number as a PNG stamp. Only place it appears."""
    w, h = 520, 160
    img = Image.new("RGB", (w, h), "white")
    draw = ImageDraw.Draw(img)
    # Outer border
    draw.rectangle([4, 4, w - 5, h - 5], outline=(180, 30, 30), width=4)
    draw.rectangle([14, 14, w - 15, h - 15], outline=(180, 30, 30), width=2)

    try:
        font_big = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial Bold.ttf", 36)
        font_small = ImageFont.truetype("/System/Library/Fonts/Supplemental/Arial.ttf", 18)
    except OSError:
        font_big = ImageFont.load_default()
        font_small = ImageFont.load_default()

    draw.text((30, 30), "OFFICIAL INVOICE", fill=(180, 30, 30), font=font_small)
    draw.text((30, 60), invoice_number, fill=(20, 20, 20), font=font_big)
    draw.text((30, 115), "VENDOR-ISSUED · DO NOT DUPLICATE", fill=(120, 30, 30), font=font_small)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def _format_money(amount: float, symbol: str) -> str:
    return f"{symbol}{amount:,.2f}"


def _build_pdf(spec: InvoiceSpec, out_path: Path) -> None:
    subtotal = round(sum(li.total for li in spec.line_items), 2)
    tax = round(subtotal * spec.tax_rate, 2)
    total = round(subtotal + tax, 2)

    doc = fitz.open()
    page = doc.new_page(width=612, height=792)  # US Letter

    # Header (no invoice number here — image only)
    page.insert_text((50, 60), spec.vendor, fontname="helv", fontsize=20)
    page.insert_text((50, 82), spec.vendor_address, fontname="helv", fontsize=10)
    if spec.partial_invoice_text:
        page.insert_text(
            (50, 102),
            f"Invoice no. (system prefix): {spec.partial_invoice_text}",
            fontname="helv",
            fontsize=10,
        )

    # Embedded stamp image carrying the invoice number
    stamp_png = _make_stamp(spec.invoice_number)
    stamp_rect = fitz.Rect(330, 40, 562, 110)
    page.insert_image(stamp_rect, stream=stamp_png)

    # Meta block
    y = 140
    meta = [
        ("Invoice date:", spec.invoice_date),
        ("Due date:", spec.due_date),
        ("Terms:", spec.terms),
        ("Currency:", spec.currency),
        ("Customer PO:", spec.po_number),
    ]
    for label, val in meta:
        page.insert_text((50, y), label, fontname="helv", fontsize=10)
        page.insert_text((150, y), val, fontname="helv", fontsize=10)
        y += 16

    # Bill-to / Ship-to
    y += 10
    page.insert_text((50, y), "Bill to:", fontname="helv", fontsize=10)
    page.insert_text((110, y), spec.bill_to, fontname="helv", fontsize=10)
    y += 16
    page.insert_text((50, y), "Ship to:", fontname="helv", fontsize=10)
    for site in spec.ship_to:
        page.insert_text((110, y), site, fontname="helv", fontsize=10)
        y += 14
    y += 6

    # Line items header
    page.insert_text((50, y), "SKU", fontname="helv", fontsize=10)
    page.insert_text((110, y), "Description", fontname="helv", fontsize=10)
    page.insert_text((360, y), "Qty", fontname="helv", fontsize=10)
    page.insert_text((410, y), "Unit", fontname="helv", fontsize=10)
    page.insert_text((490, y), "Line total", fontname="helv", fontsize=10)
    y += 4
    page.draw_line(fitz.Point(50, y), fitz.Point(562, y))
    y += 14

    for li in spec.line_items:
        page.insert_text((50, y), li.sku, fontname="helv", fontsize=9)
        page.insert_text((110, y), li.description[:55], fontname="helv", fontsize=9)
        page.insert_text((360, y), str(li.qty), fontname="helv", fontsize=9)
        page.insert_text((410, y), _format_money(li.unit_price, spec.currency_symbol),
                          fontname="helv", fontsize=9)
        page.insert_text((490, y), _format_money(li.total, spec.currency_symbol),
                          fontname="helv", fontsize=9)
        y += 14

    y += 6
    page.draw_line(fitz.Point(360, y), fitz.Point(562, y))
    y += 14
    page.insert_text((410, y), "Subtotal:", fontname="helv", fontsize=10)
    page.insert_text((490, y), _format_money(subtotal, spec.currency_symbol),
                      fontname="helv", fontsize=10)
    y += 14
    page.insert_text((410, y), f"{spec.tax_label} ({spec.tax_rate * 100:.1f}%):",
                      fontname="helv", fontsize=10)
    page.insert_text((490, y), _format_money(tax, spec.currency_symbol),
                      fontname="helv", fontsize=10)
    y += 14
    page.insert_text((410, y), "TOTAL DUE:", fontname="helv", fontsize=11)
    page.insert_text((490, y), _format_money(total, spec.currency_symbol),
                      fontname="helv", fontsize=11)

    # Notes
    y += 28
    page.insert_text((50, y), "Notes:", fontname="helv", fontsize=10)
    y += 14
    for note in spec.notes:
        page.insert_text((60, y), f"- {note}", fontname="helv", fontsize=9)
        y += 13

    out_path.parent.mkdir(parents=True, exist_ok=True)
    doc.save(out_path)
    doc.close()


def _build_email(spec: InvoiceSpec, pdf_name: str, out_path: Path) -> None:
    msg = {
        "Message": {
            "Subject": spec.email_subject,
            "Body": {"ContentType": "Text", "Content": spec.email_body},
            "From": {
                "EmailAddress": {
                    "Name": spec.email_from_name,
                    "Address": spec.email_from_addr,
                }
            },
            "ToRecipients": [
                {"EmailAddress": {"Name": "Accounts Payable",
                                    "Address": "ap@yourcompany.example"}}
            ],
            "Attachments": [
                {
                    "@odata.type": "#microsoft.graph.fileAttachment",
                    "Name": pdf_name,
                    "ContentType": "application/pdf",
                    "ContentBytes": None,
                }
            ],
            "SentDateTime": spec.sent_at,
        }
    }
    out_path.write_text(json.dumps(msg, indent=2, ensure_ascii=False),
                         encoding="utf-8")


SPECS: list[InvoiceSpec] = [
    InvoiceSpec(
        case_dir="case_4_eur_consulting",
        vendor="Helvetia Cloud Consulting GmbH",
        vendor_address="Bahnhofstrasse 42, 8001 Zürich, Switzerland · VAT CHE-123.456.789",
        invoice_number="HCC-2026-0431",
        invoice_date="2026-03-04",
        due_date="2026-04-03",
        terms="Net 30",
        currency="EUR",
        currency_symbol="€",
        po_number="ACME-PO-55821",
        bill_to="Acme Robotics SA, 14 rue de Lausanne, 1201 Genève, CH",
        ship_to=["Acme Robotics SA — HQ, Genève (services delivered remotely)"],
        line_items=[
            LineItem("CONS-ARCH", "Cloud architecture review — Q1 2026", 40, 185.00),
            LineItem("CONS-IMPL", "Implementation pairing sessions", 24, 165.00),
            LineItem("CONS-DOC", "Architecture decision records package", 1, 1800.00),
        ],
        tax_rate=0.077,
        tax_label="Swiss VAT",
        notes=[
            "Engagement reference: Project KESTREL — Phase 1.",
            "Hours logged in client Jira; export attached on request.",
            "Reminder: this supersedes draft INV-2026-0419 (cancelled).",
        ],
        email_subject="Helvetia Cloud Consulting — invoice for Q1 2026 engagement",
        email_from_name="Lena Brunner",
        email_from_addr="lena.brunner@helvetiacloud.example",
        email_body=(
            "Hi AP team,\n\n"
            "Attached is the Q1 2026 invoice for Project KESTREL under PO "
            "ACME-PO-55821 (Net 30). Services were delivered remotely; no "
            "physical shipment.\n\n"
            "Please note this invoice replaces draft INV-2026-0419 which "
            "was cancelled — flag if you see both in the system.\n\n"
            "Thanks,\nLena Brunner\nHelvetia Cloud Consulting GmbH"
        ),
        sent_at="2026-03-04T09:12:00+01:00",
    ),
    InvoiceSpec(
        case_dir="case_5_usd_logistics",
        vendor="Pacific Northwest Logistics LLC",
        vendor_address="2200 Harbor Ave SW, Seattle, WA 98126 · EIN 47-1928374",
        invoice_number="PNL-INV-77 401",
        invoice_date="2026-04-22",
        due_date="2026-05-22",
        terms="Net 30",
        currency="USD",
        currency_symbol="$",
        po_number="GLBT-PO-9913",
        bill_to="Globaltronics Inc., 500 Innovation Way, Austin, TX 78758",
        ship_to=[
            "Globaltronics DC-East — 1200 Logistics Blvd, Memphis, TN 38118",
            "Globaltronics DC-West — 800 Cargo Rd, Reno, NV 89506",
        ],
        line_items=[
            LineItem("LTL-WEST", "LTL freight Seattle→Reno (3 loads)", 3, 1850.00),
            LineItem("LTL-EAST", "LTL freight Seattle→Memphis (2 loads)", 2, 2410.00),
            LineItem("FUEL-SUR", "Fuel surcharge (April 2026)", 1, 612.40),
            LineItem("APPT-FEE", "Dock appointment scheduling fee", 5, 45.00),
        ],
        tax_rate=0.0,
        tax_label="Sales tax (services exempt)",
        notes=[
            "Appointment-based deliveries — receiving teams CC'd on email.",
            "Reference BOL numbers attached separately.",
            "Possible duplicate: PNL-INV-77 399 was a draft, not invoiced.",
        ],
        email_subject="PNL freight invoice for April moves — PO GLBT-PO-9913",
        email_from_name="Marcus Tate",
        email_from_addr="marcus.tate@pnl-logistics.example",
        email_body=(
            "Hi Globaltronics AP,\n\n"
            "Please find attached our April 2026 freight invoice covering "
            "five LTL loads across the Memphis and Reno DCs, under PO "
            "GLBT-PO-9913 (Net 30). Fuel surcharge and appointment fees are "
            "itemized.\n\n"
            "Heads-up: PNL-INV-77 399 was a draft and never invoiced — "
            "please flag if it appears in your queue.\n\n"
            "Thanks,\nMarcus Tate\nPacific Northwest Logistics LLC"
        ),
        sent_at="2026-04-22T08:30:00-07:00",
    ),
    InvoiceSpec(
        case_dir="case_6_gbp_multi_tax",
        vendor="Albion Print & Signage Ltd.",
        vendor_address="Unit 7, Camden Wharf, London NW1 8AB, UK · VAT GB 187 654 321",
        invoice_number="APS-2026-04-118",
        invoice_date="2026-04-15",
        due_date="2026-05-30",
        terms="Net 45",
        currency="GBP",
        currency_symbol="£",
        po_number="HRZN-PO-31204",
        bill_to="Horizon Retail Group plc, 88 Bishopsgate, London EC2N 4BQ, UK",
        ship_to=[
            "Horizon Retail — Manchester Flagship, 12 Market St, Manchester M1 1WR",
            "Horizon Retail — Bristol Cabot Circus, Unit B12, Bristol BS1 3BX",
        ],
        line_items=[
            LineItem("SIG-LG-3M", "Large illuminated storefront sign (3m)", 4, 1840.00),
            LineItem("SIG-WIN", "Window vinyl set — Spring 2026 campaign", 18, 215.00),
            LineItem("INST-STD", "On-site installation (standard)", 6, 480.00),
            LineItem("PERMIT-FEE", "Council permit liaison fee", 2, 145.00),
        ],
        tax_rate=0.20,
        tax_label="UK VAT (standard rate)",
        notes=[
            "Reverse charge does NOT apply — VAT charged in full at 20%.",
            "Installation must be coordinated with each store manager 5 business days ahead.",
            "Duplicate check: please reject any APS-2026-04-117 (pro-forma, not for payment).",
        ],
        email_subject="Albion Print invoice — Spring campaign signage rollout",
        email_from_name="Priya Shah",
        email_from_addr="priya.shah@albion-signage.example",
        email_body=(
            "Hello AP,\n\n"
            "Attached is our invoice for the Spring 2026 signage rollout under "
            "PO HRZN-PO-31204 (Net 45). Two ship-to sites: Manchester flagship "
            "and Bristol Cabot Circus. VAT is charged at standard UK rate "
            "(20%) — reverse charge does not apply.\n\n"
            "Note: pro-forma APS-2026-04-117 was sent earlier for budgeting "
            "and should not be paid. This invoice (APS-2026-04-118) is the "
            "one to process.\n\n"
            "Best,\nPriya Shah — Albion Print & Signage Ltd."
        ),
        sent_at="2026-04-15T11:05:00+01:00",
    ),
    InvoiceSpec(
        case_dir="case_7_jpy_no_decimals",
        vendor="Sakura Precision Components K.K.",
        vendor_address="2-14-3 Shibaura, Minato-ku, Tokyo 108-0023, Japan",
        invoice_number="SPC-2026-Q2-0098",
        invoice_date="2026-05-02",
        due_date="2026-06-01",
        terms="Net 30",
        currency="JPY",
        currency_symbol="¥",
        po_number="KAIRO-PO-2026-441",
        bill_to="Kairo Manufacturing Co., 5-1-1 Konan, Minato-ku, Tokyo 108-0075",
        ship_to=["Kairo Manufacturing — Yokohama Plant, 3-2-1 Minatomirai, Yokohama 220-0012"],
        line_items=[
            LineItem("PRC-BR-08", "Precision bearings 8mm (lot of 500)", 12, 38500.00),
            LineItem("PRC-SH-04", "Stainless shafts 4mm (lot of 1000)", 6, 71200.00),
            LineItem("QC-INSP", "Outgoing QC inspection report", 1, 18000.00),
        ],
        tax_rate=0.10,
        tax_label="Japan consumption tax",
        notes=[
            "Amounts in JPY — no decimal places. Round per JIS standard.",
            "Delivery window: weekdays 09:00–16:00 JST, prior appointment required.",
            "Receiving dock 2; reference PO on all packing slips.",
        ],
        email_subject="桜精密部品 — 第2四半期請求書 (Sakura Precision Q2 invoice)",
        email_from_name="Aiko Tanaka",
        email_from_addr="aiko.tanaka@sakura-precision.example",
        email_body=(
            "Dear Accounts Payable,\n\n"
            "Please find attached our Q2 2026 invoice covering bearings, "
            "shafts and the outgoing QC report under PO KAIRO-PO-2026-441 "
            "(Net 30). Amounts are in JPY (no decimals).\n\n"
            "Yokohama plant receiving requires appointment-based delivery, "
            "weekdays 09:00–16:00 JST.\n\n"
            "Kind regards,\nAiko Tanaka — Sakura Precision Components K.K."
        ),
        sent_at="2026-05-02T10:20:00+09:00",
    ),
    InvoiceSpec(
        case_dir="case_8_split_invoice_number",
        vendor="Northwind Pharmaceuticals Distribution Inc.",
        vendor_address="1450 Lakeshore Dr, Burlington, ON L7S 1B1, Canada",
        # The PDF header text will print only the prefix "NWP-2026-"; the
        # image stamp carries the FULL number. The agent must merge them.
        invoice_number="NWP-2026-RX-04498",
        invoice_date="2026-05-08",
        due_date="2026-06-07",
        terms="Net 30 (2% discount if paid within 10 days)",
        currency="CAD",
        currency_symbol="$",
        po_number="MERIDIAN-PO-CTRL-7782",
        bill_to="Meridian Health Network, 200 Front St W, Toronto, ON M5V 3K2",
        ship_to=[
            "Meridian Health — Central Pharmacy, 100 College St, Toronto, ON M5G 1L5 (TEMPERATURE-CONTROLLED)",
            "Meridian Health — Hamilton Site, 50 Charlton Ave E, Hamilton, ON L8N 1Y4",
        ],
        line_items=[
            LineItem("RX-NS-09", "Normal saline 0.9% — 1L bags (case of 12)", 80, 42.50),
            LineItem("RX-AB-217", "Antibiotic A vial 1g (controlled, schedule F)", 240, 18.75),
            LineItem("RX-VAC-CLD", "Cold-chain vaccine pack (case of 10)", 30, 380.00),
            LineItem("LOG-TEMP", "Temperature-monitored transport surcharge", 1, 920.00),
        ],
        tax_rate=0.13,
        tax_label="HST (Ontario)",
        notes=[
            "TEMPERATURE-CONTROLLED shipment — Central Pharmacy delivery only between 07:00–11:00.",
            "Schedule F controlled items: signature of licensed pharmacist required on receipt.",
            "Early-payment discount: 2% if paid within 10 days of invoice date.",
            "Cross-reference: vendor system shows partial invoice prefix NWP-2026- in plain text; full number is on the stamp.",
        ],
        email_subject="Northwind Pharma — May delivery invoice (cold chain) for PO MERIDIAN-PO-CTRL-7782",
        email_from_name="Robert Caron",
        email_from_addr="robert.caron@northwind-pharma.example",
        email_body=(
            "Hi Meridian AP,\n\n"
            "Attached please find our May 2026 delivery invoice under PO "
            "MERIDIAN-PO-CTRL-7782 (Net 30, 2% within 10 days). Two ship-to "
            "sites — Central Pharmacy (cold-chain, controlled) and Hamilton.\n\n"
            "Important: the controlled-substance line (RX-AB-217) requires a "
            "licensed pharmacist signature at Central Pharmacy on receipt, "
            "and the vaccine pack must stay in cold chain until handover.\n\n"
            "Thanks,\nRobert Caron — Northwind Pharmaceuticals Distribution"
        ),
        sent_at="2026-05-08T08:45:00-04:00",
        partial_invoice_text="NWP-2026-",
    ),
]



def main() -> None:
    for spec in SPECS:
        case_dir = EXAMPLES / spec.case_dir
        pdf_path = case_dir / "Invoice.pdf"
        email_path = case_dir / "Email.json"
        _build_pdf(spec, pdf_path)
        _build_email(spec, "Invoice.pdf", email_path)
        print(f"wrote {pdf_path.relative_to(REPO)}")
        print(f"wrote {email_path.relative_to(REPO)}")


if __name__ == "__main__":
    main()
