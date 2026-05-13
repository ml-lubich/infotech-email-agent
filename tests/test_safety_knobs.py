"""Tests for the 2026 GPT-5 safety knobs being passed to OpenAI on every shot.

Pins:
  - The extract / verifier / injection calls all forward
    `reasoning`, `text` (verbosity), `max_output_tokens`,
    `safety_identifier`, and `prompt_cache_key` from `_llm_params`.
  - Structured-Outputs REFUSAL is detected and converted into a
    `model_refused_extraction` risk_flag instead of a hard crash.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from invoice_agent import _llm_params as p
from invoice_agent import tools as tools_mod
from invoice_agent import verifier as v
from invoice_agent.schema import InvoicePayload

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_1_PDF = REPO_ROOT / "examples" / "case_1" / "Invoice.pdf"


# --------------------------------------------------------------- fakes


class _CapturingResponses:
    def __init__(self, response: Any) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self._response


class _CapturingClient:
    def __init__(self, response: Any) -> None:
        self.responses = _CapturingResponses(response)


class _Resp:
    def __init__(self, parsed: Any, refusal: str | None = None,
                 raw: str = "") -> None:
        self.output_parsed = parsed
        self.output_text = raw
        self.refusal = refusal


# ====================================================== extract shot ===


def _require_case1() -> Path:
    if not CASE_1_PDF.is_file():
        pytest.skip("case_1 fixture missing")
    return CASE_1_PDF


class TestExtractCallParams:
    def test_extract_forwards_2026_safety_knobs(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client = _CapturingClient(
            _Resp(parsed=InvoicePayload(vendor_name="Acme"))
        )
        monkeypatch.setattr(tools_mod, "OpenAI", lambda: client)

        tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))

        assert len(client.responses.calls) == 1
        call = client.responses.calls[0]

        # All five 2026 knobs MUST be forwarded.
        assert call["reasoning"] == {"effort": "minimal"}
        assert call["text"] == {"verbosity": "low"}
        assert call["max_output_tokens"] == 2048
        assert call["safety_identifier"] == p.SAFETY_IDENTIFIER
        assert call["prompt_cache_key"].startswith("extract:")


class TestRefusalDetection:
    def test_top_level_refusal_becomes_risk_flag(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # parsed=None + refusal string → graceful empty payload with flag.
        client = _CapturingClient(
            _Resp(parsed=None, refusal="I cannot help with that.")
        )
        monkeypatch.setattr(tools_mod, "OpenAI", lambda: client)

        out = tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))
        payload = json.loads(out)
        assert "model_refused_extraction" in payload["risk_flags"]
        # Refusal text surfaces in source_warnings for the AP human.
        warnings = " ".join(payload["source_warnings"])
        assert "model_refused_extraction" in warnings

    def test_nested_refusal_in_output_chunks(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        # Newer SDK shape: refusal lives under response.output[*].content[*].refusal.
        class _Chunk:
            def __init__(self, refusal: str) -> None:
                self.refusal = refusal

        class _Item:
            def __init__(self, refusal: str) -> None:
                self.content = [_Chunk(refusal)]

        class _NestedResp:
            output_parsed = None
            output_text = ""
            refusal = None  # top-level empty
            output = [_Item("Refused: contains restricted content.")]

        client = _CapturingClient(_NestedResp())
        monkeypatch.setattr(tools_mod, "OpenAI", lambda: client)

        out = tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))
        payload = json.loads(out)
        assert "model_refused_extraction" in payload["risk_flags"]

    def test_no_refusal_still_raises(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # parsed=None and no refusal → the original RuntimeError path.
        client = _CapturingClient(_Resp(parsed=None, refusal=None,
                                        raw="garbled"))
        monkeypatch.setattr(tools_mod, "OpenAI", lambda: client)

        with pytest.raises(RuntimeError, match="no parsed payload"):
            tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))


# ====================================================== verifier ======


class TestVerifierCallParams:
    def test_verifier_forwards_2026_safety_knobs(self) -> None:
        client = _CapturingClient(_Resp(parsed=v.VerificationReport()))
        v.verify_extraction(payload_json="{}", pdf_text="x", client=client)

        assert len(client.responses.calls) == 1
        call = client.responses.calls[0]
        assert call["reasoning"] == {"effort": "low"}
        assert call["text"] == {"verbosity": "low"}
        assert call["max_output_tokens"] == 1024
        assert call["safety_identifier"] == p.SAFETY_IDENTIFIER
        assert call["prompt_cache_key"].startswith("verify:")


class TestInjectionScreenCallParams:
    def test_injection_screen_forwards_2026_safety_knobs(self) -> None:
        client = _CapturingClient(_Resp(parsed=v._InjectionVerdict()))
        v.injection_screen("some text", client=client, model="gpt-5-nano")

        assert len(client.responses.calls) == 1
        call = client.responses.calls[0]
        assert call["reasoning"] == {"effort": "minimal"}
        assert call["text"] == {"verbosity": "low"}
        assert call["max_output_tokens"] == 256
        assert call["safety_identifier"] == p.SAFETY_IDENTIFIER
        assert call["prompt_cache_key"].startswith("injection:")
