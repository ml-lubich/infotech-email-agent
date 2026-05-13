"""Allow-listed OpenAI models for this project.

Assignment constraint: only `gpt-5-mini` and `gpt-5-nano` may be used.
Any code path that picks a model MUST route through `resolve_model()` so
configuration drift (env override, refactor) cannot silently introduce a
different model.
"""

from __future__ import annotations

from typing import Final

ALLOWED_MODELS: Final[frozenset[str]] = frozenset({"gpt-5-mini", "gpt-5-nano"})

DEFAULT_AGENT_MODEL: Final[str] = "gpt-5-mini"
DEFAULT_EXTRACT_MODEL: Final[str] = "gpt-5-mini"
# Critic + injection-screen pipeline shots: prefer the cheaper nano model.
DEFAULT_CRITIC_MODEL: Final[str] = "gpt-5-nano"


def resolve_model(candidate: str | None, default: str) -> str:
    """Return `candidate` if allow-listed, else `default`. Raises if default invalid.

    Raises:
        ValueError: candidate is set but not in ALLOWED_MODELS, or default is
            not in ALLOWED_MODELS (programmer error).
    """
    if default not in ALLOWED_MODELS:
        raise ValueError(
            f"Invalid default model {default!r}; allowed: {sorted(ALLOWED_MODELS)}"
        )
    if candidate is None or candidate == "":
        return default
    if candidate not in ALLOWED_MODELS:
        raise ValueError(
            f"Model {candidate!r} is not allow-listed. "
            f"Allowed: {sorted(ALLOWED_MODELS)}"
        )
    return candidate
