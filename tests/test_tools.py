"""Notification tool: real file-system writes, no OpenAI calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from invoice_agent.tools import (
    send_customer_service_notification,
    write_notification_files,
)


def test_write_notification_files_writes_txt_and_json(tmp_path: Path) -> None:
    summary = "## Vendor\nAcme Co\n\n## Invoice\nINV-001\n"
    payload = {"vendor_name": "Acme Co", "invoice_number": "INV-001"}
    txt_path, json_path = write_notification_files(
        summary, json.dumps(payload), tmp_path
    )

    assert txt_path == tmp_path / "outbound_email.txt"
    assert json_path == tmp_path / "outbound_email.json"
    assert txt_path.read_text(encoding="utf-8") == summary

    written = json.loads(json_path.read_text(encoding="utf-8"))
    assert written == payload
    # pretty-printed (2-space indent contract for downstream readers)
    assert "  " in json_path.read_text(encoding="utf-8")


def test_write_notification_files_rejects_invalid_json(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="not valid JSON"):
        write_notification_files("summary", "{not-json", tmp_path)


def test_write_notification_files_creates_missing_dir(tmp_path: Path) -> None:
    nested = tmp_path / "deep" / "out"
    assert not nested.exists()
    write_notification_files("hi", "{}", nested)
    assert (nested / "outbound_email.txt").is_file()
    assert (nested / "outbound_email.json").is_file()


def test_notification_tool_is_registered_as_function_tool() -> None:
    # The decorated symbol must remain a FunctionTool the Agent SDK can wire up.
    assert send_customer_service_notification.name == "send_customer_service_notification"
    assert callable(send_customer_service_notification.on_invoke_tool)
