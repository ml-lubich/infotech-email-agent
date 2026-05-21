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

Coverage is **gated at 80% line + branch** (configured in
`pyproject.toml` via `pytest-cov`: `--cov=invoice_agent --cov-branch
--cov-fail-under=80`). `pytest` exits non-zero if total coverage of
`src/invoice_agent/` drops below the threshold. The two
`@function_tool`-decorated wrapper bodies are single-line delegations to
`_impl` functions and are marked `# pragma: no cover` (they only execute
through the Agents SDK's tool dispatcher; their work is covered via the
`_impl` symbols).

Current suite size: 458 collected tests across 32 test modules.

The web adapter (`src/invoice_agent_web/`) is an HTTP-and-CLI adapter
layer (per `docs/ARCHITECTURE.md`); its Typer CLI is exercised by
`tests/test_web_cli.py`, which:

- pins the documented `--help` surface for the root and every
  subcommand (`up`, `start`, `stop`, `restart`, `status`, `dev`,
  `doctor`, `version`) so docs and the binary cannot silently drift;
- asserts environment-var error paths (`OPENAI_API_KEY` missing → exit
  code `2` from `up` and `start`; warning printed by `dev`);
- pins dotenv regression behavior: `up` and `start` must succeed when
  `OPENAI_API_KEY` is absent from process env but present in a repo-root
  `.env` (`test_up_reads_openai_key_from_dotenv_file`,
  `test_start_reads_openai_key_from_dotenv_file`);
- asserts `up` invokes uvicorn with the requested host/port and that
  `--rebuild` forces a fresh frontend build;
- asserts the no-subcommand call delegates to `up` (default action);
- covers the `_bundle_built` / `_build_frontend` helpers, including
  the "Bun missing" early-exit;
- exercises the background lifecycle: `start` writes the PID file and
  refuses to launch when one already exists; `stop` is a no-op when
  no PID file is present and SIGTERMs + removes the file when one is;
  `restart` stops then starts; `status` exits `3` when not running and
  `0` when running; `_read_pidfile` clears stale entries and ignores
  garbage. The PID/log files are sandboxed via `tmp_path` and the
  spawn/terminate helpers are stubbed so no real process is forked.

Heavy side effects (uvicorn, browser, `bun run build`, sleeps) are
patched out — the suite never binds a port.

The `infotech-email-agent run` subcommand (free-form file/folder intake
dispatcher) is covered by
[`tests/test_web_cli_run.py`](../tests/test_web_cli_run.py), which:

- unit-tests `discover_cases()` for every classification path
  (single case folder, folder of case subdirs, explicit `Email.json`,
  lone `.pdf` paired by sibling, mixed order, dedup) and rejects
  unknown extensions / missing paths via `typer.BadParameter`;
- replaces `invoice_agent.cli.main` with a recording stub so the
  CLI integration tests assert dispatch arguments (per-case
  `--email`, `--pdf`, `--out-dir`) without ever calling OpenAI;
- pins the `--no-llm` env-toggle, the `--continue-on-error` /
  `--stop-on-error` switch, the empty-input exit code (`2`), and the
  `--help` text (so the documented examples cannot drift).

The persisted-runs HTTP API (`GET /api/runs`, `GET /api/runs/{case_id}`,
`GET /api/runs/{case_id}/download`) is covered by
[`tests/test_web_runs_endpoints.py`](../tests/test_web_runs_endpoints.py).
It seeds a synthetic case folder under a `tmp_path`-backed runs dir
(via the `INVOICE_WEB_RUNS_DIR` override), drives a `TestClient`, and
asserts: list ordering (newest first), re-hydration field shape, zip
download contents (validated by reading the bytes back through
`zipfile.ZipFile`), and 400/404 path-traversal defences for malformed
case ids.

The **negative-input + crash-resilience surface** of the web adapter
is covered by
[`tests/test_web_intake_negative.py`](../tests/test_web_intake_negative.py).
It exercises `/api/intake` upload validation (non-`.json` filename,
malformed JSON, non-UTF-8 bytes, non-`.pdf` filename, missing
`OPENAI_API_KEY` → 503), pipeline-crash translation
(`FileNotFoundError` → 400, `ValueError` → 422, `RuntimeError` →
structured 500 envelope with no stack trace), an explicit
server-stays-alive regression (a crashing request must not poison
the next request), `/api/intake/example` traversal-name rejection
plus a happy path with a stubbed `run_intake`,
`/api/runs/{case_id}/download` for unknown ids and invalid ids,
malformed `outbound_email.json` rendering as a JSON 500 envelope
instead of crashing the worker, and `/api/health` + `/api/examples`
response-shape smoke. The OpenAI client is never constructed:
`INVOICE_PIPELINE_LLM_DISABLED=1` is set per-test and `run_intake` is
monkeypatched.

The `infotech-email-agent docker {up,down,restart,status,logs}`
subgroup is covered by
[`tests/test_docker_cli.py`](../tests/test_docker_cli.py). `shutil.which`
and `subprocess.call` are patched so no real `docker compose` runs;
the tests assert command-line construction (`-d`, `--build`, `logs -f`),
that `docker up --port N` injects `HOST_PORT=N` into the child env, and
that the missing-`docker` path exits 2.

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
- `tests/test_pdf_extract_ocr.py` — local OCR fallback
  (`rapidocr-onnxruntime`). Synthesized pixel-only PDF must recover a
  recognizable substring; engine-unavailable branch degrades to native
  text only; the real `examples/case_11_scanned_full_page/Invoice.pdf`
  fixture (no copyable text) must surface vendor `Cascadia`, invoice
  number `CLI-2026-LAB-0512`, and currency `USD` via OCR.
- `tests/test_retry.py` — bounded-retry helper
  (`invoice_agent._retry.retry_call`). Pins: success on attempt 1 with
  no sleep; success on attempt 2 with one back-off sleep; exhaustion
  after N attempts re-raises the final exception; non-transient
  exception classes bypass the retry loop; `on_attempt` callback fires
  for every attempt.
- `tests/test_robustness.py` — wiring tests for the retry envelope.
  `verify_extraction` and `injection_screen` recover on attempt 2 from
  a transient `RuntimeError` and re-raise after attempt 3.
  `pdf_extract._ocr_page` recovers from a transient ONNX error and
  degrades to empty text when retries exhaust (never raises — OCR is
  best-effort).
- `tests/test_guardrails.py` — deterministic input + output guardrails:
  `scan_for_injection` tag taxonomy (`ignore_prior_instructions`,
  `role_redefinition`, `fake_role_marker`, `auto_approve_directive`,
  `payment_redirection`); `scan_output_for_unsafe_directives` on the
  AP-facing summary; `apply_output_guardrails` additive merge of risk
  flags + `[GUARDRAIL]` banner; env side-channel
  (`publish_injection_signals` / `read_injection_signals`).
- `tests/test_llm_params.py` — `_llm_params.llm_params` per-shot defaults
  (`extract` / `verify` / `injection`): reasoning effort, verbosity,
  `max_output_tokens` caps (2048 / 1024 / 256), constant
  `safety_identifier="invoice-intake-agent"`, and
  `prompt_cache_key="<shot>:<model>"`.
- `tests/test_safety_knobs.py` — pins that the extract / verifier /
  injection-screen shots forward every `_llm_params` kwarg to
  `client.responses.parse(...)`, and that a Structured-Outputs REFUSAL
  is converted into a `model_refused_extraction` risk flag instead of a
  hard crash.
- `tests/test_pipeline.py` — `PipelineState` ledger math
  (`START_CONFIDENCE`, PASS/FLAG/FAIL/SKIPPED deltas, per-shot caps,
  `[0.0, 1.0]` clamp), `pipeline` envelope shape in
  `outbound_email.json`, banner presence in `outbound_email.txt`,
  finalise idempotency, and the `injection_screen` LLM shot.
- `tests/test_pipeline_activation.py` — wiring tests for the LLM shot
  activation path: `cli.main` constructs an `OpenAI` client and
  injects it into `run_intake`; `INVOICE_PIPELINE_LLM_DISABLED=1`
  opt-out; soft fallback when the client constructor fails; shots fire
  as `PASS`/`FLAG` when a client is provided and as `SKIPPED` when not;
  verifier exceptions surface as `FAIL` (no silent fallback).
- `tests/test_llm_noise_filter.py` — pins the citable-evidence gate
  applied to LLM shots so the weak verifier model (`gpt-5-nano`)
  cannot anchor a clean run at `0.65` with unanchored "soft"
  findings. Coverage: LLM PASS reward parity (+0.10) and a six-shot
  clean run reaching `1.00`; critic_review drops `low_confidence_*`
  grades while keeping `verifier_disagreement_*`; injection_screen
  drops the canonical aggregate
  `prompt_injection_attempt_in_document` when the deterministic
  scanner does NOT agree, and keeps it (with a cite back to the
  agreeing regex) when it does; specific known tags
  (`ignore_prior_instructions`) are kept with their regex quote;
  hallucinated unknown tags are dropped; garbage tag strings (empty,
  unicode, 500-char) do not crash the gate; an LLM seam exception
  still records `FAIL` and the pipeline finalises.
- `tests/test_verifier.py` — `VerificationReport`, `FieldScore`,
  `Disagreement` model shapes; `verify_extraction` and
  `injection_screen` against an injected fake OpenAI client (happy
  path, refusal path, allow-list enforcement via `resolve_model`).
- `tests/test_guardrails_adversarial.py` — adversarial / regression
  tests for `guardrails`. Mixed-case + whitespace prompt injection,
  filler-word variants ("ignore the above messages"), role-redefinition
  ("you are now", "act as", "pretend to be", "new system prompt"),
  fake role markers (`### system`, `[INST]`, `<|im_start|>`), payment
  redirection ("wire to new bank", "change our payment details"),
  negation phrases ("not approved", "pending approval", "awaiting
  approval") that MUST NOT trip the output guardrail, arithmetic
  tolerance + ISO format checks, and a 200 kB padding input that must
  scan in < 500 ms (catastrophic-backtracking guard).

Side-effect policy: file-writing tests use real `tmp_path` writes — the
filesystem is not mocked (see "Side effects" below). The autouse
fixture `_no_real_sleep_in_retries` in `tests/conftest.py` collapses
`_retry.time.sleep` to a no-op for every test so retry-exercising
tests run instantly without changing production timing.

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
