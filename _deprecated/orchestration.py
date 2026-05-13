"""Pass-3 deterministic post-checks + merge layer.

The orchestration module owns every **deterministic** post-extraction
check (arithmetic, vendor-domain, duplicate-history) and the pure merge
step that folds the verifier report into the outbound payload. It is
intentionally LLM-free: the small models extract; this layer guards.

Architectural notes:
  - Pure functions where possible; only ``duplicate_history_scan`` and
    ``run_post_checks`` touch the disk.
  - Demeter: callers depend on the named-tuple results and on
    ``run_post_checks`` only — no shared mutable state.
  - Append-only finding tags. Reusing an existing tag is fine; renaming
    one is breaking.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Literal, NamedTuple

from invoice_agent.verifier import VerificationReport

log = logging.getLogger(__name__)


# ---------------------------------------------------- arithmetic_check

# Two-cent rounding tolerance: invoices commonly round per line.
_ARITHMETIC_TOLERANCE: Final[float] = 0.02


class ArithmeticResult(NamedTuple):
    """Tri-state arithmetic verdict.

    ``ok`` is ``None`` when one of subtotal / total_due is missing
    (we can't decide), ``True`` when the books balance within
    tolerance, ``False`` otherwise.
    """

    ok: bool | None
    taxes_sum: float
    delta: float | None


def _coerce_float(x: object) -> float | None:
    if isinstance(x, (int, float)):
        return float(x)
    return None


def arithmetic_check(payload: dict[str, object]) -> ArithmeticResult:
    """Subtotal + Σ taxes ?= total_due, within tolerance."""
    subtotal = _coerce_float(payload.get("subtotal"))
    total_due = _coerce_float(payload.get("total_due"))

    taxes_raw = payload.get("taxes") or []
    taxes_sum = 0.0
    if isinstance(taxes_raw, list):
        for t in taxes_raw:
            if isinstance(t, dict):
                amt = _coerce_float(t.get("amount"))
                if amt is not None:
                    taxes_sum += amt

    if subtotal is None or total_due is None:
        return ArithmeticResult(ok=None, taxes_sum=taxes_sum, delta=None)

    # Sign convention: delta = expected - actual. Negative means we under-billed.
    delta = (subtotal + taxes_sum) - total_due
    ok = abs(delta) <= _ARITHMETIC_TOLERANCE
    return ArithmeticResult(ok=ok, taxes_sum=taxes_sum, delta=delta)


# ---------------------------------------------------- vendor_domain_check


@dataclass(frozen=True)
class DomainResult:
    """Tri-state vendor-domain verdict."""

    match: bool | None
    sender_domain: str | None


_DOMAIN_TOKEN_RE: Final[re.Pattern[str]] = re.compile(r"[a-z0-9]+")


def _extract_email_address(sender: object) -> str | None:
    """Pull the address out of a string or a Graph-shaped dict."""
    if isinstance(sender, str):
        return sender
    if isinstance(sender, dict):
        ea = sender.get("EmailAddress")
        if isinstance(ea, dict):
            addr = ea.get("Address")
            if isinstance(addr, str):
                return addr
        addr = sender.get("Address") or sender.get("email")
        if isinstance(addr, str):
            return addr
    return None


def _vendor_tokens(vendor_name: str) -> set[str]:
    """Lowercase alphanumeric tokens, minus stopwords."""
    stop = {"inc", "llc", "ltd", "co", "corp", "company", "services", "the"}
    return {
        tok for tok in _DOMAIN_TOKEN_RE.findall(vendor_name.lower()) if tok and tok not in stop
    }


def vendor_domain_check(
    sender_email: object | None, vendor_name: str | None
) -> DomainResult:
    """Compare the sender's domain to the vendor name (token overlap)."""
    if not sender_email or not vendor_name:
        return DomainResult(match=None, sender_domain=None)
    addr = _extract_email_address(sender_email)
    if not addr or "@" not in addr:
        return DomainResult(match=None, sender_domain=None)
    domain = addr.rsplit("@", 1)[1].strip().lower()
    if not domain:
        return DomainResult(match=None, sender_domain=None)

    domain_tokens = set(_DOMAIN_TOKEN_RE.findall(domain))
    vendor_tokens = _vendor_tokens(vendor_name)
    if not vendor_tokens:
        return DomainResult(match=None, sender_domain=domain)

    # Match if any meaningful vendor token appears in the domain
    # (full token; substring would over-match).
    match = any(tok in domain_tokens for tok in vendor_tokens)
    return DomainResult(match=match, sender_domain=domain)


# ---------------------------------------------------- duplicate_history_scan

_HISTORY_SCAN_CAP: Final[int] = 200


def duplicate_history_scan(
    invoice_number: str | None, search_root: Path
) -> list[Path]:
    """Find prior outbound JSONs whose invoice_number matches.

    Walks ``search_root/*/outbound_email.json`` and returns the matching
    paths, capped at ``_HISTORY_SCAN_CAP``. Returns ``[]`` on missing
    inputs or when the directory does not exist. Malformed JSON files
    are skipped (not raised).
    """
    if not invoice_number:
        return []
    if not search_root.is_dir():
        return []
    hits: list[Path] = []
    for entry in sorted(search_root.iterdir()):
        if not entry.is_dir():
            continue
        f = entry / "outbound_email.json"
        if not f.is_file():
            continue
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if isinstance(data, dict) and data.get("invoice_number") == invoice_number:
            hits.append(f)
            if len(hits) >= _HISTORY_SCAN_CAP:
                break
    return hits


# ---------------------------------------------------- verifier merge


_DISAGREEMENT_FLAG: Final[str] = "verifier_disagreement"
_LOW_CONFIDENCE_FLAG: Final[str] = "low_confidence_extraction"


def merge_verifier_into_payload(
    payload: dict[str, object], report: VerificationReport
) -> dict[str, object]:
    """Return a NEW payload with verifier confidence + notes folded in."""
    new = dict(payload)
    raw_flags = new.get("risk_flags") or []
    flags: list[str] = list(raw_flags) if isinstance(raw_flags, list) else []

    new["confidence"] = dict(report.field_confidence)
    new["verifier_notes"] = list(report.verifier_notes)

    if report.disagreements and _DISAGREEMENT_FLAG not in flags:
        flags.append(_DISAGREEMENT_FLAG)
    if any(level == "low" for level in report.field_confidence.values()):
        if _LOW_CONFIDENCE_FLAG not in flags:
            flags.append(_LOW_CONFIDENCE_FLAG)

    new["risk_flags"] = flags
    return new


def summarize_confidence(report: VerificationReport) -> Literal["high", "medium", "low"]:
    """Reduce per-field confidence to a single label."""
    levels = list(report.field_confidence.values())
    if not levels:
        return "medium"
    if "low" in levels:
        return "low"
    if "medium" in levels:
        return "medium"
    return "high"


# ---------------------------------------------------- run_post_checks

_ARITH_FLAG: Final[str] = "arithmetic_mismatch"
_DOMAIN_FLAG: Final[str] = "vendor_domain_mismatch"
_DUP_FLAG: Final[str] = "duplicate_invoice_number_in_history"


def run_post_checks(
    payload: dict[str, object], search_root: Path
) -> tuple[dict[str, object], list[str]]:
    """Run arithmetic + domain + duplicate checks; merge results into payload.

    Returns ``(new_payload, triggered_flags)``. ``new_payload`` is a
    shallow copy with deduped ``risk_flags`` extended additively.
    """
    new = dict(payload)
    raw_flags = new.get("risk_flags") or []
    flags: list[str] = list(raw_flags) if isinstance(raw_flags, list) else []
    triggered: list[str] = []

    # Arithmetic
    arith = arithmetic_check(payload)
    log.info(
        "decision step=arithmetic action=check ok=%s taxes_sum=%.2f delta=%s",
        arith.ok,
        arith.taxes_sum,
        f"{arith.delta:.2f}" if arith.delta is not None else "None",
    )
    if arith.ok is False and _ARITH_FLAG not in flags:
        flags.append(_ARITH_FLAG)
        triggered.append(_ARITH_FLAG)

    # Vendor domain
    ec = payload.get("email_context") or {}
    sender_email = ec.get("sender_email") if isinstance(ec, dict) else None
    vendor_name = payload.get("vendor_name") if isinstance(payload.get("vendor_name"), str) else None
    domain = vendor_domain_check(sender_email, vendor_name)
    log.info(
        "decision step=domain action=check sender_domain=%r vendor=%r match=%s",
        domain.sender_domain, vendor_name, domain.match,
    )
    if domain.match is False and _DOMAIN_FLAG not in flags:
        flags.append(_DOMAIN_FLAG)
        triggered.append(_DOMAIN_FLAG)

    # Duplicate history
    inv = payload.get("invoice_number") if isinstance(payload.get("invoice_number"), str) else None
    hits = duplicate_history_scan(inv, search_root)
    log.info(
        "decision step=duplicate action=scan history_files=%d invoice_number=%r",
        len(hits), inv,
    )
    if hits and _DUP_FLAG not in flags:
        flags.append(_DUP_FLAG)
        triggered.append(_DUP_FLAG)

    new["risk_flags"] = flags
    return new, triggered
