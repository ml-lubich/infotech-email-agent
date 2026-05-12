"""Deterministic PDF extraction tests (no network, no model calls)."""

from __future__ import annotations

from pathlib import Path

import pytest

from invoice_agent.pdf_extract import PdfContent, extract_pdf_content

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_1_PDF = REPO_ROOT / "examples" / "case_1" / "Invoice.pdf"


def test_extract_pdf_content_on_real_sample() -> None:
    assert CASE_1_PDF.is_file(), "case_1 sample PDF is part of the repo fixtures"
    content = extract_pdf_content(CASE_1_PDF)
    assert isinstance(content, PdfContent)
    assert len(content.page_texts) >= 1
    assert content.text  # non-empty concatenation
    # case_1 has at least the logo/banner image stamped on the invoice.
    assert len(content.images) >= 1
    img = content.images[0]
    assert img.png_bytes.startswith(b"\x89PNG"), "images are normalized to PNG"
    assert img.width >= 60 and img.height >= 60  # _MIN_IMAGE_SIDE filter


def test_extract_pdf_content_missing_file_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_pdf_content(tmp_path / "nope.pdf")


def test_extract_pdf_content_unreadable_pdf_raises(tmp_path: Path) -> None:
    bogus = tmp_path / "not_a.pdf"
    bogus.write_bytes(b"this is not a pdf")
    with pytest.raises(ValueError, match="Unreadable PDF"):
        extract_pdf_content(bogus)
