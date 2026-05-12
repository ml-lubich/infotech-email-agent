"""Agent tools: PDF invoice extraction + customer-service notification."""

from __future__ import annotations

import base64
import json
import logging
import os
from pathlib import Path

from agents import function_tool
from openai import OpenAI

from invoice_agent.models import DEFAULT_EXTRACT_MODEL, resolve_model
from invoice_agent.pdf_extract import extract_pdf_content
from invoice_agent.schema import InvoicePayload

log = logging.getLogger(__name__)

# Per-run side-channel: agent.run_intake sets this; the notify tool reads it.
# Keep the literal name in ONE place — agent.py imports OUT_DIR_ENV from here.
OUT_DIR_ENV = "INVOICE_OUT_DIR"
# Allow-listed: only `gpt-5-mini` or `gpt-5-nano` (assignment constraint).
_EXTRACT_MODEL = resolve_model(os.getenv("INVOICE_EXTRACT_MODEL"), DEFAULT_EXTRACT_MODEL)

_SYSTEM = (
    "You extract structured invoice data from a vendor invoice package. "
    "You receive (a) raw text scraped from the PDF and (b) every embedded "
    "image from that same PDF. Some fields (often invoice number, dates, "
    "totals stamped on a logo banner) appear ONLY inside an image — read "
    "the images carefully. Use null for unknown scalar fields and [] for "
    "unknown lists. Do not invent values. Currency must be an ISO code "
    "when determinable."
)


def _user_payload(text: str, image_count: int) -> str:
    return (
        "PDF_TEXT (may be noisy or truncated):\n"
        "------------------------------------\n"
        f"{text.strip()}\n"
        "------------------------------------\n"
        f"Embedded images attached: {image_count}\n"
        "Extract all invoice fields. If a field appears in both the text and "
        "an image, prefer the image value when they conflict and add a note "
        "to source_warnings."
    )


@function_tool
def extract_invoice_from_pdf(pdf_path: str) -> str:
    """Load a local invoice PDF, read text + embedded images, return JSON.

    Performs ONE vision-capable LLM call combining PDF text and all embedded
    images so the model can recover fields that exist only inside rasterized
    regions (e.g. an invoice number stamped on a logo banner).

    Args:
        pdf_path: Absolute or relative path to a PDF file on disk.

    Returns:
        JSON string conforming to the InvoicePayload schema (vendor, invoice
        number, dates, totals, taxes, line items, ship-to, notes, warnings).
    """
    path = Path(pdf_path).expanduser().resolve()
    content = extract_pdf_content(path)
    log.info(
        "pdf parsed pages=%d text_chars=%d images=%d",
        len(content.page_texts),
        len(content.text),
        len(content.images),
    )

    user_content: list[dict[str, object]] = [
        {"type": "input_text", "text": _user_payload(content.text, len(content.images))}
    ]
    for img in content.images:
        b64 = base64.b64encode(img.png_bytes).decode("ascii")
        user_content.append(
            {
                "type": "input_image",
                "image_url": f"data:image/png;base64,{b64}",
            }
        )

    client = OpenAI()
    response = client.responses.parse(
        model=_EXTRACT_MODEL,
        input=[
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": user_content},
        ],
        text_format=InvoicePayload,
    )

    payload = response.output_parsed
    if payload is None:
        raise RuntimeError(
            "Extraction model returned no parsed payload; "
            f"raw output: {(response.output_text or '')[:500]!r}"
        )
    return payload.model_dump_json()


def write_notification_files(
    summary_markdown: str, payload_json: str, out_dir: Path
) -> tuple[Path, Path]:
    """Pure, testable side-effectful core of the notification tool.

    Parses ``payload_json``, writes a human-readable ``.txt`` and a
    pretty-printed structured ``.json`` to ``out_dir``. Raises
    ``ValueError`` if ``payload_json`` is not valid JSON.
    """
    try:
        parsed = json.loads(payload_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"payload_json is not valid JSON: {exc}") from exc

    out_dir = out_dir.expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    txt_path = out_dir / "outbound_email.txt"
    json_path = out_dir / "outbound_email.json"

    txt_path.write_text(summary_markdown, encoding="utf-8")
    json_path.write_text(
        json.dumps(parsed, indent=2, ensure_ascii=False), encoding="utf-8"
    )
    return txt_path, json_path


@function_tool
def send_customer_service_notification(
    summary_markdown: str, payload_json: str
) -> str:
    """Deliver the AP/Customer-Service notification.

    Writes two artefacts to ``$INVOICE_OUT_DIR`` (defaults to cwd):
      - outbound_email.txt  (human-readable summary)
      - outbound_email.json (structured payload for downstream processing)

    Args:
        summary_markdown: Bulleted/sectioned summary for a human reader.
        payload_json: JSON string of the structured invoice payload (merged
            with any email_context the agent wants to forward).

    Returns:
        Confirmation string listing the artefact paths.
    """
    out_dir = Path(os.getenv(OUT_DIR_ENV, ".")).expanduser().resolve()
    txt_path, json_path = write_notification_files(
        summary_markdown, payload_json, out_dir
    )
    log.info("notification written txt=%s json=%s", txt_path, json_path)
    return f"Notification written: {txt_path} and {json_path}"
