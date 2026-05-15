# Runbook

## Table of contents

- [First-time setup](#first-time-setup)
- [Run a case](#run-a-case)
- [Inspecting and editing config](#inspecting-and-editing-config)
- [Common failures](#common-failures)
- [Rotating credentials](#rotating-credentials)
- [Adding a new case](#adding-a-new-case)
- [Logs and observability](#logs-and-observability)

## First-time setup

**Recommended (macOS / Linux): one-shot installer.**

```bash
./scripts/install.sh
```

This installs `uv` (if missing), runs `uv sync`, scaffolds `.env`,
scaffolds the global TOML config at the OS-correct path
(`~/Library/Application Support/infotech-email-agent/config.toml` on
macOS; `${XDG_CONFIG_HOME:-~/.config}/infotech-email-agent/config.toml`
on Linux), and builds the React dashboard if Bun is installed. The
script is idempotent — safe to re-run after `git pull`.

Flags: `--no-frontend` to skip the bun build; `--force-config` to
overwrite an existing global config file.

**Manual setup:**

```mermaid
flowchart LR
    A["uv sync"] --> B["cp .env.example .env"]
    B --> C["edit .env<br/>OPENAI_API_KEY=sk-..."]
    C --> D["uv run pytest<br/>(optional sanity)"]
    D --> E["uv run python main.py<br/>--email examples/case_1/Email.json"]

    classDef step fill:#0e1116,stroke:#2f81f7,color:#e6edf3;
    class A,B,C,D,E step;
```

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
uv run invoice-intake --email ./examples/case_1/Email.json
```

(Equivalent: `uv run python main.py --email …` — same backend.)

Or, the minimal "intelligent" CLI that takes files **or** folders in
any order and runs one **or many** cases in a single call:

```bash
# one case folder (Email.json + Invoice.pdf auto-paired)
uv run infotech-email-agent run examples/case_1

# every fixture under examples/ (folder of cases)
uv run infotech-email-agent run examples

# explicit files in any order — also works with -f
uv run infotech-email-agent run examples/case_1/Invoice.pdf examples/case_1/Email.json
uv run infotech-email-agent run -f examples/case_1 -f examples/case_4_eur_consulting

# fast deterministic-only sweep (no LLM bill)
uv run infotech-email-agent run examples --no-llm
```

Artifacts land in `./out/case_1/` at the repo root:

- `outbound_email.txt`
- `outbound_email.json`
- `run.log`

Override locations if needed:

```bash
uv run invoice-intake \
  --email ./examples/case_1/Email.json \
  --pdf ./examples/case_1/Invoice.pdf \
  --out-dir ./out/manual_run \
  --log-file ./out/manual_run/run.log
```

Skip Pass 3 (`critic_review`) + Pass 4 (`injection_screen`) to keep the
OpenAI bill down while iterating; deterministic checks still run:

```bash
INFOTECH_PIPELINE_LLM_DISABLED=1 uv run invoice-intake --email ./examples/case_1/Email.json
```

## Inspecting and editing config

Both the host CLI and the Docker container read **one** TOML file:
[`config/config.toml`](../config/config.toml) at the repo root. Docker
mounts it read-only via `docker-compose.yml` (`./config:/app/config:ro`),
so editing on the host is reflected in the container without a rebuild.

```bash
uv run infotech-email-agent config show     # paths + every resolved key
uv run infotech-email-agent config path     # global=… project=…
```

Precedence (lowest → highest): defaults → global TOML (per-user) →
`config/config.toml` → env vars (`INFOTECH_*`) → CLI flags. Secrets
(`OPENAI_API_KEY`) only ever come from `.env` / the environment.

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

## Logs and observability

Both the batch CLI and the FastAPI dashboard route through
[`src/invoice_agent/logging_setup.py`](../src/invoice_agent/logging_setup.py),
which installs three sinks per process:

| Path | Surface | Rotation | Purpose |
|---|---|---|---|
| `logs/cli/cli.log` | CLI | daily, 14 backups | Every batch run, in time order. |
| `logs/web/web.log` | web server | daily, 14 backups | Every dashboard request, in time order. |
| `logs/runs/<case_id>.log` | both | one file per run | Flat mirror of `out/<case>/run.log`. |

Per-run `out/<case>/run.log` and `out/web/server.log` are unchanged —
they remain the per-run / per-server-boot file. The `logs/runs/`
mirror exists so operators can `grep -r logs/runs/` across history
without walking `out/`.

Override the root with `INFOTECH_LOG_DIR=/abs/path` (legacy alias:
`INVOICE_LOG_DIR`). The directory is gitignored.

### Token-spend visibility

Every CLI run prints a stakeholder-friendly token table after the
artefact list, showing per-phase + total input / cached / output /
total tokens and the prompt-cache hit ratio. The same data lives in
`outbound_email.json` under `usage.{totals, cache_hit_ratio, shots}`
and is rendered by the dashboard's "Token usage" card. Per-shot
`usage shot=…` and the run-level `usage_total …` log lines are still
emitted — see [API.md](API.md#outputs).

## Web dashboard (optional)

A React + Vite + TypeScript dashboard ships under `src/frontend/` and is
served by a Typer CLI (`src/invoice_agent_web/cli.py`, console-script
**`infotech-email-agent`**) that mounts the built bundle and the
FastAPI adapter on a single port.

### One-shot launch (recommended)

```bash
uv sync
uv run infotech-email-agent           # builds frontend if needed, opens browser
```

That prints an ASCII banner + diagnostics, builds the React bundle
(via `bun`) on first run, and serves the whole app at
<http://127.0.0.1:8000/>. Subcommands:

| Command | Purpose |
|---|---|
| `infotech-email-agent` (no args) | alias for `up` |
| `infotech-email-agent up [--port 8000] [--host 127.0.0.1] [--rebuild] [--no-browser]` | build + serve dashboard + API on one port (foreground; Ctrl-C to stop) |
| `infotech-email-agent start [--port 8000] [--no-browser] [--rebuild]` | spawn the server in the background, write PID to `out/web/server.pid`, stream logs to `out/web/server.log` |
| `infotech-email-agent status` | report whether a background server is running (exit 0 = running, 3 = not running) |
| `infotech-email-agent stop` | SIGTERM the background server (escalates to SIGKILL after 10s), remove the PID file |
| `infotech-email-agent restart [--port ...] [--rebuild]` | stop the background server (if any) and start it again |
| `infotech-email-agent dev [--port 8000]` | run backend with auto-reload; Vite dev (`bun run dev`) is a separate terminal proxying `/api/*` |
| `infotech-email-agent doctor` | print env / dependency diagnostics |
| `infotech-email-agent version` | print package version |

Drop an `Email.json` + `Invoice.pdf` into the upload zone, or click any
of the shipped example cases. The dashboard renders the pipeline
confidence gauge, per-shot timeline, risk-flag chips, the extracted
invoice (vendor, totals, line items), and the outbound packet (human
summary, full JSON, run-log tail).

Per-request artefacts land under `out/web/<timestamp>_<slug>_<id>/` (or
the path in `INVOICE_WEB_RUNS_DIR`). See `src/frontend/README.md` for env
vars and production-build notes.

### Common dashboard failures

| Symptom | Likely cause | Fix |
|---|---|---|
| `OPENAI_API_KEY is not set` (CLI exits 2) | `.env` missing or empty | `cp .env.example .env` and paste the key. |
| Browser shows JSON `frontend bundle missing` (HTTP 503) | The React bundle hasn't been built | Run `infotech-email-agent up --rebuild` (needs Bun on `$PATH`). |
| `ERROR: Bun is not installed` | Bun not on `$PATH` | Install from <https://bun.sh>, or build manually in `src/frontend/` and re-run `up`. |
| `email upload must be a .json file` (HTTP 400) | Wrong file type dropped | Drop the `Email.json` file, not the PDF. |
| `PDF attachment not found: …` (HTTP 400) | Email's `Attachments[].Name` doesn't match the uploaded PDF, and no PDF was uploaded | Either upload the PDF in the same drop or rename it to match. |
| `intake crashed: AgentRunner.run_sync() cannot be called when an event loop is already running` | Adapter route accidentally declared `async def` | Keep `/api/intake` and `/api/intake/example` as **sync** handlers; FastAPI dispatches them on a worker thread. |

## Containerized run (Docker)

The repo ships a multi-stage [Dockerfile](../Dockerfile) and a
[docker-compose.yml](../docker-compose.yml). Stages:

- `frontend` — `oven/bun` builds the React/Vite bundle.
- `base` — `python:3.12-slim` + `uv sync --frozen --no-dev` + `tesseract-ocr`.
- `runtime` — runs `infotech-email-agent up --no-browser --host 0.0.0.0 --port 8000`.
- `test` — adds dev deps + `tests/`. `CMD` runs `uv run pytest -q`.

### One-command spin-up

```bash
echo "OPENAI_API_KEY=sk-..." > .env
docker compose up
# open http://localhost:8000/
```

`out/` is mounted as a volume so per-case artefacts
(`outbound_email.{txt,json}` + `run.log`) persist on the host **and**
across container restarts. The dashboard's "History" sidebar reads them
back via `GET /api/runs`, and each row exposes a ⇣ button that streams a
`.zip` of the case folder via `GET /api/runs/{case_id}/download`.

### CLI wrapper (`infotech-email-agent docker …`)

The Typer CLI also wraps the same compose file so you do not need to
remember the docker invocation:

```bash
infotech-email-agent docker up                 # docker compose up -d
infotech-email-agent docker up --port 9000     # publish on host port 9000
infotech-email-agent docker up --rebuild       # force `--build`
infotech-email-agent docker up -f              # foreground (no -d)
infotech-email-agent docker status             # docker compose ps
infotech-email-agent docker logs --tail 200    # follow logs
infotech-email-agent docker restart            # down then up
infotech-email-agent docker down               # stop and remove
```

`docker up --port N` works because `docker-compose.yml` now interpolates
`${HOST_PORT:-8000}:8000`; the container always listens on 8000
internally.

### Plain `docker run`

```bash
docker build -t infotech-agent .
docker run --rm -p 8000:8000 \
           -e OPENAI_API_KEY=$OPENAI_API_KEY \
           -v "$PWD/out:/app/out" \
           infotech-agent
```

### Test image

The offline pytest suite needs no `OPENAI_API_KEY` (the SDK is stubbed
in `tests/conftest.py`):

```bash
docker compose run --rm tests           # via compose (profile=tests)
# or
docker build -t infotech-agent-tests --target test .
docker run --rm infotech-agent-tests
```

### Common container failures

| Symptom | Fix |
|---|---|
| `OPENAI_API_KEY is not set` at startup | Make sure `.env` exists in the build/run context, or pass `-e OPENAI_API_KEY=...`. |
| Port 8000 already bound on host | Map a different host port: `-p 9000:8000` (or set `ports: ["9000:8000"]` in compose). |
| `permission denied` writing under `/app/out` | The `out/` bind-mount on the host must be writable by your UID; `chmod g+w out/` or run with `--user "$(id -u):$(id -g)"`. |
| Build fails on `bun install` | Network blocked the Bun image; run `docker pull oven/bun:1.3-alpine` first, or set a proxy in your Docker daemon. |
