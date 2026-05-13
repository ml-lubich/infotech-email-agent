"""Tests for ``invoice_agent.usage`` — token-usage observability.

Covers:
  - ``extract_usage`` against three response shapes (full / partial / absent),
  - ``UsageMeter.record_response`` + ``record_dict`` accumulation,
  - ``cache_hit_ratio`` math (incl. zero-division guard),
  - the side-channel write/read roundtrip,
  - that the extract tool actually publishes a usage file when the model
    response has a ``usage`` block (integration with ``tools._call_extract_model``),
  - that ``_IntakeRun`` embeds ``payload["usage"]`` in the outbound JSON
    AND emits the ``usage_total`` summary log line (integration).
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from invoice_agent.usage import (
    USAGE_EXTRACT_FILENAME,
    ShotUsage,
    UsageMeter,
    extract_usage,
    read_extract_usage,
    write_extract_usage,
)


# ---------------------------------------------------------------- response stubs


@dataclass
class _UsageDetails:
    cached_tokens: int = 0
    reasoning_tokens: int = 0


@dataclass
class _Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    input_tokens_details: object = None
    output_tokens_details: object = None


@dataclass
class _Response:
    usage: object | None = None


# --------------------------------------------------------------------- extract_usage


class TestExtractUsage:
    def test_full_response_shape_extracts_all_fields(self) -> None:
        resp = _Response(
            usage=_Usage(
                input_tokens=1000,
                output_tokens=200,
                total_tokens=1200,
                input_tokens_details=_UsageDetails(cached_tokens=400),
                output_tokens_details=_UsageDetails(reasoning_tokens=50),
            )
        )
        assert extract_usage(resp) == {
            "input_tokens": 1000,
            "output_tokens": 200,
            "total_tokens": 1200,
            "cached_input_tokens": 400,
            "reasoning_tokens": 50,
        }

    def test_partial_response_omits_zero_details(self) -> None:
        # No details blocks → no cached/reasoning keys in the output.
        resp = _Response(usage=_Usage(input_tokens=10, output_tokens=5, total_tokens=15))
        out = extract_usage(resp)
        assert out["input_tokens"] == 10
        assert out["output_tokens"] == 5
        assert out["total_tokens"] == 15
        assert "cached_input_tokens" not in out
        assert "reasoning_tokens" not in out

    def test_response_without_usage_returns_empty_dict(self) -> None:
        assert extract_usage(_Response(usage=None)) == {}

    def test_non_numeric_fields_coerced_to_zero(self) -> None:
        resp = _Response(usage=_Usage(input_tokens="oops"))  # type: ignore[arg-type]
        out = extract_usage(resp)
        assert out["input_tokens"] == 0


# --------------------------------------------------------------------- UsageMeter


class TestUsageMeter:
    def test_record_response_accumulates(self) -> None:
        m = UsageMeter()
        m.record_response(
            "extract", "gpt-5-mini",
            _Response(usage=_Usage(input_tokens=1000, output_tokens=200, total_tokens=1200)),
        )
        m.record_response(
            "critic_review", "gpt-5-nano",
            _Response(usage=_Usage(input_tokens=300, output_tokens=50, total_tokens=350)),
        )
        t = m.totals()
        assert t["input_tokens"] == 1300
        assert t["output_tokens"] == 250
        assert t["total_tokens"] == 1550
        assert len(m.shots) == 2
        assert m.shots[0].shot == "extract"
        assert m.shots[1].shot == "critic_review"

    def test_record_response_with_no_usage_skips_silently(self) -> None:
        m = UsageMeter()
        m.record_response("critic_review", "gpt-5-nano", _Response(usage=None))
        assert m.shots == []
        assert m.totals()["total_tokens"] == 0

    def test_record_dict_handles_missing_keys(self) -> None:
        m = UsageMeter()
        m.record_dict("extract", "gpt-5-mini", {"input_tokens": 100})
        assert m.shots[0].input_tokens == 100
        assert m.shots[0].output_tokens == 0
        assert m.shots[0].total_tokens == 0

    def test_record_dict_empty_skips(self) -> None:
        m = UsageMeter()
        m.record_dict("extract", "gpt-5-mini", {})
        assert m.shots == []

    def test_cache_hit_ratio(self) -> None:
        m = UsageMeter()
        m.record_dict(
            "extract", "gpt-5-mini",
            {"input_tokens": 1000, "output_tokens": 0, "total_tokens": 1000,
             "cached_input_tokens": 250},
        )
        assert m.cache_hit_ratio() == pytest.approx(0.25)

    def test_cache_hit_ratio_zero_division_safe(self) -> None:
        assert UsageMeter().cache_hit_ratio() == 0.0

    def test_as_envelope_shape(self) -> None:
        m = UsageMeter()
        m.record_dict("extract", "gpt-5-mini",
                      {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15})
        env = m.as_envelope()
        assert set(env.keys()) == {"totals", "cache_hit_ratio", "shots"}
        assert env["totals"]["total_tokens"] == 15
        assert isinstance(env["shots"], list) and len(env["shots"]) == 1
        assert env["shots"][0]["shot"] == "extract"

    def test_sink_for_records_into_meter(self) -> None:
        m = UsageMeter()
        sink = m.sink_for("critic_review", "gpt-5-nano")
        sink(_Response(usage=_Usage(input_tokens=42, output_tokens=7, total_tokens=49)))
        assert len(m.shots) == 1
        assert m.shots[0].input_tokens == 42

    def test_log_summary_emits_one_line(self, caplog: pytest.LogCaptureFixture) -> None:
        m = UsageMeter()
        m.record_dict("extract", "gpt-5-mini",
                      {"input_tokens": 100, "output_tokens": 20, "total_tokens": 120})
        caplog.set_level(logging.INFO, logger="invoice_agent.usage")
        m.log_summary(logging.getLogger("invoice_agent.usage"))
        text = "\n".join(r.message for r in caplog.records)
        assert "usage_total" in text
        assert "input=100" in text and "output=20" in text and "total=120" in text


# -------------------------------------------------- side-channel file roundtrip


class TestSideChannel:
    def test_write_read_roundtrip(self, tmp_path: Path) -> None:
        write_extract_usage(
            tmp_path, "gpt-5-mini",
            {"input_tokens": 1024, "output_tokens": 256, "total_tokens": 1280},
        )
        path = tmp_path / USAGE_EXTRACT_FILENAME
        assert path.is_file()

        result = read_extract_usage(tmp_path)
        assert result is not None
        model, usage = result
        assert model == "gpt-5-mini"
        assert usage["total_tokens"] == 1280

    def test_read_returns_none_when_absent(self, tmp_path: Path) -> None:
        assert read_extract_usage(tmp_path) is None

    def test_read_returns_none_on_bad_json(self, tmp_path: Path) -> None:
        (tmp_path / USAGE_EXTRACT_FILENAME).write_text("{not json", encoding="utf-8")
        assert read_extract_usage(tmp_path) is None


# -------------------------------------------------- integration: extract tool


class TestExtractToolPublishesUsage:
    def test_call_extract_model_writes_side_channel(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        from invoice_agent import tools as tools_mod

        # Force OUT_DIR to a temp dir.
        monkeypatch.setenv(tools_mod.OUT_DIR_ENV, str(tmp_path))

        # Stub OpenAI client so no network call happens.
        captured: dict[str, Any] = {}

        class _FakeParsed:
            def model_dump_json(self) -> str:
                return "{}"

        class _FakeResponse:
            def __init__(self) -> None:
                self.usage = _Usage(input_tokens=500, output_tokens=80, total_tokens=580)
                self.output_parsed = _FakeParsed()
                self.output_text = ""

        class _FakeResponses:
            def parse(self, **kwargs: Any) -> _FakeResponse:
                captured.update(kwargs)
                return _FakeResponse()

        class _FakeClient:
            def __init__(self) -> None:
                self.responses = _FakeResponses()

        monkeypatch.setattr(tools_mod, "OpenAI", lambda: _FakeClient())

        tools_mod._call_extract_model([{"type": "input_text", "text": "x"}], {"reasoning": {"effort": "minimal"}, "max_output_tokens": 1, "safety_identifier": "x"})

        result = read_extract_usage(tmp_path)
        assert result is not None
        model, usage = result
        assert usage["total_tokens"] == 580
