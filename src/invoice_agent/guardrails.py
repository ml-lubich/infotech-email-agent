"""Plain-Python prompt-injection checks (input + output).

Why this exists: the agent itself runs on a small LLM, and small
LLMs are easier to jailbreak. So we wrap the LLM with non-LLM
checks. Even a fully tricked model cannot:

  - drop the ``prompt_injection_attempt_in_document`` flag,
  - emit "APPROVED" / "auto-approved" in the AP summary,
  - skip downstream checks based on something the document said.

What this module exposes:
  * ``scan_for_injection(text)`` — regex over the email body.
  * ``scan_output_for_unsafe_directives(summary)`` — regex over the
    AP-facing summary the agent produced.
  * ``apply_output_guardrails(...)`` — merge guardrail signals into
    the payload's ``risk_flags`` and append a visible safety banner.
  * ``publish_injection_signals`` / ``read_injection_signals`` —
    a tiny env-var hand-off so ``run_intake`` (which sees the raw
    email) can pass signals to the notify tool (which writes the
    artefact) without the LLM in the middle being able to suppress
    them.

This module owns the policy. Other modules (``agent.py``,
``tools.py``) only call these functions; they never build regexes
themselves.
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
        # Permit a short filler word (e.g. "the") between "ignore" and the
        # priority keyword so "ignore the above messages" still trips.
        r"\bignore\s+(?:all\s+|the\s+)?(?:previous|prior|above|earlier)\s+"
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


def _sum_amounts(items: object, key: str) -> tuple[float, bool]:
    """Sum the floats found at ``items[i][key]``. Returns ``(total, any_seen)``."""
    if not isinstance(items, list):
        return 0.0, False
    total = 0.0
    seen = False
    for entry in items:
        if not isinstance(entry, dict):
            continue
        amt = _coerce_float(entry.get(key))
        if amt is not None:
            total += amt
            seen = True
    return total, seen


def _check_totals_match(
    subtotal: float | None, tax_sum: float, total_due: float | None
) -> str | None:
    if subtotal is None or total_due is None:
        return None
    if abs((subtotal + tax_sum) - total_due) > _ARITHMETIC_TOLERANCE:
        return "totals_inconsistent"
    return None


def _check_line_items_match(
    li_sum: float, li_any: bool, subtotal: float | None
) -> str | None:
    if subtotal is None or not li_any:
        return None
    if abs(li_sum - subtotal) > _ARITHMETIC_TOLERANCE:
        return "line_items_sum_mismatch"
    return None


def _check_pattern(
    value: object, pattern: re.Pattern[str], tag: str
) -> str | None:
    if isinstance(value, str) and value and not pattern.match(value):
        return tag
    return None


def _check_negative_total(total_due: float | None) -> str | None:
    if total_due is not None and total_due < 0:
        return "negative_total_due"
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
    subtotal = _coerce_float(payload.get("subtotal"))
    total_due = _coerce_float(payload.get("total_due"))
    tax_sum, _ = _sum_amounts(payload.get("taxes") or [], "amount")
    li_sum, li_any = _sum_amounts(payload.get("line_items") or [], "line_total")

    candidates = [
        _check_totals_match(subtotal, tax_sum, total_due),
        _check_line_items_match(li_sum, li_any, subtotal),
        _check_pattern(payload.get("currency"), _ISO_CURRENCY_RE, "currency_not_iso_4217"),
        _check_pattern(payload.get("invoice_date"), _ISO_DATE_RE, "invoice_date_unparseable"),
        _check_pattern(payload.get("due_date"), _ISO_DATE_RE, "due_date_unparseable"),
        _check_negative_total(total_due),
    ]
    return [tag for tag in candidates if tag is not None]

