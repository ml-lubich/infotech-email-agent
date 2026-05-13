"""Shared fixtures: repo paths + isolated working dir for filesystem side-effects."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
EXAMPLES = REPO_ROOT / "examples"


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def examples_dir() -> Path:
    return EXAMPLES


@pytest.fixture
def case1_pdf() -> Path:
    p = EXAMPLES / "case_1" / "Invoice.pdf"
    if not p.is_file():
        pytest.skip(f"Fixture PDF missing: {p}")
    return p


@pytest.fixture
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip env vars that would bleed across tests."""
    for var in ("INVOICE_OUT_DIR", "INVOICE_AGENT_MODEL", "INVOICE_EXTRACT_MODEL"):
        monkeypatch.delenv(var, raising=False)
    # Keep OPENAI_API_KEY untouched unless a specific test removes it.
    os.environ.setdefault("OPENAI_API_KEY", "test-key-not-used")


@pytest.fixture(autouse=True)
def _no_real_sleep_in_retries(monkeypatch: pytest.MonkeyPatch) -> None:
    """Speed up the bounded-retry helper for every test.

    The retry helper sleeps with exponential back-off in production
    (`invoice_agent._retry.retry_call`). Tests assert behavior, not
    wall-clock backoff, so collapse the sleep to a no-op. Tests that
    explicitly want to observe the sleep schedule inject their own
    `sleep` callable via `retry_call(..., sleep=...)` and are unaffected.
    """
    import invoice_agent._retry as _retry

    monkeypatch.setattr(_retry.time, "sleep", lambda _s: None)
