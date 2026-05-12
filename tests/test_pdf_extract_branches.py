"""Hit the defensive exception branches in `pdf_extract.extract_pdf_content`.

We monkey-patch parts of the pymupdf/PIL pipeline so that each `try/except`
branch fires without needing a corrupt PDF for every variant.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image

from invoice_agent import pdf_extract

REPO_ROOT = Path(__file__).resolve().parents[1]
CASE_1_PDF = REPO_ROOT / "examples" / "case_1" / "Invoice.pdf"


def _require_case1() -> Path:
    if not CASE_1_PDF.is_file():
        pytest.skip("case_1 fixture missing")
    return CASE_1_PDF


def test_page_get_text_failure_records_empty_string(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _require_case1()

    def _boom(self: Any, *args: Any, **kwargs: Any) -> str:  # type: ignore[no-untyped-def]
        raise RuntimeError("text engine down")

    monkeypatch.setattr(fitz.Page, "get_text", _boom)
    content = pdf_extract.extract_pdf_content(pdf)
    # All page texts collapsed to "".
    assert all(t == "" for t in content.page_texts)


def test_page_get_images_failure_skips_image_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pdf = _require_case1()

    def _boom(self: Any, *args: Any, **kwargs: Any) -> list:  # type: ignore[no-untyped-def]
        raise RuntimeError("image table broken")

    monkeypatch.setattr(fitz.Page, "get_images", _boom)
    content = pdf_extract.extract_pdf_content(pdf)
    assert content.images == []


def test_extract_image_failure_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = _require_case1()

    def _boom(self: Any, *args: Any, **kwargs: Any) -> dict:  # type: ignore[no-untyped-def]
        raise RuntimeError("xref unreadable")

    monkeypatch.setattr(fitz.Document, "extract_image", _boom)
    content = pdf_extract.extract_pdf_content(pdf)
    assert content.images == []


def test_extract_image_missing_bytes_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = _require_case1()

    def _empty(self: Any, *args: Any, **kwargs: Any) -> dict:  # type: ignore[no-untyped-def]
        return {"image": b""}

    monkeypatch.setattr(fitz.Document, "extract_image", _empty)
    content = pdf_extract.extract_pdf_content(pdf)
    assert content.images == []


def test_pillow_failure_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = _require_case1()

    def _boom(*args: Any, **kwargs: Any) -> Image.Image:  # type: ignore[no-untyped-def]
        raise OSError("not an image")

    monkeypatch.setattr(pdf_extract.Image, "open", _boom)
    content = pdf_extract.extract_pdf_content(pdf)
    assert content.images == []


def test_tiny_image_is_filtered_out(monkeypatch: pytest.MonkeyPatch) -> None:
    pdf = _require_case1()

    # 10x10 png — smaller than _MIN_IMAGE_SIDE.
    tiny = Image.new("RGB", (10, 10), "white")
    buf = io.BytesIO()
    tiny.save(buf, format="PNG")
    tiny_bytes = buf.getvalue()

    def _tiny(self: Any, *args: Any, **kwargs: Any) -> dict:  # type: ignore[no-untyped-def]
        return {"image": tiny_bytes}

    monkeypatch.setattr(fitz.Document, "extract_image", _tiny)
    content = pdf_extract.extract_pdf_content(pdf)
    assert content.images == []


# Import shim so the `Any` references resolve when running this file standalone.
from typing import Any  # noqa: E402
