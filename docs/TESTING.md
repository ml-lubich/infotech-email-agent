# Testing

## Table of contents

- [Pytest module suite (no API credit)](#pytest-module-suite-no-api-credit)
- [Manual verification (no API credit)](#manual-verification-no-api-credit)
- [End-to-end (uses OpenAI credit)](#end-to-end-uses-openai-credit)
- [Regenerating synthetic fixtures](#regenerating-synthetic-fixtures)
- [Side effects](#side-effects)
- [What is intentionally NOT tested here](#what-is-intentionally-not-tested-here)

## Pytest module suite (no API credit)

The repo ships a module-level pytest suite under `tests/`. It exercises
the pure / deterministic surfaces — no OpenAI calls, no agent runs.

```bash
uv run pytest
```

Coverage is **gated at 100% line + branch** (configured in
`pyproject.toml` via `pytest-cov`: `--cov=invoice_agent --cov-branch
--cov-fail-under=100`). `pytest` exits non-zero if any line or branch in
`src/invoice_agent/` is uncovered. The two `@function_tool`-decorated
wrapper bodies are single-line delegations to `_impl` functions and are
marked `# pragma: no cover` (they only execute through the Agents SDK's
tool dispatcher; their work is covered via the `_impl` symbols).

Coverage map:

- `tests/test_models.py` — `resolve_model` allow-list policy (`gpt-5-mini`,
  `gpt-5-nano` only); default fallback; rejection of unknown candidates
  and unknown defaults.
- `tests/test_schema.py` — `InvoicePayload` defaults, JSON round-trip,
  partial-dict validation.
- `tests/test_pdf_extract.py` — runs `extract_pdf_content` against the
  real `examples/case_1/Invoice.pdf` (asserts text + at least one PNG-
  normalized embedded image); error paths for missing and unreadable PDFs.
- `tests/test_pdf_extract_branches.py` — monkey-patches PyMuPDF/Pillow to
  trigger every defensive `try/except` branch (text-engine failure,
  image-table failure, `extract_image` failure, missing image bytes,
  Pillow failure, sub-`_MIN_IMAGE_SIDE` filtering).
- `tests/test_tools.py` — `write_notification_files` writes both
  `outbound_email.txt` and `outbound_email.json` to a real tmp dir,
  creates missing parents, rejects invalid JSON; checks
  `send_customer_service_notification` is registered as a FunctionTool.
- `tests/test_extract_tool.py` — `_extract_invoice_from_pdf_impl` against
  a mocked `OpenAI` client (happy path + `output_parsed is None` error,
  with and without `output_text`); `_send_customer_service_notification_impl`
  with and without `INVOICE_OUT_DIR` set; `_user_payload` helper.
- `tests/test_cli.py` — `_parse_args`, `_resolve_out_dir`, and the four
  `main()` error-path exit codes (missing key=2, missing email=2,
  missing PDF=1, missing attachment=1).
- `tests/test_end_to_end.py` — `agent.run_intake` happy path with a
  stubbed `Runner.run_sync` (sibling-PDF auto-resolution and explicit
  `--pdf` override), attachment-without-name skip path, missing-email and
  missing-PDF raises, and `cli.main` success / unexpected-exception
  paths.
- `tests/test_agent.py` — `build_agent` produces an agent named
  `InvoiceIntakeAgent`, model in `ALLOWED_MODELS`, exactly two tools, and
  the system prompt carries the trust-boundary + risk-flag taxonomy.

Side-effect policy: file-writing tests use real `tmp_path` writes — the
filesystem is not mocked (see "Side effects" below).

## Manual verification (no API credit)

Module-level sanity (no OpenAI calls, no credit burn):

```bash
uv run python -c "from invoice_agent.cli import _parse_args; \
  print(_parse_args(['--email', 'examples/case_1/Email.json']))"

uv run python -c "from invoice_agent.pdf_extract import extract_pdf_content; \
  from pathlib import Path; \
  r = extract_pdf_content(Path('examples/case_1/Invoice.pdf')); \
  print('pages=', len(r.page_texts), 'images=', len(r.images))"

uv run python -c "from invoice_agent.agent import build_agent; \
  a = build_agent(); print(a.name, [t.name for t in a.tools])"
```

Error-path checks (no API credit needed — fails before any model call):

```bash
# case 2: PDF referenced by email is missing on disk
uv run python main.py --email ./examples/case_2_missing_pdf/Email.json
echo "exit=$?"     # expect 1

# case 3: email has no PDF attachment entry
uv run python main.py --email ./examples/case_3_no_attachment/Email.json
echo "exit=$?"     # expect 1

# missing OPENAI_API_KEY
env -u OPENAI_API_KEY uv run python main.py --email ./examples/case_1/Email.json
echo "exit=$?"     # expect 2
```

## End-to-end (uses OpenAI credit)

```bash
uv run python main.py --email ./examples/case_1/Email.json
uv run python main.py --email ./examples/case_4_eur_consulting/Email.json
uv run python main.py --email ./examples/case_5_usd_logistics/Email.json
uv run python main.py --email ./examples/case_6_gbp_multi_tax/Email.json
uv run python main.py --email ./examples/case_7_jpy_no_decimals/Email.json
uv run python main.py --email ./examples/case_8_split_invoice_number/Email.json
```

Quick result inspector (no model calls):

```bash
uv run python scripts/verify_outputs.py
```

Definition of Done for the happy path:

1. Exit code 0.
2. `./out/<case>/outbound_email.txt` exists, has Vendor / Invoice / PO /
   Totals / Line items / Ship-to sections, and contains the invoice number
   recovered from the embedded image.
3. `./out/<case>/outbound_email.json` exists and parses; `payload` matches
   the `InvoicePayload` schema; `email_context.po_number` is populated
   when the email body carries a PO.
4. `./out/<case>/run.log` shows each tool called exactly once.
5. For `case_5_usd_logistics`: the extracted `invoice_number` is the
   image-stamp value `PNL-INV-77 401`, NOT the cancelled draft
   `PNL-INV-77 399` mentioned in the PDF text. A `source_warnings` entry
   should flag the conflict.
6. For `case_7_jpy_no_decimals`: `currency` is `JPY` and `total_due` is
   not silently truncated or scaled (JPY has no decimal subunit).
7. For `case_8_split_invoice_number`: the final `invoice_number` is the
   merged value `NWP-2026-RX-04498` (text prefix + image suffix); a
   `source_warnings` entry should note the merge.

## Regenerating synthetic fixtures

```bash
uv run python scripts/generate_examples.py
```

Rewrites `examples/case_4_*/` through `examples/case_8_*/` from the
`InvoiceSpec` definitions in the script. `case_1` is the real provided
sample and is never touched.

## Side effects

The notify tool writes real files; do not mock the filesystem. Tests that
assert artifact shape must read the actual written files under `./out/`.

## What is intentionally NOT tested here

- Full agent E2E (`Runner.run_sync` against OpenAI) is not in the pytest
  suite — it would burn API credit on every run. Exercise it manually
  via `examples/case_1/` etc. (see "End-to-end" above).
