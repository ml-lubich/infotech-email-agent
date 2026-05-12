# Changelog

All notable changes to the invoice-intake agent.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0; minor versions may include breaking changes.

## Table of contents

- [[Unreleased]](#unreleased)
- [[0.1.0] — initial cut](#010--initial-cut)

## [Unreleased]

### Added
- Tables of contents on `README.md` and on each canonical doc
  (ARCHITECTURE, API, TESTING, RUNBOOK, CHANGELOG) for faster navigation.
- Outputs audit (manual): all six positive cases (`case_1`, `case_4`–`case_8`)
  populate `vendor_name`, `invoice_number`, `currency`, `total_due`,
  `customer_po_number`, and at least one line item; the three hard cases
  (`case_5`, `case_6`, `case_8`) raise the expected `source_warnings`
  flagging image-only or split-source invoice numbers. Both negative
  fixtures (`case_2`, `case_3`) exit 1 with an explicit error in
  `run.log`.
- `tests/` — pytest module suite covering model allow-list, schema
  round-trip, deterministic PDF extraction (real `case_1/Invoice.pdf`),
  notification file writes, CLI parsing + error-path exit codes, and
  agent wiring. Run with `uv run pytest`. No OpenAI calls.
- `invoice_agent.tools.write_notification_files` — pure helper extracted
  from `send_customer_service_notification` so the side-effectful write
  path is directly testable without going through the Agents SDK wrapper.
- `examples/case_2_missing_pdf/` and `examples/case_3_no_attachment/`
  fixtures for error-path verification.
- `examples/case_4_eur_consulting/` — synthetic EUR consulting invoice
  (Swiss VAT, image-only invoice number `HCC-2026-0431`).
- `examples/case_5_usd_logistics/` — synthetic USD freight invoice with a
  duplicate-warning trap (image stamp shows real number `PNL-INV-77 401`;
  PDF text mentions cancelled draft `PNL-INV-77 399`).
- `examples/case_6_gbp_multi_tax/` — synthetic GBP print-and-signage
  invoice with a multi-line tax breakdown (UK VAT + reverse-charge note);
  image-only invoice number `APS-2026-04-118`.
- `examples/case_7_jpy_no_decimals/` — synthetic JPY manufacturing
  invoice exercising zero-decimal currency handling; image-only invoice
  number `SPC-2026-Q2-0098`.
- `examples/case_8_split_invoice_number/` — synthetic CAD pharma
  distribution invoice where the invoice number is split across PDF text
  (`NWP-2026-RX-`) and embedded image (`04498`), forcing the agent to
  merge sources into the canonical `NWP-2026-RX-04498`.
- `scripts/generate_examples.py` — regenerates the synthetic fixtures via
  pymupdf + Pillow (no extra runtime deps).
- `scripts/verify_outputs.py` — quick sanity printer for `out/<case>/`
  payloads.
- `scripts/check_pdf_structure.py` — asserts synthetic invoice numbers
  stay image-only (exits non-zero on regression).
- `examples/README.txt` describing the case folder layout.
- Canonical docs under `docs/`: ARCHITECTURE, API, TESTING, RUNBOOK,
  CHANGELOG.

### Changed
- Default `--out-dir` is now `./out/<email-parent-folder-name>/` (was CWD).
  Per-case outputs no longer pollute example directories.
- Default `--log-file` is now `<out-dir>/run.log` (was `./run.log`).
- `.gitignore`: ignore `/out/` and `run.log` instead of root-level
  `outbound_email.{txt,json}`.
- `INVOICE_OUT_DIR` env-var name now lives in one place
  (`invoice_agent.tools.OUT_DIR_ENV`) and is imported by
  `agent.run_intake` — no more duplicate string literals across modules.
- `scripts/verify_outputs.py` reads `customer_po_number` (was the
  non-existent `po_number`, which always printed `None`) and defaults to
  the full case list 1/4/5/6/7/8.

### Removed
- Unused `pypdf` runtime dependency (the project uses PyMuPDF only).
- PyMuPDF `outbound_email.{txt,json}` and `run.log` from the repo root.
- `examples/case_1/out/` (artifacts now route to `./out/case_1/`).

## [0.1.0] — initial cut

### Added
- Agents SDK wiring with two tools: `extract_invoice_from_pdf`,
  `send_customer_service_notification`.
- Pydantic `InvoicePayload` schema.
- pypdf + Pillow PDF text and embedded-image extraction.
- Vision-assisted invoice-number recovery from embedded images.
- CLI (`main.py` / `invoice-intake`) with structured exit codes.
- `.env` / `.env.example` secrets contract.
- `examples/case_1/` sample (real email + PDF).
