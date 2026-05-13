"""OCR fallback tests for `pdf_extract`.

We synthesize a PDF whose only content is a rasterized image of text — the
native `page.get_text()` path returns nothing — and verify the local OCR
fallback (RapidOCR / ONNX Runtime) recovers a recognizable substring.

These tests exercise REAL filesystem writes and the REAL OCR engine
(no mocks) per the project's testing rules. They are skipped if RapidOCR is
unavailable in the environment.
"""

from __future__ import annotations

import io
from pathlib import Path

import fitz  # PyMuPDF
import pytest
from PIL import Image, ImageDraw, ImageFont

from invoice_agent import pdf_extract


def _ocr_available() -> bool:
    try:
        import rapidocr_onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


pytestmark = pytest.mark.skipif(
    not _ocr_available(),
    reason="rapidocr-onnxruntime not installed in this environment",
)


def _scanned_pdf_with_text(text: str, out_path: Path) -> None:
    """Build a 1-page PDF whose content is ONLY a raster image of `text`."""
    img = Image.new("RGB", (1200, 400), "white")
    draw = ImageDraw.Draw(img)
    # Use a default PIL font scaled up — works without system fonts.
    try:
        font = ImageFont.truetype("Helvetica", 48)
    except OSError:
        font = ImageFont.load_default()
    draw.text((40, 80), text, fill=(0, 0, 0), font=font)
    buf = io.BytesIO()
    img.save(buf, format="PNG")

    doc = fitz.open()
    page = doc.new_page(width=612, height=300)
    page.insert_image(fitz.Rect(20, 20, 592, 280), stream=buf.getvalue())
    doc.save(out_path)
    doc.close()


def test_ocr_fallback_recovers_text_from_scanned_pdf(tmp_path: Path) -> None:
    pdf = tmp_path / "scanned.pdf"
    _scanned_pdf_with_text("Invoice INV-9001 Total 1234", pdf)

    # Sanity: native text path is essentially empty.
    raw = fitz.open(pdf)
    native = raw[0].get_text("text") or ""
    raw.close()
    assert len(native.strip()) < 20, f"expected near-empty native text, got {native!r}"

    content = pdf_extract.extract_pdf_content(pdf)

    assert content.ocr_pages == [0], (
        f"OCR fallback should fire on page 0; got {content.ocr_pages}"
    )
    # OCR is imperfect on synthesized fonts; check for a stable substring.
    assert "INV" in content.text.upper(), (
        f"OCR text should contain 'INV'; got: {content.text!r}"
    )


def test_ocr_disabled_when_engine_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pdf = tmp_path / "scanned.pdf"
    _scanned_pdf_with_text("Invoice INV-9002", pdf)
    monkeypatch.setattr(pdf_extract, "_get_ocr_engine", lambda: None)

    content = pdf_extract.extract_pdf_content(pdf)
    assert content.ocr_pages == []
    # Without OCR, the page text remains essentially empty.
    assert len(content.text.strip()) < 20


# --- Real fixture: case_11_scanned_full_page ---------------------------------
#
# This repo ships a scan-only invoice (the whole page is a rasterized image,
# the PDF text layer is just a placeholder). Pin the behavior that the OCR
# fallback both fires AND recovers content the downstream agent depends on
# (vendor name, invoice number, currency).

REPO_ROOT = Path(__file__).resolve().parents[1]
SCANNED_FIXTURE = REPO_ROOT / "examples" / "case_11_scanned_full_page" / "Invoice.pdf"


def test_ocr_fallback_on_real_scanned_fixture() -> None:
    if not SCANNED_FIXTURE.is_file():
        pytest.skip("case_11_scanned_full_page fixture missing")

    # Sanity: native text layer is essentially a placeholder.
    raw = fitz.open(SCANNED_FIXTURE)
    native = raw[0].get_text("text") or ""
    raw.close()
    assert len(native.strip()) < 200, (
        f"fixture should be scan-only; native text was {len(native)} chars"
    )

    content = pdf_extract.extract_pdf_content(SCANNED_FIXTURE)

    assert content.ocr_pages == [0], (
        f"OCR fallback must fire on scan-only fixture; got {content.ocr_pages}"
    )
    upper = content.text.upper()
    # Vendor + invoice number + currency must come out of OCR (allow OCR
    # noise — check stable substrings only).
    assert "CASCADIA" in upper, f"vendor lost; got: {content.text!r}"
    assert "CLI-2026-LAB-0512" in upper.replace(" ", ""), (
        f"invoice number lost; got: {content.text!r}"
    )
    assert "USD" in upper, f"currency lost; got: {content.text!r}"
