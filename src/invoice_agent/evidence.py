"""Pure helpers that turn finding tags into AP-facing ``Evidence`` quotes.

Why a separate module: keeping evidence-extraction PURE (no I/O, no
LLM, no global state) makes it cheap to test and impossible to regress
the underlying pipeline math by accident. Every public function here
takes the same source text the original shot already saw and returns
zero or one ``Evidence`` per finding tag.

DIP / Demeter:
  - Re-uses the SAME compiled regexes from ``guardrails._INJECTION_PATTERNS``
    (single source of truth for what a "prompt injection" looks like).
  - Knows nothing about ``PipelineState``; callers (``agent.py``) pass
    the resulting list to ``state.record(..., evidence=...)``.

Quote contract (per ``docs/API.md``):
  - ``quote`` is at most 240 characters.
  - ``source`` is one of ``email | pdf_text | extracted_payload |
    verifier | summary``.
  - ``location`` is a short human hint (e.g. "PDF page 1",
    "field: total_due"). Optional.
"""

from __future__ import annotations

from typing import Final

from invoice_agent.guardrails import _INJECTION_PATTERNS
from invoice_agent.schema import Evidence, EvidenceSource
from invoice_agent.verifier import Disagreement

_QUOTE_MAX: Final[int] = 240
_WINDOW_RADIUS: Final[int] = 80  # chars on each side of a regex match


def _window(text: str, start: int, end: int) -> str:
    """Return ``text[start..end]`` padded with up to ``_WINDOW_RADIUS``
    chars of context on each side, capped at ``_QUOTE_MAX`` total.
    """
    lo = max(0, start - _WINDOW_RADIUS)
    hi = min(len(text), end + _WINDOW_RADIUS)
    snippet = text[lo:hi].strip()
    if lo > 0:
        snippet = "…" + snippet
    if hi < len(text):
        snippet = snippet + "…"
    if len(snippet) > _QUOTE_MAX:
        snippet = snippet[: _QUOTE_MAX - 1] + "…"
    return snippet


def quote_for_regex_finding(
    tag: str,
    text: str,
    *,
    source: EvidenceSource,
    location: str | None = None,
) -> Evidence | None:
    """Re-run the named injection regex on ``text`` and return one Evidence.

    Returns ``None`` if the tag is not a known regex tag, or if the
    pattern does not match the supplied text (e.g. the finding came
    from a different shot or a different source).
    """
    pattern = _INJECTION_PATTERNS.get(tag)
    if pattern is None or not text:
        return None
    match = pattern.search(text)
    if match is None:
        return None
    return Evidence(
        finding=tag,
        source=source,
        quote=_window(text, match.start(), match.end()),
        location=location,
    )


def quotes_for_email_injection(
    tags: list[str],
    email_body: str,
) -> list[Evidence]:
    """Build one Evidence per tag that fired against the email body."""
    out: list[Evidence] = []
    for tag in tags:
        ev = quote_for_regex_finding(
            tag, email_body, source="email", location="email.body"
        )
        if ev is not None:
            out.append(ev)
    return out


def quote_for_disagreement(d: Disagreement) -> Evidence:
    """Render a verifier ``Disagreement`` as a single Evidence row.

    Pure: no source-text lookup; the disagreement already carries the
    v1 value and the suggested value. We surface them verbatim so the
    AP reviewer can see exactly what the verifier compared.
    """
    quote = (
        f"v1={d.v1_value!r} suggested={d.suggested_value!r} — {d.reason}"
    )
    if len(quote) > _QUOTE_MAX:
        quote = quote[: _QUOTE_MAX - 1] + "…"
    return Evidence(
        finding=f"verifier_disagreement_{d.field}",
        source="verifier",
        quote=quote,
        location=f"field: {d.field}",
    )


def quote_for_low_confidence(field: str) -> Evidence:
    """Render a `low_confidence_<field>` finding as an Evidence row."""
    return Evidence(
        finding=f"low_confidence_{field}",
        source="verifier",
        quote=f"verifier graded `{field}` as low-confidence vs the PDF.",
        location=f"field: {field}",
    )


def quote_for_arithmetic(
    payload: dict[str, object] | None,
    finding: str,
) -> Evidence | None:
    """Reconstruct the arithmetic mismatch for a single finding.

    Currently only ``totals_inconsistent`` is supported; other
    finding tags return ``None``.
    """
    if not payload or finding != "totals_inconsistent":
        return None
    subtotal = payload.get("subtotal")
    total_due = payload.get("total_due")
    taxes = payload.get("taxes") or []
    tax_sum = 0.0
    if isinstance(taxes, list):
        for t in taxes:
            if isinstance(t, dict):
                amt = t.get("amount")
                if isinstance(amt, (int, float)):
                    tax_sum += float(amt)
    if not isinstance(subtotal, (int, float)) or not isinstance(
        total_due, (int, float)
    ):
        return None
    expected = float(subtotal) + tax_sum
    quote = (
        f"subtotal={float(subtotal):.2f} + taxes={tax_sum:.2f} "
        f"= {expected:.2f}, but stated total_due={float(total_due):.2f}."
    )
    return Evidence(
        finding=finding,
        source="extracted_payload",
        quote=quote,
        location="field: total_due",
    )
