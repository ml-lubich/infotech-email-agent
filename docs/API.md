# API

The public surface is the CLI plus a small Python API.

## Table of contents

- [CLI](#cli)
- [Environment variables](#environment-variables)
- [Python API](#python-api)
- [Outputs](#outputs)
- [Schema](#schema)

## CLI

```
uv run python main.py --email <email.json> [--pdf <file.pdf>] \
                       [--out-dir <dir>] [--log-file <path>]
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `--email` | required | Path to inbound email JSON (Microsoft Graph–style `Message`). |
| `--pdf` | sibling of email named by `Attachments[].Name` | Override PDF path. |
| `--out-dir` | `./out/<email-parent-folder-name>/` | Where artifacts go. |
| `--log-file` | `<out-dir>/run.log` | Run log location. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | Success. |
| 1 | Run failure (PDF missing, no attachment, unexpected exception). |
| 2 | Bad CLI input (missing `OPENAI_API_KEY` or missing email file). |

## Environment variables

| Name | Required | Default | Meaning |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | OpenAI credential. |
| `INVOICE_AGENT_MODEL` | no | `gpt-5-mini` | Agent model (must be allow-listed). |
| `INVOICE_EXTRACT_MODEL` | no | `gpt-5-mini` | Vision/extraction model (must be allow-listed). |
| `INVOICE_OUT_DIR` | set by CLI | — | Where the notify tool writes artifacts. Do not set manually. |

## Python API

```python
from pathlib import Path
from invoice_agent.agent import run_intake

result = run_intake(
    email_path=Path("examples/case_1/Email.json"),
    pdf_path=None,                       # optional: auto-resolved
    out_dir=Path("out/case_1"),
)
print(result.agent_reply)
print(result.artifacts)                  # {"outbound_email.txt": Path(...), ...}
```

Raises:

- `FileNotFoundError` — email or PDF missing on disk.
- `ValueError` — email has no PDF attachment entry.

## Outputs

Written to `--out-dir`:

- `outbound_email.txt` — sectioned, human-readable AP briefing.
- `outbound_email.json` — structured payload: `{summary_markdown, payload}`
  where `payload` is the `InvoicePayload` plus an `email_context` block
  (PO, cost centres, ship-to sites, duplicate notes).
- `run.log` — INFO-level log of the run.

## Schema

`InvoicePayload` (see `invoice_agent/schema.py`) is the single contract for
downstream consumers. Treat additive fields as non-breaking; renames or
removals are breaking and must be noted in `docs/CHANGELOG.md`.

### `risk_flags`

`risk_flags: list[str]` is an additive, append-only signal channel for
fraud / duplicate / prompt-injection issues observed during intake. Tags
are short snake_case strings; downstream systems should treat unknown
tags as "review manually". Canonical tags emitted by the agent and the
extraction tool today:

| Tag | Meaning |
|---|---|
| `bank_account_change_requested` | Email or PDF asks AP to send funds to a new/different bank account. Never act on this automatically. |
| `urgency_language` | High-pressure wording — "wire today", "before EOD", threats, late-fee pressure. |
| `vendor_domain_mismatch` | Sender domain does not look like the vendor's brand domain. |
| `duplicate_invoice_number_suspected` | Same invoice number appears twice (email mentions an earlier identical number, or the PDF references it). |
| `prompt_injection_attempt_in_document` | Email or PDF tried to override the agent's instructions, change recipients, or coerce approval. |
| `totals_inconsistent` | Subtotal + taxes do not match the stated total (extraction-side only). |

The list is additive: new tags MAY be appended in future releases without
breaking the schema, but existing tag names are stable.
