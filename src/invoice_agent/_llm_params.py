"""Centralised OpenAI Responses API call parameters for the 2026 GPT-5 family.

Why this module exists (architecture / Demeter):
  Every shot that touches the model — extraction, verifier, injection
  screen — needs the same answer to the same questions:

    * how much reasoning effort?
    * how chatty is the output allowed to be?
    * what's the hard token cap so a runaway response cannot drain the
      budget?
    * what stable identifier do we pass for OpenAI's abuse / safety
      signals (the documents we send are UNTRUSTED user data)?
    * what prompt-cache key keeps repeated runs cheap?

  Putting these in one place means we cannot drift: the verifier and
  the extractor will always use the same safety identifier, the same
  cache routing strategy, and consistent token caps. Callers depend on
  ``llm_params(...)`` only — they never assemble these kwargs by hand.

Notes on the GPT-5 family (assignment-mandated `gpt-5-mini` /
`gpt-5-nano`):
  * GPT-5 reasoning models do NOT honour ``temperature`` / ``top_p``
    adjustments — the documented knob is ``reasoning.effort`` instead.
    We therefore do not export those parameters.
  * ``text.verbosity`` is the documented length-policy knob in 2026.
  * ``safety_identifier`` (Responses API) is the per-application
    stable ID OpenAI uses to cluster abuse signals when you send
    user-derived content through the API. Required-class best practice
    for any pipeline that ingests untrusted documents.
"""

from __future__ import annotations

from typing import Final, Literal, TypedDict

# Hard cost ceilings. Conservative on purpose — invoice extraction never
# needs more than a few hundred tokens of output, and a runaway
# response is the failure mode we are guarding against.
_MAX_TOKENS_EXTRACT: Final[int] = 2048
_MAX_TOKENS_VERIFY: Final[int] = 1024
_MAX_TOKENS_INJECTION: Final[int] = 256

# Stable identifier OpenAI uses to attribute abuse signals to this
# application. Documented under "Safety best practices" for endpoints
# that ingest user-supplied content.
SAFETY_IDENTIFIER: Final[str] = "invoice-intake-agent"


ReasoningEffort = Literal["minimal", "low", "medium", "high"]
Verbosity = Literal["low", "medium", "high"]


class LLMCallParams(TypedDict, total=False):
    """Strictly-typed kwargs passed to ``client.responses.parse(**...)``."""

    reasoning: dict[str, str]
    text: dict[str, str]
    max_output_tokens: int
    safety_identifier: str
    prompt_cache_key: str


def llm_params(
    *,
    shot: Literal["extract", "verify", "injection"],
    model: str,
    effort: ReasoningEffort | None = None,
    verbosity: Verbosity = "low",
) -> LLMCallParams:
    """Return the canonical kwargs for a given shot.

    Defaults are tuned for cost + safety:
      * extract    → effort=minimal (structured, no reasoning needed)
      * verify     → effort=low     (light cross-check)
      * injection  → effort=minimal (binary scan, no reasoning needed)
      * verbosity  → low for all (we want short structured outputs)
      * cache key  → "<shot>:<model>" so repeated runs hit the cache
    """
    chosen_effort: ReasoningEffort = effort or _default_effort(shot)
    cap: int = _max_tokens_for(shot)

    return {
        "reasoning": {"effort": chosen_effort},
        "text": {"verbosity": verbosity},
        "max_output_tokens": cap,
        "safety_identifier": SAFETY_IDENTIFIER,
        "prompt_cache_key": f"{shot}:{model}",
    }


def _default_effort(shot: str) -> ReasoningEffort:
    if shot == "verify":
        return "low"
    # extract + injection are deterministic-style tasks; no reasoning.
    return "minimal"


def _max_tokens_for(shot: str) -> int:
    if shot == "extract":
        return _MAX_TOKENS_EXTRACT
    if shot == "verify":
        return _MAX_TOKENS_VERIFY
    return _MAX_TOKENS_INJECTION
