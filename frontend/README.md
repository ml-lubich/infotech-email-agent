# Invoice Intake — Web Dashboard

React + Vite + TypeScript dashboard over the `invoice_agent` pipeline.
Adapter backend: `src/invoice_agent_web/main.py` (FastAPI).

## Architecture

```
browser (Vite dev :5173)
        │  /api/* (proxied)
        ▼
FastAPI :8000  ──►  invoice_agent.run_intake  ──►  out/web/<case>/
                                                     ├ outbound_email.json
                                                     ├ outbound_email.txt
                                                     └ run.log
```

The backend is a thin **adapter** — no business logic. It stages an
uploaded `Email.json` (+ optional PDF) into a per-request case directory
and calls the existing pipeline exactly as the CLI does.

## Run locally

### One command (recommended)

From the repo root:

```bash
uv sync
uv run infotech-email-agent           # builds bundle if needed, opens browser
```

The Typer CLI (console-script `infotech-email-agent`) builds the React
bundle on first run, mounts it at `/`, mounts the FastAPI API at
`/api/*`, and serves everything from <http://127.0.0.1:8000/>.

Useful flags:

```bash
uv run infotech-email-agent up --port 9000 --no-browser
uv run infotech-email-agent up --rebuild      # force a fresh frontend build
uv run infotech-email-agent doctor            # env / dependency diagnostics
```

### Two-terminal dev mode (live reload on both sides)

```bash
uv run infotech-email-agent dev               # backend with auto-reload :8000
cd frontend && bun install && bun run dev     # Vite dev server :5173
```

The Vite dev server proxies `/api/*` to the FastAPI backend, so CORS is
a non-issue in development.

## Production build

```bash
cd frontend
bun run build      # emits frontend/dist/
bun run preview    # serve the built bundle on :4173 for QA
```

In a real deployment, serve `frontend/dist/` from a static host (or
mount it into the FastAPI app) and point it at the backend.

## Environment

| Variable | Default | Purpose |
|---|---|---|
| `OPENAI_API_KEY` | — | required by the pipeline (same as the CLI) |
| `INVOICE_PIPELINE_LLM_DISABLED` | `0` | set to `1` to skip Pass 3/4 LLM shots |
| `INVOICE_WEB_HOST` | `127.0.0.1` | uvicorn bind host |
| `INVOICE_WEB_PORT` | `8000` | uvicorn port |
| `INVOICE_WEB_RUNS_DIR` | `./out/web` | where per-request case dirs are written |
| `VITE_API_TARGET` | `http://127.0.0.1:8000` | dev proxy target |

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/health` | liveness + LLM activation status |
| GET | `/api/examples` | list shipped example cases |
| POST | `/api/intake` | multipart: `email` + optional `pdf` + `label` |
| POST | `/api/intake/example` | run a shipped case by `name` |

See `docs/API.md` for the canonical spec.
