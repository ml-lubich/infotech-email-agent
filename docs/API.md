# API

The public surface is the CLI plus a small Python API.

## Table of contents

- [CLI](#cli)
  - [`infotech-email-agent run`](#infotech-email-agent-run--minimal-intelligent-cli)
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

### `infotech-email-agent run` — minimal "intelligent" CLI

Same intake pipeline, but accepts a free-form list of paths in any
order: files (`.json`, `.pdf`), a single case folder, or a folder of
case subdirectories. Each input is auto-classified; one or many cases
are dispatched through the same `invoice_agent.cli.main` entry point.

```
infotech-email-agent run [PATHS]... [-f/--file PATH]... \
                         [--out-dir <dir>] [--no-llm] \
                         [--continue-on-error|--stop-on-error]
```

Examples:

```
# one case folder (Email.json + Invoice.pdf auto-paired)
infotech-email-agent run examples/case_1

# a folder of cases — runs every subdir that contains Email.json
infotech-email-agent run examples

# explicit files in any order
infotech-email-agent run examples/case_1/Invoice.pdf examples/case_1/Email.json

# repeated -f works just like positional paths
infotech-email-agent run -f examples/case_1 -f examples/case_4_eur_consulting

# fast smoke pass with the LLM shots disabled (deterministic only)
infotech-email-agent run examples --no-llm
```

Flags:

| Flag | Default | Meaning |
|---|---|---|
| `PATHS...` | — | Files (`.json`, `.pdf`) and/or folders. Order does not matter. |
| `-f`, `--file` | — | Same as a positional path; repeatable. |
| `--out-dir` | `./out/` | Root for artifacts. Each case writes to `<out-dir>/<case-folder-name>/`. |
| `--no-llm` | off | Sets `INFOTECH_PIPELINE_LLM_DISABLED=1` — pipeline LLM shots SKIPPED. |
| `--continue-on-error` / `--stop-on-error` | continue | Behaviour when running many cases. |

Exit codes:

| Code | Meaning |
|---|---|
| 0 | All discovered cases succeeded. |
| 1 | One or more cases failed (full per-case summary printed at the end). |
| 2 | Bad CLI input (no inputs, no `Email.json` discovered, unknown extension). |

## Environment variables

The canonical prefix is `INFOTECH_*`. Legacy `INVOICE_*` names are still
honored as aliases for back-compat — when both are set, `INFOTECH_*` wins.

| Name (canonical / legacy) | Required | Default | Meaning |
|---|---|---|---|
| `OPENAI_API_KEY` | yes | — | OpenAI credential. Loaded from `.env`. Never put this in TOML. |
| `INFOTECH_AGENT_MODEL` / `INVOICE_AGENT_MODEL` | no | `gpt-5-mini` | Synthesis agent model (must be allow-listed). |
| `INFOTECH_EXTRACT_MODEL` / `INVOICE_EXTRACT_MODEL` | no | `gpt-5-mini` | Vision/extraction model (must be allow-listed). |
| `INFOTECH_CRITIC_MODEL` / `INVOICE_CRITIC_MODEL` | no | `gpt-5-nano` | Verifier model used by the `critic_review` and `injection_screen` shots (must be allow-listed). |
| `INFOTECH_PIPELINE_LLM_DISABLED` / `INVOICE_PIPELINE_LLM_DISABLED` | no | unset | When `1`, the CLI skips constructing an OpenAI client; pipeline LLM shots become `SKIPPED`. Used by the test suite to keep runs offline. |
| `INFOTECH_LOG_DIR` / `INVOICE_LOG_DIR` | no | `<repo>/logs` | Override the centralized log root (`logs/{cli,web,runs}/`). |
| `INFOTECH_WEB_HOST` / `INVOICE_WEB_HOST` | no | `127.0.0.1` | Bind host for the dashboard. |
| `INFOTECH_WEB_PORT` / `INVOICE_WEB_PORT` | no | `8000` | Port for the dashboard. |
| `INFOTECH_WEB_RUNS_DIR` / `INVOICE_WEB_RUNS_DIR` | no | `./out/web` | Per-request case dir root used by the FastAPI adapter. |
| `INVOICE_OUT_DIR` | set by CLI | — | Where the notify tool writes artifacts. Do not set manually. |
| `INVOICE_INJECTION_SIGNALS` | set by `run_intake` | — | Per-run side channel from the input guardrail to the notify tool. Do not set manually. |

## Configuration cascade (TOML)

Highest layer wins. See [`src/invoice_agent/config.py`](../src/invoice_agent/config.py).

1. **Defaults** (the `Settings` Pydantic model).
2. **Global TOML** — OS-correct user config path:
   - macOS: `~/Library/Application Support/infotech-email-agent/config.toml`
   - Linux: `${XDG_CONFIG_HOME:-~/.config}/infotech-email-agent/config.toml`
   - Windows: `%APPDATA%\infotech-email-agent\config.toml`
3. **Project TOML** — first match wins, walking up from CWD:
   1. `./config/config.toml`                  ← recommended (visible in repo + Docker mount)
   2. `./config/infotech-email-agent.toml`
   3. `./infotech-email-agent.toml`           (flat keys at the root)
   4. `./pyproject.toml` table `[tool.infotech-email-agent]`
4. **Environment variables** (table above).
5. **Command-line flags** — `infotech-email-agent --port 9000`, etc.

Inspect the merged view from the CLI:

```bash
infotech-email-agent config show     # pretty: paths + every resolved key
infotech-email-agent config path     # machine-friendly: global=… project=…
```

Example global config (auto-scaffolded by `./scripts/install.sh`):

```toml
# ~/Library/Application Support/infotech-email-agent/config.toml  (macOS)
# ~/.config/infotech-email-agent/config.toml                       (Linux)

# agent_model    = "gpt-5-mini"
# extract_model  = "gpt-5-mini"
# critic_model   = "gpt-5-nano"
# web_host       = "127.0.0.1"
# web_port       = 8000
# web_runs_dir   = "/abs/path/to/per-request/case/dirs"
# llm_disabled   = false
```

Bad values (unknown model id, non-integer port, malformed TOML) abort
startup with a clear error — there are no silent fallbacks.

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

In addition to per-run `out/<case>/run.log`, both surfaces also write
to a centralized `logs/` tree (overridable via `INFOTECH_LOG_DIR`):

- `logs/cli/cli.log` — daily-rotated (14 backups), every CLI run.
- `logs/web/web.log` — daily-rotated (14 backups), every web request.
- `logs/runs/<case_id>.log` — flat mirror of the per-run log, written
  at the end of each successful run.

After a successful CLI run, a stakeholder-friendly token table is also
printed to stdout (per-phase + total tokens, prompt-cache hit ratio).

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

The optional web dashboard (see `src/frontend/README.md` and
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
| GET | `/api/runs` | `{runs: [{case_id, label, created_at, has_outbound, file_count, size_bytes}, ...]}` listing every persisted case dir under `runs_dir`, newest first. |
| GET | `/api/runs/{case_id}` | Re-hydrate a previously-stored run as an `IntakeResponse` (no pipeline call). 400 on a malformed id, 404 if the case dir is missing. |
| GET | `/api/runs/{case_id}/file/{filename}` | Stream one source file from the case dir for inline rendering. Used by the dashboard's source panel to show the original `Email.json` and invoice PDF. Only `.json` and `.pdf` are served (run.log and outbound artefacts have their own fields on `IntakeResponse`). 400 on a malformed filename / path-traversal attempt, 404 if the file is missing, 415 if the extension is not allowed. Response carries `Content-Disposition: inline` so browsers render the PDF in `<iframe>` instead of forcing a download. |
| GET | `/api/runs/{case_id}/download` | Streams a `application/zip` of the case folder (every file recursively). Filename: `<case_id>.zip`. |

### Dashboard CLI subcommands

The Typer CLI ships these subcommands (see `docs/RUNBOOK.md` for full
flags). Background lifecycle is PID-file-based; the PID file lives at
`out/web/server.pid` and the captured server log at `out/web/server.log`.

| Subcommand | Purpose | Exit codes |
|---|---|---|
| `up` (default) | Foreground: build bundle (if needed), bind, open browser, block until Ctrl-C. | 0 on clean exit, 2 if `OPENAI_API_KEY` is missing. |
| `start` | Spawn detached server, write PID file, return immediately. | 0 on success, 1 if a live PID file already exists or the child died on launch, 2 if `OPENAI_API_KEY` is missing. |
| `stop` | SIGTERM the PID in the PID file (10 s grace, then SIGKILL). | 0 on success or no-op (no PID file), 1 if the process refused to die. |
| `restart` | Best-effort `stop` then `start`. | Same as `start`. |
| `status` | Report whether a background server is running. | 0 = running, 3 = not running. |
| `dev` | Backend with `--reload`; Vite dev server runs separately. | 0 on clean exit. |
| `doctor` | Print env / dependency diagnostics. | 0. |
| `version` | Print package version. | 0. |
| `docker up` | `docker compose up -d` against the bundled `docker-compose.yml`. `--port N` publishes the host port via the `HOST_PORT` env var consumed by compose; `--rebuild` adds `--build`; `--foreground/-f` streams logs instead of detaching. | 0 on success, 2 if `docker` is not on PATH or `docker-compose.yml` is missing, otherwise the compose exit code. |
| `docker down` | `docker compose down` (use `--volumes/-v` to also drop named volumes; the `./out` bind mount is unaffected). | 0 on success, otherwise the compose exit code. |
| `docker restart` | Best-effort `docker down` then `docker up`. | Same as `docker up`. |
| `docker status` | `docker compose ps`. | Pass-through. |
| `docker logs` | `docker compose logs --tail N -f agent`. `--no-follow` returns immediately; SIGINT (130) while following is treated as a clean exit. | 0 on clean exit. |

`IntakeResponse` shape (matches `IntakeResponse` in
`src/invoice_agent_web/main.py`):

```json
{
  "case_id": "20260512_224316_usd-logistics_acac67",
  "agent_reply": "Notification sent.",
  "outbound_text": "...full outbound_email.txt...",
  "outbound_json": { "...InvoicePayload...": "...", "pipeline": { "confidence": 0.8, "flag_count": 1, "shots": [ ... ] } },
  "artifacts": { "outbound_email.txt": "/abs/path", "outbound_email.json": "/abs/path" },
  "log_tail": "...last ~200 lines of run.log...",
  "email_filename": "Email.json",
  "pdf_filename": "Invoice.pdf"
}
```

`email_filename` and `pdf_filename` name the original inbound files
stored inside the case dir. Either may be `null` (e.g. a hand-built
minimal case with no PDF). The dashboard fetches them via
`/api/runs/{case_id}/file/{filename}` to render the source packet
alongside the extraction output.

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
