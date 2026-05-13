"""FastAPI server exposing the invoice-intake pipeline to the dashboard UI.

Endpoints
---------
GET  /api/health        liveness probe + LLM activation status.
POST /api/intake        multipart upload (email + optional pdf) -> intake result.
GET  /api/examples      list shipped example cases (folders under examples/).
POST /api/intake/example run a shipped example case by name.

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
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from invoice_agent.agent import IntakeResult, run_intake

log = logging.getLogger("invoice_agent_web")

# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #

REPO_ROOT: Path = Path(__file__).resolve().parents[2]
EXAMPLES_DIR: Path = REPO_ROOT / "examples"
FRONTEND_DIST: Path = REPO_ROOT / "frontend" / "dist"
DEFAULT_RUNS_DIR: Path = REPO_ROOT / "out" / "web"
RUNS_DIR_ENV: str = "INVOICE_WEB_RUNS_DIR"

_SLUG_RE = re.compile(r"[^a-zA-Z0-9_.-]+")


def _runs_dir() -> Path:
    override = os.getenv(RUNS_DIR_ENV)
    base = Path(override) if override else DEFAULT_RUNS_DIR
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

    outbound_text, outbound_json = _read_outbound(inputs.case_dir)
    return IntakeResponse(
        case_id=inputs.case_dir.name,
        agent_reply=result.agent_reply,
        outbound_text=outbound_text,
        outbound_json=outbound_json,
        artifacts={name: str(path) for name, path in result.artifacts.items()},
        log_tail=_read_log_tail(inputs.case_dir),
    )


# --------------------------------------------------------------------------- #
# FastAPI app
# --------------------------------------------------------------------------- #


def create_app() -> FastAPI:
    load_dotenv()
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)

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
