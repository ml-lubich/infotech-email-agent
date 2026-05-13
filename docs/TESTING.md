# Testing

## Table of contents

- [Pytest module suite (no API credit)](#pytest-module-suite-no-api-credit)
- [Manual verification (no API credit)](#manual-verification-no-api-credit)
- [End-to-end (uses OpenAI credit)](#end-to-end-uses-openai-credit)
- [Regenerating synthetic fixtures](#regenerating-synthetic-fixtures)
- [Side effects](#side-effects)
- [Frontend e2e (Playwright, hermetic — no backend, no API credit)](#frontend-e2e-playwright-hermetic--no-backend-no-api-credit)
- [What is intentionally NOT tested here](#what-is-intentionally-not-tested-here)

## Pytest module suite (no API credit)

The repo ships a module-level pytest suite under `tests/`. It exercises
the pure / deterministic surfaces — no OpenAI calls, no agent runs.

```bash
uv run pytest                       # local
docker compose run --rm tests       # in the test image
```

Coverage is **gated at 80% line + branch** (configured in
`pyproject.toml` via `pytest-cov`: `--cov=invoice_agent --cov-branch
--cov-fail-under=80`). `pytest` exits non-zero if total coverage of
`src/invoice_agent/` drops below the threshold. The two
`@function_tool`-decorated wrapper bodies are single-line delegations to
`_impl` functions and are marked `# pragma: no cover` (they only execute
through the Agents SDK's tool dispatcher; their work is covered via the
`_impl` symbols).

Current suite size: 262 collected tests across 21 test modules.

The web adapter (`src/invoice_agent_web/`) is an HTTP-and-CLI adapter
layer (per `docs/ARCHITECTURE.md`); its Typer CLI is exercised by
`tests/test_web_cli.py`, which:

- pins the documented `--help` surface for the root and every
  subcommand (`up`, `dev`, `doctor`, `version`) so docs and the binary
  cannot silently drift;
- asserts environment-var error paths (`OPENAI_API_KEY` missing → exit
  code `2` from `up`; warning printed by `dev`);
- asserts `up` invokes uvicorn with the requested host/port and that
  `--rebuild` forces a fresh frontend build;
- asserts the no-subcommand call delegates to `up` (default action);
- covers the `_bundle_built` / `_build_frontend` helpers, including
  the "Bun missing" early-exit.

Heavy side effects (uvicorn, browser, `bun run build`, sleeps) are
patched out — the suite never binds a port.

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
- `tests/test_web_run_log.py` — regression coverage for the dashboard
  "Run log" tab. Asserts `_attach_run_log_handler` actually writes
  pipeline log records into `<case_dir>/run.log` (so `IntakeResponse.log_tail`
  is non-empty), and that `_read_log_tail` returns the requested tail
  size and an empty string when no `run.log` exists.

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

## Frontend e2e (Playwright, hermetic — no backend, no API credit)

The React dashboard under `frontend/` ships a Playwright suite at
`frontend/tests/e2e/`. It runs against the **production** `vite preview`
build (`dist/`), so we test the same bundle we ship.

**Hermetic by construction.** The suite never reaches the FastAPI
backend and never burns OpenAI credit. Every `/api/**` request is
intercepted in-browser via `page.route()` from
`frontend/tests/e2e/fixtures/mocks.ts`. A catch-all `**/api/**` route
is registered FIRST (Playwright runs handlers in reverse registration
order — so specific handlers registered after it win) and returns HTTP
599 — any unmocked call fails the test loudly instead of silently
hitting a real server.

```bash
cd frontend
bun install                       # one-time
bun run test:e2e:install          # one-time: download chromium
bun run test:e2e                  # headless run
bun run test:e2e:ui               # Playwright UI mode for debugging
bun run ci                        # lint + build + e2e (used in CI)
```

Coverage (7 specs in `tests/e2e/dashboard.spec.ts`):

- Header heading + health pill (healthy: "LLM active · key OK").
- Degraded backend (no `OPENAI_API_KEY`) — pill flips to
  "deterministic only · no OPENAI_API_KEY".
- Shipped-example list rendering (one button per mocked case) and the
  empty results state until a run is dispatched.
- Running a shipped example → confidence gauge, risk-flag chips
  (incl. high-risk `bank_account_change_requested`), three-shot
  pipeline timeline (`extract`, `verify_extraction`, `injection_screen`),
  invoice card with vendor / invoice # / `$1,234.56` total.
- Upload flow — `Run intake` button stays **disabled** until an
  `Email.json` is dropped on the hidden `<input type="file">`; once
  dropped + clicked, the mocked intake response renders.
- Backend error path — 500 / `{"error": …}` is surfaced as the red
  banner and **no** result card is rendered.
- Theme toggle flips `data-theme` on `<html>` and persists the choice
  to `localStorage["iia-theme"]`.
- JPY currency formatting — `Intl.NumberFormat` renders `¥13,200`
  with no decimal subunit (regression guard for `case_7_jpy_no_decimals`).

Hardening:

- TZ pinned to `America/Los_Angeles` via `test.use({ timezoneId })` so
  any date-only formatting is deterministic across CI / laptops.
- `pageerror` listener throws — any uncaught browser error fails the
  test (catches React render crashes a snapshot suite would miss).
- Headless chromium only (one project). CI gets `retries: 2`,
  `trace: on-first-retry`, screenshots + videos on failure.
- `webServer` auto-launches `vite preview --strictPort` on `127.0.0.1`
  and tears it down at the end of the run.

Artifacts (`test-results/`, `playwright-report/`, `playwright/.cache/`)
are git-ignored.

## What is intentionally NOT tested here

- Full agent E2E (`Runner.run_sync` against OpenAI) is not in the pytest
  suite — it would burn API credit on every run. Exercise it manually
  via `examples/case_1/` etc. (see "End-to-end" above).
