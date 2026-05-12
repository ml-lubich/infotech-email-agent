# Runbook

## Table of contents

- [First-time setup](#first-time-setup)
- [Run a case](#run-a-case)
- [Common failures](#common-failures)
- [Rotating credentials](#rotating-credentials)
- [Adding a new case](#adding-a-new-case)

## First-time setup

```bash
uv sync
cp .env.example .env
# edit .env, set OPENAI_API_KEY=sk-...
```

`.env` is git-ignored. If a real key was ever pasted into chat, a commit,
or shell history, rotate it at https://platform.openai.com/api-keys before
using it.

## Run a case

```bash
uv run python main.py --email ./examples/case_1/Email.json
```

Artifacts land in `./out/case_1/` at the repo root:

- `outbound_email.txt`
- `outbound_email.json`
- `run.log`

Override locations if needed:

```bash
uv run python main.py \
  --email ./examples/case_1/Email.json \
  --pdf ./examples/case_1/Invoice.pdf \
  --out-dir ./out/manual_run \
  --log-file ./out/manual_run/run.log
```

## Common failures

| Symptom | Likely cause | Fix |
|---|---|---|
| Exit 2, `OPENAI_API_KEY is not set` | `.env` missing or empty | `cp .env.example .env`, paste key. |
| Exit 2, `email file not found` | Bad `--email` path | Use a path under `examples/`. |
| Exit 1, `PDF attachment not found` | Email names a PDF that isn't on disk | Provide `--pdf` or place the PDF beside the email. |
| Exit 1, `No PDF attachment found in email` | `Attachments[]` empty or no PDF entry | Add the attachment metadata or `--pdf` flag. |
| Exit 1, `model … is not allow-listed` | `INVOICE_*_MODEL` set to something other than `gpt-5-mini` / `gpt-5-nano` | Unset the env var or pick an allowed id. |

## Rotating credentials

1. Revoke the old key in the OpenAI dashboard.
2. Generate a new one.
3. Update `.env` locally; never commit it.

## Adding a new case

1. Create `examples/case_<n>_<slug>/`.
2. Drop `Email.json` (Microsoft Graph–style `Message`) and `Invoice.pdf`.
3. Run: `uv run python main.py --email ./examples/case_<n>_<slug>/Email.json`.
4. Outputs go to `./out/case_<n>_<slug>/`.
