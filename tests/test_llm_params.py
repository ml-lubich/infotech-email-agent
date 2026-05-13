"""Tests for the centralised LLM call-parameter helper.

These pin the 2026 GPT-5 safety / cost knobs that every shot must use:
  * reasoning.effort   — minimal/low/medium/high
  * text.verbosity     — low/medium/high
  * max_output_tokens  — hard cost cap
  * safety_identifier  — abuse-signal clustering for untrusted input
  * prompt_cache_key   — deterministic cache routing
"""

from __future__ import annotations

import pytest

from invoice_agent import _llm_params as p


class TestExtractShot:
    def test_uses_minimal_effort_by_default(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini")
        assert out["reasoning"] == {"effort": "minimal"}

    def test_caps_output_tokens(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini")
        assert out["max_output_tokens"] == 2048

    def test_low_verbosity_default(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini")
        assert out["text"] == {"verbosity": "low"}

    def test_safety_identifier_constant(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini")
        assert out["safety_identifier"] == p.SAFETY_IDENTIFIER
        assert out["safety_identifier"] == "invoice-intake-agent"

    def test_cache_key_includes_shot_and_model(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini")
        assert out["prompt_cache_key"] == "extract:gpt-5-mini"


class TestVerifyShot:
    def test_uses_low_effort_by_default(self) -> None:
        out = p.llm_params(shot="verify", model="gpt-5-nano")
        assert out["reasoning"] == {"effort": "low"}

    def test_lower_token_cap_than_extract(self) -> None:
        out = p.llm_params(shot="verify", model="gpt-5-nano")
        assert out["max_output_tokens"] == 1024

    def test_cache_key_uses_verify_shot(self) -> None:
        out = p.llm_params(shot="verify", model="gpt-5-nano")
        assert out["prompt_cache_key"] == "verify:gpt-5-nano"


class TestInjectionShot:
    def test_uses_minimal_effort(self) -> None:
        out = p.llm_params(shot="injection", model="gpt-5-nano")
        assert out["reasoning"] == {"effort": "minimal"}

    def test_smallest_token_cap(self) -> None:
        out = p.llm_params(shot="injection", model="gpt-5-nano")
        assert out["max_output_tokens"] == 256


class TestOverrides:
    def test_explicit_effort_wins(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini", effort="medium")
        assert out["reasoning"] == {"effort": "medium"}

    def test_explicit_verbosity_wins(self) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini", verbosity="high")
        assert out["text"] == {"verbosity": "high"}

    @pytest.mark.parametrize("effort", ["minimal", "low", "medium", "high"])
    def test_all_effort_levels_round_trip(self, effort: str) -> None:
        out = p.llm_params(shot="extract", model="gpt-5-mini", effort=effort)  # type: ignore[arg-type]
        assert out["reasoning"]["effort"] == effort
