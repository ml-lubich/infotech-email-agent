"""FastAPI server exposing the invoice-intake pipeline to the dashboard UI.

Endpoints
---------
GET  /api/health                       liveness probe + LLM activation status.
POST /api/intake                       multipart upload (email + optional pdf) -> intake result.
GET  /api/examples                     list shipped example cases (folders under examples/).
POST /api/intake/example               run a shipped example case by name.
GET  /api/runs                         list persisted runs in the runs dir (newest first).
GET  /api/runs/{case_id}               re-hydrate a previously stored run as IntakeResponse.
GET  /api/runs/{case_id}/download      stream a .zip of the case folder (all artefacts).

Design notes
------------
* This module is a pure adapter — it never imports low-level I/O drivers
  directly. The pipeline is invoked through ``invoice_agent.agent.run_intake``
  exactly like the CLI does.
* Each request gets its own case directory under a server-owned root
  (``INVOICE_WEB_RUNS_DIR`` env, default ``./out/web/<timestamp>_<slug>``)
  so concurrent runs cannot collide.
* Errors surface explicitly with HTTP status codes; no silent fallbacks.
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import time
import uuid
import zipfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from invoice_agent.agent import IntakeResult, run_intake
from invoice_agent.logging_setup import configure as configure_logging
from invoice_agent.logging_setup import mirror_run_log

log = logging.getLogger("invoice_agent_web")

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
EXAMPLES_DIR: Path = REPO_ROOT / "examples"
# Frontend lives under src/frontend/ — it's a sibling of the Python
# packages, not a top-level folder. Keeps everything project-related
# under src/ so live editing is one tree.
FRONTEND_DIST: Path = REPO_ROOT / "src" / "frontend" / "dist"
DEFAULT_RUNS_DIR: Path = REPO_ROOT / "out" / "web"
RUNS_DIR_ENV: str = "INVOICE_WEB_RUNS_DIR"

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _runs_dir() -> Path:
    # Precedence: legacy env var (back-compat) → merged Settings (TOML/INFOTECH_*)
    # → hardcoded default. Env-first preserves the documented behaviour while
    # adding TOML as the next layer down.
    override = os.getenv(RUNS_DIR_ENV)
    if override:
        base = Path(override)
    else:
        from invoice_agent.config import load_settings

        cfg_dir = load_settings().web_runs_dir
        base = Path(cfg_dir) if cfg_dir else DEFAULT_RUNS_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def _slug(value: str, fallback: str = "case") -> str:
    cleaned = _SLUG_RE.sub("-", value).strip("-_.") or fallback
    return cleaned[:48]


def _new_case_dir(label: str) -> Path:
    stamp = time.strftime("%Y%m%d_%H%M%S")
    case = _runs_dir() / f"{stamp}_{_slug(label)}_{uuid.uuid4().hex[:6]}"
    case.mkdir(parents=True, exist_ok=False)
    return case


# Stored runs live as direct children of ``_runs_dir()``. We do NOT
# walk up the parent chain when resolving by ``case_id`` so the URL
# cannot escape the runs root.

_CASE_ID_RE = re.compile(r"^[A-Za-z0-9_.-]+$")


def _resolve_case_dir(case_id: str) -> Path:
    """Return the case dir for ``case_id`` or raise 400/404.

    Validates the id against a strict allow-list and ensures the resolved
    path stays inside the runs directory (path-traversal defence).
    """
    if not case_id or not _CASE_ID_RE.fullmatch(case_id):
        raise HTTPException(status_code=400, detail="invalid case_id")
    base = _runs_dir().resolve()
    candidate = (base / case_id).resolve()
    if base not in candidate.parents:
        raise HTTPException(status_code=400, detail="invalid case_id")
    if not candidate.is_dir():
        raise HTTPException(status_code=404, detail=f"run not found: {case_id}")
    return candidate


def _collect_runs() -> list["StoredRun"]:
    """List all persisted runs in the runs dir, newest first."""
    base = _runs_dir()
    if not base.is_dir():
        return []
    out: list[StoredRun] = []
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            stat = child.stat()
        except OSError:
            continue
        files = [p for p in child.rglob("*") if p.is_file()]
        size = sum(p.stat().st_size for p in files)
        # Best-effort label = the slug between the timestamp and the uuid
        # suffix (see ``_new_case_dir``); fall back to the raw name.
        parts = child.name.split("_")
        label = "_".join(parts[2:-1]) if len(parts) >= 4 else child.name
        out.append(
            StoredRun(
                case_id=child.name,
                label=label or child.name,
                created_at=stat.st_mtime,
                has_outbound=(child / "outbound_email.json").is_file(),
                file_count=len(files),
                size_bytes=size,
            )
        )
    out.sort(key=lambda r: r.created_at, reverse=True)
    return out


# --------------------------------------------------------------------------- #
# response models
# --------------------------------------------------------------------------- #


class HealthResponse(BaseModel):
    status: str = Field("ok")
    llm_enabled: bool
    has_openai_key: bool
    runs_dir: str


class ExampleCase(BaseModel):
    name: str
    has_pdf: bool
    subject: str | None = None


class ExamplesResponse(BaseModel):
    cases: list[ExampleCase]


class IntakeResponse(BaseModel):
    case_id: str
    agent_reply: str
    outbound_text: str
    outbound_json: dict[str, Any]
    artifacts: dict[str, str]
    log_tail: str
    # Names of the original input files inside the case dir, so the UI
    # can fetch them via /api/runs/{case_id}/file/{filename} and render
    # the source email + PDF alongside the extraction output. Either may
    # be ``None`` (no PDF attached, or hand-built minimal case).
    email_filename: str | None = None
    pdf_filename: str | None = None


class StoredRun(BaseModel):
    case_id: str
    label: str
    created_at: float = Field(..., description="POSIX mtime of the case dir.")
    has_outbound: bool
    file_count: int
    size_bytes: int


class RunsResponse(BaseModel):
    runs: list[StoredRun]


# --------------------------------------------------------------------------- #
# OpenAI client wiring (mirror cli.py policy)
# --------------------------------------------------------------------------- #


def _build_openai_client() -> object | None:
    if os.getenv("INVOICE_PIPELINE_LLM_DISABLED") == "1":
        log.info("LLM shots disabled via INVOICE_PIPELINE_LLM_DISABLED=1")
        return None
    try:
        from openai import OpenAI

        client = OpenAI()
        log.info("OpenAI client built (LLM shots ACTIVE)")
        return client
    except Exception as exc:  # noqa: BLE001 — surface, not silently degrade
        log.warning("OpenAI client unavailable (%s); LLM shots will be SKIPPED", exc)
        return None


# --------------------------------------------------------------------------- #
# pipeline invocation helpers
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class _RunInputs:
    email_path: Path
    pdf_path: Path | None
    case_dir: Path


def _safe_subject(email_path: Path) -> str | None:
    try:
        data = json.loads(email_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    msg = data.get("Message") if isinstance(data, dict) else None
    if not isinstance(msg, dict):
        return None
    subj = msg.get("Subject")
    return subj if isinstance(subj, str) else None


def _read_log_tail(case_dir: Path, lines: int = 200) -> str:
    log_file = case_dir / "run.log"
    if not log_file.is_file():
        return ""
    try:
        text = log_file.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"(could not read run.log: {exc})"
    return "\n".join(text.splitlines()[-lines:])


def _attach_run_log_handler(case_dir: Path) -> logging.FileHandler:
    """Attach a per-run FileHandler so pipeline logs are captured to run.log.

    Mirrors cli._configure_logging's file sink, but scoped to a single
    request: handler is attached to the root logger, then detached by the
    caller in a finally block.
    """
    log_path = case_dir / "run.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.FileHandler(log_path, encoding="utf-8")
    handler.setLevel(logging.INFO)
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    )
    root = logging.getLogger()
    # Ensure INFO records actually reach the handler. ``create_app`` already
    # sets this on server startup, but tests / library use may not, and we
    # never want the run log to silently drop pipeline records.
    if root.level == logging.NOTSET or root.level > logging.INFO:
        root.setLevel(logging.INFO)
    root.addHandler(handler)
    return handler


def _read_outbound(case_dir: Path) -> tuple[str, dict[str, Any]]:
    txt_path = case_dir / "outbound_email.txt"
    json_path = case_dir / "outbound_email.json"
    text = txt_path.read_text(encoding="utf-8") if txt_path.is_file() else ""
    payload: dict[str, Any] = {}
    if json_path.is_file():
        try:
            loaded = json.loads(json_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise HTTPException(
                status_code=500,
                detail=f"outbound_email.json is not valid JSON: {exc}",
            ) from exc
        if isinstance(loaded, dict):
            payload = loaded
    return text, payload


# Files that the case dir produces but that are NOT the original
# inputs — used by ``_source_filenames`` to find the inbound email +
# invoice PDF without misclassifying agent-written artefacts.
_OUTPUT_NAMES: frozenset[str] = frozenset(
    {
        "outbound_email.json",
        "outbound_email.txt",
        "run.log",
        "agent_reply.txt",
        "extracted_invoice.json",
    }
)


def _source_filenames(case_dir: Path) -> tuple[str | None, str | None]:
    """Return ``(email_filename, pdf_filename)`` for the case dir.

    The intake handlers always copy the inbound email to ``Email.json``
    and store any attached PDF alongside it under its original name. We
    surface those names so the dashboard can deep-link to the source
    artefacts via ``/api/runs/{case_id}/file/{filename}``.
    """
    email_name: str | None = None
    pdf_name: str | None = None
    for entry in sorted(case_dir.iterdir()):
        if not entry.is_file():
            continue
        if entry.name in _OUTPUT_NAMES:
            continue
        suffix = entry.suffix.lower()
        if suffix == ".json" and email_name is None:
            email_name = entry.name
        elif suffix == ".pdf" and pdf_name is None:
            pdf_name = entry.name
    return email_name, pdf_name


def _execute_pipeline(inputs: _RunInputs) -> IntakeResponse:
    if not os.getenv("OPENAI_API_KEY"):
        raise HTTPException(
            status_code=503,
            detail="OPENAI_API_KEY is not set on the server (see .env.example).",
        )

    client = _build_openai_client()
    log_handler = _attach_run_log_handler(inputs.case_dir)
    try:
        try:
            result: IntakeResult = run_intake(
                email_path=inputs.email_path,
                pdf_path=inputs.pdf_path,
                out_dir=inputs.case_dir,
                openai_client=client,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except Exception as exc:  # noqa: BLE001 — surface as 500 with message
            log.exception("intake crashed for case_dir=%s", inputs.case_dir)
            raise HTTPException(status_code=500, detail=f"intake crashed: {exc}") from exc
    finally:
        logging.getLogger().removeHandler(log_handler)
        log_handler.close()
        # Mirror this run's log into logs/runs/<case_id>.log so ops
        # can grep historical runs from one flat directory.
        mirror_run_log(inputs.case_dir / "run.log", inputs.case_dir.name)

    outbound_text, outbound_json = _read_outbound(inputs.case_dir)
    email_name, pdf_name = _source_filenames(inputs.case_dir)
    return IntakeResponse(
        case_id=inputs.case_dir.name,
        agent_reply=result.agent_reply,
        outbound_text=outbound_text,
        outbound_json=outbound_json,
        artifacts={name: str(path) for name, path in result.artifacts.items()},
        log_tail=_read_log_tail(inputs.case_dir),
        email_filename=email_name,
        pdf_filename=pdf_name,
    )


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #


def create_app() -> FastAPI:
    load_dotenv()
    # Centralized logging: stderr + logs/web/web.log (rotated daily, 14d).
    # Existing per-request out/<case>/run.log handlers attach via
    # _attach_run_log_handler so the dashboard's log_tail still works.
    configure_logging(surface="web")

    app = FastAPI(
        title="Invoice Intake Dashboard API",
        version="0.1.0",
        description="HTTP adapter over invoice_agent.run_intake for the React dashboard.",
    )

    # Vite dev server defaults — locked to localhost.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[
            "http://localhost:5173",
            "http://127.0.0.1:5173",
            "http://localhost:4173",
        ],
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

    @app.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(
            llm_enabled=os.getenv("INVOICE_PIPELINE_LLM_DISABLED") != "1",
            has_openai_key=bool(os.getenv("OPENAI_API_KEY")),
            runs_dir=str(_runs_dir()),
        )

    @app.get("/api/examples", response_model=ExamplesResponse)
    def list_examples() -> ExamplesResponse:
        if not EXAMPLES_DIR.is_dir():
            return ExamplesResponse(cases=[])
        cases: list[ExampleCase] = []
        for child in sorted(EXAMPLES_DIR.iterdir()):
            if not child.is_dir() or not child.name.startswith("case_"):
                continue
            email = child / "Email.json"
            if not email.is_file():
                continue
            has_pdf = any(p.suffix.lower() == ".pdf" for p in child.iterdir())
            cases.append(
                ExampleCase(
                    name=child.name,
                    has_pdf=has_pdf,
                    subject=_safe_subject(email),
                )
            )
        return ExamplesResponse(cases=cases)

    @app.post("/api/intake", response_model=IntakeResponse)
    def intake(
        email: UploadFile = File(..., description="Email.json from the inbox."),
        pdf: UploadFile | None = File(
            None, description="Invoice PDF (optional if the email lists Attachments[])."
        ),
        label: str = Form("upload"),
    ) -> IntakeResponse:
        # Sync handler: the underlying ``run_intake`` calls ``Runner.run_sync``
        # which refuses to run with an active event loop. FastAPI dispatches
        # sync handlers on a worker thread, which is exactly what we need.
        if email.filename is None or not email.filename.lower().endswith(".json"):
            raise HTTPException(
                status_code=400, detail="email upload must be a .json file"
            )

        case_dir = _new_case_dir(label or email.filename or "upload")
        email_path = case_dir / "Email.json"
        email_bytes = email.file.read()
        try:
            json.loads(email_bytes.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            shutil.rmtree(case_dir, ignore_errors=True)
            raise HTTPException(
                status_code=400, detail=f"email.json is not valid JSON: {exc}"
            ) from exc
        email_path.write_bytes(email_bytes)

        pdf_path: Path | None = None
        if pdf is not None and pdf.filename:
            if not pdf.filename.lower().endswith(".pdf"):
                raise HTTPException(status_code=400, detail="pdf upload must be a .pdf")
            # Save as the name referenced by the email so auto-resolve works.
            target_name = Path(pdf.filename).name
            pdf_path = case_dir / target_name
            pdf_path.write_bytes(pdf.file.read())

        return _execute_pipeline(
            _RunInputs(email_path=email_path, pdf_path=pdf_path, case_dir=case_dir)
        )

    @app.post("/api/intake/example", response_model=IntakeResponse)
    def intake_example(name: str = Form(...)) -> IntakeResponse:
        if "/" in name or ".." in name:
            raise HTTPException(status_code=400, detail="invalid example name")
        src = EXAMPLES_DIR / name
        email = src / "Email.json"
        if not email.is_file():
            raise HTTPException(
                status_code=404, detail=f"example case not found: {name}"
            )

        case_dir = _new_case_dir(name)
        # Copy email + any PDFs sitting alongside it so auto-resolution works.
        for entry in src.iterdir():
            if entry.is_file() and entry.suffix.lower() in {".json", ".pdf"}:
                shutil.copy2(entry, case_dir / entry.name)

        return _execute_pipeline(
            _RunInputs(
                email_path=case_dir / "Email.json",
                pdf_path=None,
                case_dir=case_dir,
            )
        )

    # ------------------------------------------------------------------ #
    # persisted runs — list, re-hydrate, and download as a single zip
    # ------------------------------------------------------------------ #

    @app.get("/api/runs", response_model=RunsResponse)
    def list_runs() -> RunsResponse:
        return RunsResponse(runs=_collect_runs())

    @app.get("/api/runs/{case_id}", response_model=IntakeResponse)
    def get_run(case_id: str) -> IntakeResponse:
        case_dir = _resolve_case_dir(case_id)
        outbound_text, outbound_json = _read_outbound(case_dir)
        # Re-hydrate the artefact map from the case dir contents so the UI
        # has the same shape it gets from a fresh run.
        artifacts = {
            p.name: str(p) for p in case_dir.iterdir() if p.is_file()
        }
        email_name, pdf_name = _source_filenames(case_dir)
        return IntakeResponse(
            case_id=case_dir.name,
            agent_reply="(loaded from history)",
            outbound_text=outbound_text,
            outbound_json=outbound_json,
            artifacts=artifacts,
            log_tail=_read_log_tail(case_dir),
            email_filename=email_name,
            pdf_filename=pdf_name,
        )

    @app.get("/api/runs/{case_id}/file/{filename}")
    def get_run_file(case_id: str, filename: str) -> FileResponse:
        """Stream one file from a case dir for inline rendering.

        Used by the dashboard's "source" panel to display the original
        ``Email.json`` and the invoice PDF that the agent worked from.

        Defence:
        * ``case_id`` is validated by ``_resolve_case_dir``.
        * ``filename`` must be a simple basename (no slashes, no ``..``);
          the resolved path must stay inside the case dir.
        * Only ``.json`` and ``.pdf`` are served. Run logs and outbound
          artefacts already have their own fields on ``IntakeResponse``;
          opening this surface to ``run.log`` etc. would let a noisy
          handler exfiltrate secrets that landed in a stack trace.
        """
        case_dir = _resolve_case_dir(case_id)
        if not filename or "/" in filename or "\\" in filename or ".." in filename:
            raise HTTPException(status_code=400, detail="invalid filename")
        candidate = (case_dir / filename).resolve()
        if case_dir.resolve() not in candidate.parents:
            raise HTTPException(status_code=400, detail="invalid filename")
        if not candidate.is_file():
            raise HTTPException(status_code=404, detail=f"file not found: {filename}")
        suffix = candidate.suffix.lower()
        if suffix == ".pdf":
            media = "application/pdf"
        elif suffix == ".json":
            media = "application/json"
        else:
            raise HTTPException(
                status_code=415,
                detail="only .json and .pdf source files can be fetched",
            )
        return FileResponse(
            candidate,
            media_type=media,
            # ``inline`` so browsers render the PDF in <iframe> / <embed>
            # instead of forcing a download.
            headers={"Content-Disposition": f'inline; filename="{candidate.name}"'},
        )

    @app.get("/api/runs/{case_id}/download")
    def download_run(case_id: str) -> StreamingResponse:
        case_dir = _resolve_case_dir(case_id)
        buf = BytesIO()
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for path in sorted(case_dir.rglob("*")):
                if path.is_file():
                    zf.write(path, arcname=path.relative_to(case_dir))
        buf.seek(0)
        return StreamingResponse(
            buf,
            media_type="application/zip",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="{case_dir.name}.zip"'
                )
            },
        )

    @app.exception_handler(HTTPException)
    async def http_exc_handler(_request: object, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail, "status": exc.status_code},
        )

    # Mount the built React bundle at the site root so a single port
    # serves the whole app. If the bundle is missing (developer hasn't
    # run `bun run build`), keep `/` informative instead of 404'ing.
    if FRONTEND_DIST.is_dir() and (FRONTEND_DIST / "index.html").is_file():
        app.mount(
            "/assets",
            StaticFiles(directory=FRONTEND_DIST / "assets"),
            name="assets",
        )

        @app.get("/", include_in_schema=False)
        def _index() -> FileResponse:
            return FileResponse(FRONTEND_DIST / "index.html")

        favicon_path = FRONTEND_DIST / "favicon.svg"
        if favicon_path.is_file():
            @app.get("/favicon.ico", include_in_schema=False)
            @app.get("/favicon.svg", include_in_schema=False)
            def _favicon() -> FileResponse:
                return FileResponse(favicon_path, media_type="image/svg+xml")
    else:
        @app.get("/", include_in_schema=False)
        def _missing_bundle() -> JSONResponse:
            return JSONResponse(
                status_code=503,
                content={
                    "error": (
                        "frontend bundle missing — run "
                        "`cd frontend && bun install && bun run build` "
                        "or use the `infotech-email-agent dev` command."
                    ),
                    "expected_at": str(FRONTEND_DIST),
                },
            )

    return app


app = create_app()


# The user-facing entrypoint lives in :mod:`invoice_agent_web.cli` (the
# Typer app exposed as ``infotech-email-agent``). To run the ASGI app
# directly without the CLI, use::
#
#     uv run uvicorn invoice_agent_web.main:app --host 127.0.0.1 --port 8000


if __name__ == "__main__":
    from invoice_agent_web.cli import main

    main()
