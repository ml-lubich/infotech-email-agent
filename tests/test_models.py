"""Allow-list guard for the assignment's model constraint."""

from __future__ import annotations

import pytest

from invoice_agent.models import (
    ALLOWED_MODELS,
    DEFAULT_AGENT_MODEL,
    DEFAULT_EXTRACT_MODEL,
    resolve_model,
)


def test_allowed_models_exactly_two() -> None:
    assert ALLOWED_MODELS == frozenset({"gpt-5-mini", "gpt-5-nano"})


def test_defaults_are_allow_listed() -> None:
    assert DEFAULT_AGENT_MODEL in ALLOWED_MODELS
    assert DEFAULT_EXTRACT_MODEL in ALLOWED_MODELS


@pytest.mark.parametrize("candidate", [None, ""])
def test_resolve_model_falls_back_to_default(candidate: str | None) -> None:
    assert resolve_model(candidate, DEFAULT_AGENT_MODEL) == DEFAULT_AGENT_MODEL


def test_resolve_model_passes_through_allow_listed() -> None:
    assert resolve_model("gpt-5-nano", DEFAULT_AGENT_MODEL) == "gpt-5-nano"


def test_resolve_model_rejects_unknown_candidate() -> None:
    with pytest.raises(ValueError, match="not allow-listed"):
        resolve_model("gpt-4o", DEFAULT_AGENT_MODEL)


def test_resolve_model_rejects_unknown_default() -> None:
    with pytest.raises(ValueError, match="Invalid default model"):
        resolve_model(None, "gpt-4o")
