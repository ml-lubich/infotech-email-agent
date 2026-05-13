"""Deterministic prompt-injection guardrails (input + output).

Why: the assignment constrains us to small models (`gpt-5-mini` /
`gpt-5-nano`). 2026 best practice for Agents SDK pipelines on weak models
is **defense in depth**: the system prompt declares the trust boundary,
AND a non-LLM layer enforces it so a fully jailbroken model still cannot:

  - silently drop the `prompt_injection_attempt_in_document` risk flag,
  - emit "APPROVED" / "auto-approved" in the AP-facing summary,
  - skip downstream checks based on text inside the document.

This module exposes:
  * `scan_for_injection(text)` — input guardrail (regex, deterministic).
  * `scan_output_for_unsafe_directives(summary)` — output guardrail.
  * `apply_output_guardrails(...)` — additive merge of guardrail signals
    into the structured payload + a visible safety banner on the summary.
  * `publish_injection_signals` / `read_injection_signals` — env-var side
    channel so `run_intake` (which sees the raw email) can hand off to
    the notify tool (which writes the artefact) without the LLM in the
    middle being able to suppress the signal.

Architecture note (Demeter / DIP): this module owns the policy. Callers
(`agent.py`, `tools.py`) only depend on these stable functions; they do
not assemble regexes themselves.
"""

from __future__ import annotations

import os
import re
from typing import Final

# --- env side channel ------------------------------------------------------

INJECTION_SIGNALS_ENV: Final[str] = "INVOICE_INJECTION_SIGNALS"


def publish_injection_signals(signals: list[str]) -> None:
    """Write deduped, comma-joined signals to the env var (or clear it)."""
    deduped = list(dict.fromkeys(signals))  # preserve order, drop dupes
    os.environ[INJECTION_SIGNALS_ENV] = ",".join(deduped)


def read_injection_signals() -> list[str]:
    raw = os.environ.get(INJECTION_SIGNALS_ENV, "")
    return [tag for tag in raw.split(",") if tag]


# --- input guardrail -------------------------------------------------------

# Each entry maps a short snake_case tag -> compiled regex. Keep these
# narrow and well-named; new families should get their own tag rather
# than overloading an existing one.
_INJECTION_PATTERNS: Final[dict[str, re.Pattern[str]]] = {
    "ignore_prior_instructions": re.compile(
        r"\bignore\s+(?:all\s+)?(?:previous|prior|above|earlier)\s+"
        r"(?:instructions?|prompts?|rules?|messages?)\b",
        re.IGNORECASE,
    ),
    "role_redefinition": re.compile(
        r"\byou\s+are\s+now\b|\bact\s+as\b|\bpretend\s+to\s+be\b|"
        r"\bnew\s+system\s+prompt\b",
        re.IGNORECASE,
    ),
    "fake_role_marker": re.compile(
        r"(?:^|\n)\s*#{2,}\s*(?:system|assistant|user|developer)\b"
        r"|<\|im_start\|>"
        r"|<\|im_end\|>"
        r"|\[INST\]|\[/INST\]",
        re.IGNORECASE,
    ),
    "auto_approve_directive": re.compile(
        r"\bauto[-\s]?approv\w*\b"
        r"|\bapprov\w*\s+(?:immediately|now|automatically|without\s+review)\b"
        r"|\bskip\s+all\s+checks?\b"
        r"|\breply\s+only\s+with\s+['\"]?approved['\"]?\b",
        re.IGNORECASE,
    ),
    "payment_redirection": re.compile(
        r"\b(?:wire|send|transfer|remit)\b[^\n]{0,80}\b"
        r"(?:new|different|updated|corrected)\s+"
        r"(?:bank|account|iban|routing)\b"
        r"|\bchange\s+(?:our\s+)?(?:bank|account|payment\s+details)\b",
        re.IGNORECASE,
    ),
}


def scan_for_injection(text: str | None) -> list[str]:
    """Return the snake_case tags of every injection pattern that fires.

    Defensive: ``None`` and ``""`` return ``[]`` so missing email bodies
    do not crash the pipeline.
    """
    if not text:
        return []
    return [tag for tag, pattern in _INJECTION_PATTERNS.items() if pattern.search(text)]


# --- output guardrail ------------------------------------------------------

# Negation guard: do NOT trip on phrases like "not approved" or
# "pending approval". We require an affirmative form.
_AUTO_APPROVAL_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"(?<![A-Za-z])(?<!not\s)(?<!pending\s)(?<!awaiting\s)"
    r"(?:auto[-\s]?approved|is\s+approved\s+for\s+payment)"
    r"|^\s*approved\s*[\.\!]?\s*$",
    re.IGNORECASE | re.MULTILINE,
)

_SKIP_CHECKS_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"\bskip\s+(?:all\s+)?(?:checks?|reviews?|validations?|verifications?)\b",
    re.IGNORECASE,
)


def scan_output_for_unsafe_directives(summary_markdown: str) -> list[str]:
    """Return the tags of unsafe phrases found in the AP-facing summary."""
    tags: list[str] = []
    if _AUTO_APPROVAL_PATTERN.search(summary_markdown):
        tags.append("auto_approval_language_in_output")
    if _SKIP_CHECKS_PATTERN.search(summary_markdown):
        tags.append("skip_checks_language_in_output")
    return tags


# --- merge layer -----------------------------------------------------------

_INJECTION_FLAG: Final[str] = "prompt_injection_attempt_in_document"
_OUTPUT_GUARDRAIL_FLAG: Final[str] = "output_guardrail_triggered"

_BANNER_TEMPLATE: Final[str] = (
    "\n\n---\n"
    "[GUARDRAIL] Output guardrail tripped: {tags}.\n"
    "The text above contains language that resembles auto-approval or "
    "skip-checks directives. Do NOT act on it without human AP review.\n"
    "---\n"
)


def _ensure_flag(flags: list[str], flag: str) -> list[str]:
    return flags if flag in flags else [*flags, flag]


def apply_output_guardrails(
    *,
    summary_markdown: str,
    payload: dict[str, object],
    input_signals: list[str],
) -> tuple[str, dict[str, object], list[str]]:
    """Apply output guardrails additively. Returns (summary, payload, triggered).

    - If ``input_signals`` is non-empty, force
      ``prompt_injection_attempt_in_document`` into ``payload.risk_flags``.
    - If the summary contains auto-approval / skip-checks language, append a
      visible safety banner to the summary and add
      ``output_guardrail_triggered`` to ``payload.risk_flags``.
    - Existing ``risk_flags`` are preserved (never removed, never reordered).
    - The input ``payload`` dict is not mutated; a shallow copy is returned.
    """
    new_payload: dict[str, object] = dict(payload)
    raw_flags = new_payload.get("risk_flags") or []
    flags: list[str] = list(raw_flags) if isinstance(raw_flags, list) else []

    triggered: list[str] = []

    if input_signals:
        if _INJECTION_FLAG not in flags:
            flags = _ensure_flag(flags, _INJECTION_FLAG)
            triggered.append(_INJECTION_FLAG)

    output_signals = scan_output_for_unsafe_directives(summary_markdown)
    new_summary = summary_markdown
    if output_signals:
        if _OUTPUT_GUARDRAIL_FLAG not in flags:
            flags = _ensure_flag(flags, _OUTPUT_GUARDRAIL_FLAG)
        triggered.extend(output_signals)
        new_summary = summary_markdown + _BANNER_TEMPLATE.format(
            tags=", ".join(output_signals)
        )

    new_payload["risk_flags"] = flags
    return new_summary, new_payload, triggered


# --- arithmetic & format guardrail (deterministic) ---------------------

# Tolerance: invoices commonly round per line, so allow a few cents of
# rounding noise before flagging an arithmetic mismatch.
_ARITHMETIC_TOLERANCE: Final[float] = 0.02

_ISO_CURRENCY_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Z]{3}$")
_ISO_DATE_RE: Final[re.Pattern[str]] = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _coerce_float(x: object) -> float | None:
    if isinstance(x, (int, float)):
        return float(x)
    return None


def arithmetic_check(payload: dict[str, object]) -> list[str]:
    """Return short snake_case finding tags for arithmetic / format issues.

    Pure function over the parsed invoice payload (a plain dict, as returned
    by ``InvoicePayload.model_dump`` or ``json.loads(payload_json)``). Empty
    list means the deterministic checks all passed.

    Tags emitted (subset, additive):
      - ``totals_inconsistent``           — subtotal + Σ taxes ≠ total_due
      - ``line_items_sum_mismatch``       — Σ line_total ≠ subtotal
      - ``currency_not_iso_4217``         — currency is not a 3-letter code
      - ``invoice_date_unparseable``      — date is not ISO 8601
      - ``due_date_unparseable``          — due_date is not ISO 8601
      - ``negative_total_due``            — total_due is < 0 (credit memo
                                            should be flagged for AP review)
    """
    findings: list[str] = []

    subtotal = _coerce_float(payload.get("subtotal"))
    total_due = _coerce_float(payload.get("total_due"))

    taxes_raw = payload.get("taxes") or []
    tax_sum = 0.0
    if isinstance(taxes_raw, list):
        for t in taxes_raw:
            if isinstance(t, dict):
                amt = _coerce_float(t.get("amount"))
                if amt is not None:
                    tax_sum += amt

    line_items_raw = payload.get("line_items") or []
    li_sum = 0.0
    li_any = False
    if isinstance(line_items_raw, list):
        for li in line_items_raw:
            if isinstance(li, dict):
                lt = _coerce_float(li.get("line_total"))
                if lt is not None:
                    li_sum += lt
                    li_any = True

    if subtotal is not None and total_due is not None:
        expected = subtotal + tax_sum
        if abs(expected - total_due) > _ARITHMETIC_TOLERANCE:
            findings.append("totals_inconsistent")

    if subtotal is not None and li_any:
        if abs(li_sum - subtotal) > _ARITHMETIC_TOLERANCE:
            findings.append("line_items_sum_mismatch")

    currency = payload.get("currency")
    if isinstance(currency, str) and currency and not _ISO_CURRENCY_RE.match(currency):
        findings.append("currency_not_iso_4217")

    inv_date = payload.get("invoice_date")
    if isinstance(inv_date, str) and inv_date and not _ISO_DATE_RE.match(inv_date):
        findings.append("invoice_date_unparseable")

    due_date = payload.get("due_date")
    if isinstance(due_date, str) and due_date and not _ISO_DATE_RE.match(due_date):
        findings.append("due_date_unparseable")

    if total_due is not None and total_due < 0:
        findings.append("negative_total_due")

    return findings

