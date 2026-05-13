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
| `INVOICE_AGENT_MODEL` | no | `gpt-5-mini` | Synthesis agent model (must be allow-listed). |
| `INVOICE_EXTRACT_MODEL` | no | `gpt-5-mini` | Vision/extraction model (must be allow-listed). |
| `INVOICE_CRITIC_MODEL` | no | `gpt-5-nano` | Verifier model used by the `critic_review` and `injection_screen` shots (must be allow-listed). |
| `INVOICE_PIPELINE_LLM_DISABLED` | no | unset | When set to `1`, the CLI skips constructing an OpenAI client; the LLM pipeline shots (`critic_review`, `injection_screen`) become `SKIPPED`. Used by the test suite to keep runs offline. |
| `INVOICE_OUT_DIR` | set by CLI | — | Where the notify tool writes artifacts. Do not set manually. |
| `INVOICE_INJECTION_SIGNALS` | set by `run_intake` | — | Per-run side channel from the input guardrail to the notify tool. Comma-separated tag list; do not set manually. |

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

- `outbound_email.txt` — sectioned, human-readable AP briefing. First
  line is the pipeline confidence banner, e.g.
  `Confidence: 0.65 — 5 shots, 2 flag(s)`. If the output guardrail
  fired, a `[GUARDRAIL]` banner is prepended.
- `outbound_email.json` — structured payload: `{summary_markdown, payload, pipeline, usage}`
  where `payload` is the `InvoicePayload` plus an `email_context` block
  (PO, cost centres, ship-to sites, duplicate notes), `pipeline` is
  `{confidence, flag_count, shots: [...]}` with one record per shot
  (`name`, `kind`, `model`, `decision`, `confidence_before`, `delta`,
  `confidence_after`, `findings`), and `usage` is
  `{totals, cache_hit_ratio, shots: [...]}` with one record per LLM
  shot (`shot`, `model`, `input_tokens`, `output_tokens`,
  `total_tokens`, `cached_input_tokens`, `reasoning_tokens`).
- `usage_extract.json` — internal side-channel file the extract tool
  writes inside the same out-dir so the orchestrator can fold its
  token usage into `payload["usage"]`. Safe to ignore downstream.
- `run.log` — INFO-level log of the run, including one structured
  `shot=<n> name=… decision=… confidence_after=…` line per shot, one
  `usage shot=… model=… input=… output=… total=… cached_in=… reasoning_out=…`
  line per LLM shot, and a single `usage_total shots=… input=… output=… total=… cache_hit_ratio=…`
  summary line per run.

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

## HTTP API (web dashboard)

The optional web dashboard (see `frontend/README.md` and
`docs/RUNBOOK.md`) is served by a thin FastAPI adapter,
`src/invoice_agent_web/main.py`, launched through the Typer CLI
`src/invoice_agent_web/cli.py` (console script
**`infotech-email-agent`**). It owns no business logic — every endpoint
stages inputs into a per-request case directory under `out/web/`
(overridable via `INVOICE_WEB_RUNS_DIR`) and calls
`invoice_agent.agent.run_intake`. When the React bundle exists at
`frontend/dist/`, FastAPI also serves `index.html` at `/` and the JS/CSS
at `/assets/*`, so the whole app runs on one port.

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | `{status, llm_enabled, has_openai_key, runs_dir}` |
| GET | `/api/examples` | `{cases: [{name, has_pdf, subject}, ...]}` listing `examples/case_*/`. |
| POST | `/api/intake` | Multipart form: `email` (`.json`, required), `pdf` (`.pdf`, optional), `label` (string, optional). Runs the pipeline and returns an `IntakeResponse`. |
| POST | `/api/intake/example` | Form: `name=<case folder name>`. Copies the example into a fresh case dir and runs the pipeline. |

`IntakeResponse` shape (matches `IntakeResponse` in
`src/invoice_agent_web/main.py`):

```json
{
  "case_id": "20260512_224316_usd-logistics_acac67",
  "agent_reply": "Notification sent.",
  "outbound_text": "...full outbound_email.txt...",
  "outbound_json": { "...InvoicePayload...": "...", "pipeline": { "confidence": 0.8, "flag_count": 1, "shots": [ ... ] } },
  "artifacts": { "outbound_email.txt": "/abs/path", "outbound_email.json": "/abs/path" },
  "log_tail": "...last ~200 lines of run.log..."
}
```

Each entry in `pipeline.shots[]` has the shape:

```json
{
  "name": "critic_review",
  "kind": "llm",
  "model": "gpt-5-nano",
  "decision": "FLAG",
  "confidence_before": 0.75,
  "delta": -0.15,
  "confidence_after": 0.60,
  "findings": ["verifier_disagreement_invoice_number", "low_confidence_due_date"],
  "evidence": [
    {
      "finding": "verifier_disagreement_invoice_number",
      "source": "verifier",
      "quote": "v1='INV-1042' suggested='INV-1042-A' — image stamp shows the longer form.",
      "location": "field: invoice_number"
    }
  ]
}
```

`evidence[]` is **additive and optional** (default `[]`). Each entry is
the AP reviewer's pointer back to the exact text that triggered the
finding. `source` is one of `email | pdf_text | extracted_payload |
verifier | summary`; `quote` is a short (≤ 240 char) substring from
that source; `location` is a human hint such as `"PDF page 1"`,
`"email.body"`, or `"field: total_due"`. Old consumers that ignore
`evidence` continue to work.

Errors are JSON envelopes: `{"error": "<detail>", "status": <code>}`.
HTTP status mapping: `400` bad upload, `404` unknown example, `422`
validation (e.g. no PDF in email), `500` pipeline crash, `503`
`OPENAI_API_KEY` not set on the server.

Backend env vars:

| Name | Default | Meaning |
|---|---|---|
| `INVOICE_WEB_HOST` | `127.0.0.1` | uvicorn bind host. |
| `INVOICE_WEB_PORT` | `8000` | uvicorn port. |
| `INVOICE_WEB_RUNS_DIR` | `./out/web` | Where per-request case dirs are written. |
