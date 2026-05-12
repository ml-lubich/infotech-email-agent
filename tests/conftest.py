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
