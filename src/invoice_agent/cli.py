"""Command-line entrypoint for the invoice intake agent."""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from invoice_agent.agent import run_intake


def _configure_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[
            logging.StreamHandler(sys.stderr),
            logging.FileHandler(log_path, encoding="utf-8"),
        ],
        force=True,
    )
    # Quiet third-party noise; keep our own decision trail loud.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="invoice-intake",
        description="Run the invoice-intake agent against a saved email + PDF.",
    )
    p.add_argument("--email", type=Path, required=True, help="Inbound email JSON file.")
    p.add_argument(
        "--pdf",
        type=Path,
        default=None,
        help="Invoice PDF (defaults to the sibling PDF named in the email).",
    )
    p.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Where to write outbound_email.{txt,json} "
             "(default: ./out/<email-parent-folder-name>/).",
    )
    p.add_argument(
        "--log-file",
        type=Path,
        default=None,
        help="Run log path (default: <out-dir>/run.log).",
    )
    return p.parse_args(argv)


def _resolve_out_dir(email: Path, override: Path | None) -> Path:
    if override is not None:
        return override
    # Group artifacts by case name (parent dir of the email), under CWD/out/.
    case_name = email.resolve().parent.name or "case"
    return Path.cwd() / "out" / case_name


def _build_openai_client(log: logging.Logger) -> object | None:
    """Construct an OpenAI client for the LLM pipeline shots.

    Activates `critic_review` and `injection_screen` (Pass 3 + Pass 4) in
    production. Set ``INVOICE_PIPELINE_LLM_DISABLED=1`` to opt out and run
    deterministic-only (cheaper, the two LLM shots will be SKIPPED).
    """
    if os.getenv("INVOICE_PIPELINE_LLM_DISABLED") == "1":
        log.info("pipeline LLM shots disabled via INVOICE_PIPELINE_LLM_DISABLED=1")
        return None
    try:
        from openai import OpenAI

        client = OpenAI()
        log.info("pipeline OpenAI client built (LLM shots ACTIVE)")
        return client
    except Exception as exc:  # noqa: BLE001 — surface, do not silently degrade
        log.warning(
            "pipeline OpenAI client unavailable (%s); LLM shots will be SKIPPED",
            exc,
        )
        return None


def _log_run_start(
    log: logging.Logger, args: argparse.Namespace, out_dir: Path, log_file: Path
) -> None:
    log.info("===== invoice-intake run START =====")
    log.info("cwd=%s", Path.cwd())
    log.info("email=%s", args.email.resolve())
    log.info(
        "pdf_arg=%s",
        args.pdf if args.pdf else "(auto-resolve from Email.json Attachments[])",
    )
    log.info("out_dir=%s", out_dir)
    log.info("log_file=%s", log_file)
    log.info(
        "models agent=%s extract=%s (env: INVOICE_AGENT_MODEL=%r INVOICE_EXTRACT_MODEL=%r)",
        os.getenv("INVOICE_AGENT_MODEL") or "(default)",
        os.getenv("INVOICE_EXTRACT_MODEL") or "(default)",
        os.getenv("INVOICE_AGENT_MODEL"),
        os.getenv("INVOICE_EXTRACT_MODEL"),
    )


def _log_artifacts(log: logging.Logger, artifacts: dict[str, Path]) -> None:
    for name, path in artifacts.items():
        marker = "OK" if path.is_file() else "MISSING"
        log.info("artifact %s status=%s path=%s", name, marker, path)
    log.info("===== invoice-intake run END =====")


def _print_result(reply: str, artifacts: dict[str, Path]) -> None:
    print(reply)
    print()
    print("Artifacts:")
    for name, path in artifacts.items():
        marker = "" if path.is_file() else "  (NOT WRITTEN)"
        print(f"  {name}: {path}{marker}")


def _run_intake_or_report(
    log: logging.Logger, args: argparse.Namespace, out_dir: Path
) -> tuple[int, object | None]:
    """Returns ``(exit_code, result_or_None)``. Exit 0 ⇒ result is set."""
    try:
        client = _build_openai_client(log)
        result = run_intake(
            email_path=args.email,
            pdf_path=args.pdf,
            out_dir=out_dir,
            openai_client=client,
        )
    except (FileNotFoundError, ValueError) as exc:
        log.error("intake failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1, None
    except Exception as exc:  # surface unexpected failures with a stack in the log
        log.exception("intake crashed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1, None
    return 0, result


def _validate_preconditions(args: argparse.Namespace) -> int | None:
    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set (see .env.example).", file=sys.stderr)
        return 2
    if not args.email.is_file():
        print(f"ERROR: email file not found: {args.email}", file=sys.stderr)
        return 2
    return None


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    early_exit = _validate_preconditions(args)
    if early_exit is not None:
        return early_exit

    out_dir = _resolve_out_dir(args.email, args.out_dir)
    log_file = args.log_file if args.log_file is not None else out_dir / "run.log"

    _configure_logging(log_file)
    log = logging.getLogger("invoice_agent.cli")
    _log_run_start(log, args, out_dir, log_file)

    code, result = _run_intake_or_report(log, args, out_dir)
    if code != 0 or result is None:
        return code

    _log_artifacts(log, result.artifacts)
    _print_result(result.agent_reply, result.artifacts)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
