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
