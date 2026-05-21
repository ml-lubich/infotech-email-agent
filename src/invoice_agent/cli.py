"""Command-line entrypoint for the invoice intake agent."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

from invoice_agent.agent import run_intake
from invoice_agent.logging_setup import configure as configure_logging
from invoice_agent.logging_setup import mirror_run_log


def _configure_logging(log_path: Path) -> None:
    """Install centralized + per-run logging sinks.

    Delegates to :mod:`invoice_agent.logging_setup` so the CLI and the
    web adapter share one definition of "where logs go".
    """
    configure_logging(surface="cli", extra_file=log_path)


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


def _print_token_summary(out_dir: Path) -> None:
    """Pretty-print per-phase token usage for business stakeholders.

    Reads the ``usage`` envelope embedded in ``outbound_email.json`` by
    ``_finalise_outbound``. Silent no-op when the envelope is missing
    (e.g. a partial run that never reached the finalise shot) \u2014 the
    structured ``usage`` log lines remain in ``run.log``.
    """
    json_path = out_dir / "outbound_email.json"
    if not json_path.is_file():
        return
    try:
        payload = json.loads(json_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    usage = payload.get("usage") if isinstance(payload, dict) else None
    if not isinstance(usage, dict):
        return
    totals = usage.get("totals") or {}
    shots = usage.get("shots") or []
    if not isinstance(totals, dict) or not isinstance(shots, list):
        return

    print()
    print("Token usage")
    print("-----------")
    header = f"  {'phase':<22}{'model':<14}{'in':>10}{'cached':>10}{'out':>10}{'total':>10}"
    print(header)
    print("  " + "-" * (len(header) - 2))
    for s in shots:
        if not isinstance(s, dict):
            continue
        print(
            f"  {str(s.get('shot', ''))[:22]:<22}"
            f"{str(s.get('model', ''))[:14]:<14}"
            f"{int(s.get('input_tokens', 0) or 0):>10,}"
            f"{int(s.get('cached_input_tokens', 0) or 0):>10,}"
            f"{int(s.get('output_tokens', 0) or 0):>10,}"
            f"{int(s.get('total_tokens', 0) or 0):>10,}"
        )
    cache_ratio = float(usage.get("cache_hit_ratio", 0.0) or 0.0)
    print("  " + "-" * (len(header) - 2))
    print(
        f"  {'TOTAL':<22}{'':<14}"
        f"{int(totals.get('input_tokens', 0) or 0):>10,}"
        f"{int(totals.get('cached_input_tokens', 0) or 0):>10,}"
        f"{int(totals.get('output_tokens', 0) or 0):>10,}"
        f"{int(totals.get('total_tokens', 0) or 0):>10,}"
    )
    print(f"  prompt-cache hit ratio: {cache_ratio * 100:.1f}%")


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
    load_dotenv(Path(__file__).resolve().parents[2] / ".env")

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
    _print_token_summary(out_dir)
    # Mirror per-run log into logs/runs/<case_id>.log so operators have
    # a flat, greppable history without walking out/. Best-effort.
    mirror_run_log(log_file, out_dir.name)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
