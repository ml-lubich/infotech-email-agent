# Invoice Intake Agent

Small Python project built on the OpenAI Agents SDK that ingests an inbound
email plus its PDF attachment, extracts invoice/purchase fields (including
data that only appears inside images embedded in the PDF), and produces a
Customer Service notification.

## Table of contents

- [What it does](#what-it-does)
- [Model policy](#model-policy)
- [Setup](#setup)
- [Run](#run)
- [Outputs](#outputs)
- [Examples](#examples)
- [Project layout](#project-layout)
- [Docs](#docs)
- [Tests](#tests)
- [Error handling](#error-handling)

## What it does

1. Loads a local email JSON file (Microsoft Graph–style `Message` envelope).
2. Resolves the PDF attachment alongside the email.
3. Runs an agent (`gpt-5-mini`) with two tools:
   - **`extract_invoice_from_pdf`** — parses PDF text and embedded images
     with PyMuPDF, then makes a single vision-capable structured-output
     LLM call combining text + images, returning a strict `InvoicePayload`
     JSON. This recovers fields that only exist inside rasterized regions
     (e.g. an invoice number printed on a logo banner).
   - **`send_customer_service_notification`** — writes a human-readable
     `outbound_email.txt` and a structured `outbound_email.json`.

## Model policy

Only `gpt-5-mini` and `gpt-5-nano` are permitted. Enforced in
[src/invoice_agent/models.py](src/invoice_agent/models.py) — any other
model id aborts startup.

- Agent model: `gpt-5-mini` (override with `INVOICE_AGENT_MODEL`)
- Extraction (vision) model: `gpt-5-mini` (override with `INVOICE_EXTRACT_MODEL`)

Both env overrides must resolve to one of the two allow-listed ids or the
process aborts. The agent runs each tool exactly once to conserve tokens.

## Setup

```bash
uv sync
cp .env.example .env
# edit .env and set OPENAI_API_KEY=sk-...
```

`.env` is git-ignored; never commit credentials.

## Run

```bash
uv run python main.py --email ./examples/case_1/Email.json
```

Equivalent (after `uv sync`):

```bash
uv run invoice-intake --email ./examples/case_1/Email.json
```

Useful flags:

- `--pdf <path>` — override the PDF location (default: sibling of the
  email named by the `Attachments[].Name` field).
- `--out-dir <dir>` — where to write the outbound artifacts (default:
  `./out/<email-parent-folder-name>/`).
- `--log-file <path>` — where to write the run log (default:
  `<out-dir>/run.log`).

## Outputs

Written to `--out-dir` (default `./out/<case-folder>/` under the repo
root):

- `outbound_email.txt` — sectioned, human-readable Customer Service
  briefing.
- `outbound_email.json` — structured `InvoicePayload` payload for
  downstream automation.
- `run.log` — INFO-level run log.

## Examples

Each subfolder of [examples/](examples/) is a self-contained case.
Outputs are routed to `./out/<case>/` at the repo root, never inside the
case folder.

| Case | Purpose |
|---|---|
| `case_1` | Real sample (email + PDF with image-embedded invoice number). |
| `case_2_missing_pdf` | Negative fixture — referenced PDF is absent. Exits 1. |
| `case_3_no_attachment` | Negative fixture — empty `Attachments[]`. Exits 1. |
| `case_4_eur_consulting` | Synthetic EUR consulting invoice (Swiss VAT, image-only invoice number). |
| `case_5_usd_logistics` | Synthetic USD freight invoice with a duplicate-warning trap (real number in image, cancelled-draft number in text). |
| `case_6_gbp_multi_tax` | Synthetic GBP signage invoice (UK VAT 20%, two ship-to sites, image-only invoice number). |
| `case_7_jpy_no_decimals` | Synthetic JPY parts invoice (no decimals, appointment-based delivery, image-only invoice number). |
| `case_8_split_invoice_number` | Synthetic CAD pharma invoice — PDF text holds only the prefix, the image stamp carries the full invoice number. |

Synthetic cases can be regenerated with:

```bash
uv run python scripts/generate_examples.py
```

Quick inspector for `out/<case>/outbound_email.json` payloads:

```bash
uv run python scripts/verify_outputs.py
```

See [examples/README.txt](examples/README.txt) for details.

## Project layout

```
main.py                          # thin entrypoint -> invoice_agent.cli:main
src/invoice_agent/
  agent.py                       # Agent + Runner wiring, run_intake()
  cli.py                         # argparse + .env loading + error handling
  tools.py                       # @function_tool: extract + notify
  pdf_extract.py                 # deterministic text + image extraction
  schema.py                      # Pydantic InvoicePayload
  models.py                      # model allow-list
examples/
  case_1/                        # real provided sample
  case_2_missing_pdf/            # negative fixture
  case_3_no_attachment/          # negative fixture
  case_4_eur_consulting/         # synthetic EUR (Swiss VAT)
  case_5_usd_logistics/          # synthetic USD (duplicate-warning trap)
  case_6_gbp_multi_tax/          # synthetic GBP (UK VAT, multi-site)
  case_7_jpy_no_decimals/        # synthetic JPY (no decimals)
  case_8_split_invoice_number/   # synthetic CAD (prefix-in-text + image)
scripts/
  generate_examples.py           # regenerates the synthetic PDFs/emails
  verify_outputs.py              # prints headline fields from ./out/<case>/
  check_pdf_structure.py         # asserts invoice numbers live in images
docs/                            # ARCHITECTURE, API, TESTING, RUNBOOK, CHANGELOG
tests/                           # offline pytest suite (no API calls)
out/                             # generated; per-case artifacts (git-ignored)
```

## Docs

Canonical docs (see [docs/](docs/)):

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) — module map and invariants.
- [docs/API.md](docs/API.md) — CLI flags, env vars, Python API, exit codes.
- [docs/TESTING.md](docs/TESTING.md) — verification commands and Definition of Done.
- [docs/RUNBOOK.md](docs/RUNBOOK.md) — setup, run, troubleshooting.
- [docs/CHANGELOG.md](docs/CHANGELOG.md) — change history.

## Tests

Offline pytest suite (no OpenAI calls):

```bash
uv run pytest
```

See [docs/TESTING.md](docs/TESTING.md) for coverage and Definition of Done.

## Error handling

The CLI returns non-zero on:

- missing `OPENAI_API_KEY` (exit 2)
- missing email file (exit 2)
- missing PDF / no attachment / unhandled exception (exit 1, traceback in `run.log`)

Schema validation issues are surfaced via `InvoicePayload.source_warnings`
rather than aborting the run.
