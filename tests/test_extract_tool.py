"""Tests for the underlying extract/notify implementations (no SDK runtime).

`@function_tool` wraps the public callables; the real bodies live in
`_extract_invoice_from_pdf_impl` / `_send_customer_service_notification_impl`
and are unit-tested here with a mocked OpenAI client.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from invoice_agent import tools as tools_mod
from invoice_agent.schema import InvoicePayload

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_1_PDF = REPO_ROOT / "examples" / "case_1" / "Invoice.pdf"


class _FakeResponse:
    def __init__(self, parsed: InvoicePayload | None, raw: str = "") -> None:
        self.output_parsed = parsed
        self.output_text = raw


class _FakeResponses:
    def __init__(self, response: _FakeResponse) -> None:
        self._response = response
        self.calls: list[dict[str, Any]] = []

    def parse(self, **kwargs: Any) -> _FakeResponse:
        self.calls.append(kwargs)
        return self._response


class _FakeOpenAI:
    def __init__(self, response: _FakeResponse) -> None:
        self.responses = _FakeResponses(response)


def _require_case1() -> Path:
    if not CASE_1_PDF.is_file():
        pytest.skip("case_1 fixture missing")
    return CASE_1_PDF


def test_extract_impl_happy_path_returns_payload_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = InvoicePayload(vendor_name="Acme", invoice_number="INV-9")
    fake = _FakeOpenAI(_FakeResponse(parsed=payload))
    monkeypatch.setattr(tools_mod, "OpenAI", lambda: fake)

    out = tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))
    parsed = json.loads(out)
    assert parsed["vendor_name"] == "Acme"
    assert parsed["invoice_number"] == "INV-9"

    assert len(fake.responses.calls) == 1
    call = fake.responses.calls[0]
    assert call["model"] in {"gpt-5-mini", "gpt-5-nano"}
    user_msg = call["input"][1]
    assert user_msg["role"] == "user"
    types = {item["type"] for item in user_msg["content"]}
    assert "input_text" in types and "input_image" in types


def test_extract_impl_raises_when_parser_returns_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeOpenAI(_FakeResponse(parsed=None, raw="garbled"))
    monkeypatch.setattr(tools_mod, "OpenAI", lambda: fake)

    with pytest.raises(RuntimeError, match="no parsed payload"):
        tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))


def test_extract_impl_raises_when_parser_returns_none_no_raw_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = _FakeOpenAI(_FakeResponse(parsed=None, raw=""))
    monkeypatch.setattr(tools_mod, "OpenAI", lambda: fake)

    with pytest.raises(RuntimeError, match="no parsed payload"):
        tools_mod._extract_invoice_from_pdf_impl(str(_require_case1()))


def test_send_notification_impl_uses_out_dir_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(tools_mod.OUT_DIR_ENV, str(tmp_path))
    result = tools_mod._send_customer_service_notification_impl(
        "# hi\n", json.dumps({"vendor_name": "X"})
    )
    assert "Notification written" in result
    assert (tmp_path / "outbound_email.txt").is_file()
    assert (tmp_path / "outbound_email.json").is_file()


def test_send_notification_impl_defaults_to_cwd_when_env_unset(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(tools_mod.OUT_DIR_ENV, raising=False)
    monkeypatch.chdir(tmp_path)
    result = tools_mod._send_customer_service_notification_impl(
        "summary", json.dumps({"k": 1})
    )
    assert "Notification written" in result
    assert (tmp_path / "outbound_email.txt").is_file()


def test_user_payload_includes_image_count_and_strips_text() -> None:
    out = tools_mod._user_payload("  hello  ", 3)
    assert "Embedded images attached: 3" in out
    assert "hello" in out


def test_function_tools_are_registered() -> None:
    assert tools_mod.extract_invoice_from_pdf.name == "extract_invoice_from_pdf"
    assert (
        tools_mod.send_customer_service_notification.name
        == "send_customer_service_notification"
    )
