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


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    load_dotenv()

    if not os.getenv("OPENAI_API_KEY"):
        print("ERROR: OPENAI_API_KEY is not set (see .env.example).", file=sys.stderr)
        return 2

    if not args.email.is_file():
        print(f"ERROR: email file not found: {args.email}", file=sys.stderr)
        return 2

    out_dir = _resolve_out_dir(args.email, args.out_dir)
    log_file = args.log_file if args.log_file is not None else out_dir / "run.log"

    _configure_logging(log_file)
    log = logging.getLogger("invoice_agent.cli")
    log.info("starting intake email=%s pdf=%s out_dir=%s",
             args.email, args.pdf, out_dir)

    try:
        result = run_intake(
            email_path=args.email,
            pdf_path=args.pdf,
            out_dir=out_dir,
        )
    except (FileNotFoundError, ValueError) as exc:
        log.error("intake failed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # surface unexpected failures with a stack in the log
        log.exception("intake crashed: %s", exc)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(result.agent_reply)
    print()
    print("Artifacts:")
    for name, path in result.artifacts.items():
        marker = "" if path.is_file() else "  (NOT WRITTEN)"
        print(f"  {name}: {path}{marker}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
