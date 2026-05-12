# Changelog

All notable changes to the invoice-intake agent.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
This project is pre-1.0; minor versions may include breaking changes.

## Table of contents

- [[Unreleased]](#unreleased)
- [[0.1.0] — initial cut](#010--initial-cut)

## [Unreleased]

### Added
- Prompt-injection hardening: both the agent (`src/invoice_agent/agent.py`)
  and the extraction tool (`src/invoice_agent/tools.py`) now declare a
  TRUST BOUNDARY in their system prompts. Email body, PDF text, and
  embedded images are treated strictly as untrusted data; any embedded
  directive ("ignore previous instructions", "wire to this account",
  "approve immediately", etc.) is recorded as a risk flag and never
  acted on.
- `InvoicePayload.risk_flags: list[str]` — additive surface for fraud /
  duplicate / urgency / bank-change / prompt-injection signals raised by
  the extraction step. Documented tags:
  `bank_account_change_requested`, `urgency_language`,
  `vendor_domain_mismatch`, `duplicate_invoice_number_suspected`,
  `prompt_injection_attempt_in_document`, `totals_inconsistent`.
- Branch-coverage reporting via `pytest-cov` (dev-only). `uv run pytest`
  now prints a coverage table and fails if total coverage drops below
  75% (see `[tool.pytest.ini_options]` and `[tool.coverage.*]` in
  `pyproject.toml`). Current coverage: ~91%.
- `tests/test_extract_tool.py`, `tests/test_end_to_end.py`,
  `tests/test_pdf_extract_branches.py` — extra coverage around the
  extraction tool's pure helpers and PDF branch paths. Three tests that
  drive the Agents SDK `on_invoke_tool()` internals are intentionally
  skipped with explanatory `reason=`; the underlying behaviour is
  covered end-to-end via `examples/case_1` and directly by
  `tests/test_tools.py`.
- Tables of contents on `README.md` and on each canonical doc
  (ARCHITECTURE, API, TESTING, RUNBOOK, CHANGELOG) for faster navigation.
- `.gitignore` entries for `.coverage`, `htmlcov/`, `coverage.xml`.

### Changed
- Fixed a typo in `agent.run_intake` that wrote
  `os.environ["INVOICE_OUT_DIR"]` via a mangled identifier; the env-var
  name now flows from the single `OUT_DIR_ENV` constant in
  `invoice_agent.tools`.

### Verified
- Outputs audit (manual run over every fixture): all six positive cases
  (`case_1`, `case_4`–`case_8`) populate `vendor_name`, `invoice_number`,
  `currency`, `total_due`, `customer_po_number`, and at least one line
  item. `case_5`, `case_6`, and `case_8` raise the expected
  `source_warnings` flagging image-only or split-source invoice numbers.
  Both negative fixtures (`case_2`, `case_3`) exit 1 with an explicit
  error logged to `run.log`.

## [0.1.0] — initial cut

### Added
- Agents SDK wiring with two tools: `extract_invoice_from_pdf`,
  `send_customer_service_notification`.
- Pydantic `InvoicePayload` schema with `email_context` and
  `source_warnings`.
- PyMuPDF + Pillow PDF text and embedded-image extraction.
- Vision-assisted invoice-number recovery from embedded images.
- CLI (`main.py` / `invoice-intake`) with structured exit codes
  (0 success / 1 run failure / 2 bad input).
- `.env` / `.env.example` secrets contract; `.env` git-ignored.
- Per-case output routing to `./out/<case-folder>/`; default `--log-file`
  is `<out-dir>/run.log`.
- Pytest module suite covering model allow-list, schema round-trip,
  deterministic PDF extraction (real `case_1/Invoice.pdf`),
  notification file writes, CLI parsing + error-path exit codes, and
  agent wiring. No OpenAI calls.
- `invoice_agent.tools.write_notification_files` — pure helper extracted
  from `send_customer_service_notification` so the side-effectful write
  path is directly testable without going through the Agents SDK
  wrapper.
- `INVOICE_OUT_DIR` env-var name lives in one place
  (`invoice_agent.tools.OUT_DIR_ENV`) and is imported by
  `agent.run_intake` — no duplicate string literals across modules.
- Fixtures:
  - `examples/case_1/` — real provided sample (email + PDF with
    image-embedded invoice number).
  - `examples/case_2_missing_pdf/`, `examples/case_3_no_attachment/` —
    negative fixtures for error-path verification.
  - `examples/case_4_eur_consulting/` — synthetic EUR consulting invoice
    (Swiss VAT, image-only invoice number `HCC-2026-0431`).
  - `examples/case_5_usd_logistics/` — synthetic USD freight invoice
    with a duplicate-warning trap (image stamp shows real number
    `PNL-INV-77 401`; PDF text mentions cancelled draft `PNL-INV-77 399`).
  - `examples/case_6_gbp_multi_tax/` — synthetic GBP print-and-signage
    invoice with a multi-line tax breakdown (UK VAT + reverse-charge
    note); image-only invoice number `APS-2026-04-118`.
  - `examples/case_7_jpy_no_decimals/` — synthetic JPY manufacturing
    invoice exercising zero-decimal currency handling; image-only
    invoice number `SPC-2026-Q2-0098`.
  - `examples/case_8_split_invoice_number/` — synthetic CAD pharma
    distribution invoice where the invoice number is split across PDF
    text (`NWP-2026-RX-`) and embedded image (`04498`), forcing the
    agent to merge sources into the canonical `NWP-2026-RX-04498`.
- `scripts/generate_examples.py` — regenerates the synthetic fixtures
  via PyMuPDF + Pillow (no extra runtime deps).
- `scripts/verify_outputs.py` — quick sanity printer for `out/<case>/`
  payloads (reads `customer_po_number`).
- `scripts/check_pdf_structure.py` — asserts synthetic invoice numbers
  stay image-only (exits non-zero on regression).
- `examples/README.txt` describing the case folder layout.
- Canonical docs under `docs/`: ARCHITECTURE, API, TESTING, RUNBOOK,
  CHANGELOG.
