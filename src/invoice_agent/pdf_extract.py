"""Deterministic PDF text + embedded-image extraction (no network I/O).

Includes a local OCR fallback (RapidOCR / ONNX Runtime) used when a PDF page
yields little or no extractable text — e.g. a fully scanned invoice where the
PDF is just a wrapper around a raster image. Running OCR locally keeps
expensive vision-model tokens out of the OpenAI request when a page has zero
copy-pasteable text.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass, field
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class PdfImage:
    page_index: int
    name: str
    png_bytes: bytes
    width: int
    height: int


@dataclass(frozen=True)
class PdfContent:
    text: str
    page_texts: list[str]
    images: list[PdfImage]
    # page indices where the OCR fallback was used to recover text.
    ocr_pages: list[int] = field(default_factory=list)


_MIN_IMAGE_SIDE = 60  # skip icons / decorative slivers
# A page with fewer than this many non-whitespace characters is treated as
# "no real text" and triggers the local OCR fallback. A real invoice page
# easily clears 200 chars (vendor + addresses + line items + totals);
# scan-only pages typically carry only a short placeholder string.
_OCR_MIN_PAGE_CHARS = 200
# Render PDF pages at 2x for OCR; balances speed and small-font legibility.
_OCR_RENDER_ZOOM = 2.0


def _open_pdf(pdf_path: Path) -> "fitz.Document":
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")
    try:
        return fitz.open(pdf_path)
    except Exception as exc:
        raise ValueError(f"Unreadable PDF: {pdf_path} ({exc})") from exc


def _safe_native_text(page: "fitz.Page") -> str:
    try:
        return page.get_text("text") or ""
    except Exception:
        return ""


def _merge_native_and_ocr(native_text: str, ocr_text: str) -> str:
    if not ocr_text.strip():
        return native_text
    if not native_text.strip():
        return ocr_text
    return (native_text + "\n" + ocr_text).strip()


def _extract_page_text(page: "fitz.Page", page_index: int) -> tuple[str, bool]:
    """Return ``(text, used_ocr)`` for a single page."""
    native = _safe_native_text(page)
    if len(native.strip()) >= _OCR_MIN_PAGE_CHARS:
        return native, False
    ocr_text = _ocr_page(page, page_index)
    merged = _merge_native_and_ocr(native, ocr_text)
    return merged, bool(ocr_text.strip())


def _safe_image_list(page: "fitz.Page") -> list[object]:
    try:
        return list(page.get_images(full=True))
    except Exception:
        return []


def _decode_image(doc: "fitz.Document", xref: int) -> "Image.Image | None":
    try:
        base = doc.extract_image(xref)
    except Exception:
        return None
    raw = base.get("image")
    if not raw:
        return None
    try:
        return Image.open(io.BytesIO(raw))
    except Exception:
        return None


def _to_png_bytes(pil: "Image.Image") -> bytes:
    buf = io.BytesIO()
    pil.convert("RGB").save(buf, format="PNG")
    return buf.getvalue()


def _extract_page_images(
    doc: "fitz.Document", page: "fitz.Page", page_index: int
) -> list[PdfImage]:
    out: list[PdfImage] = []
    for img_info in _safe_image_list(page):
        xref = img_info[0]
        pil = _decode_image(doc, xref)
        if pil is None:
            continue
        if pil.width < _MIN_IMAGE_SIDE or pil.height < _MIN_IMAGE_SIDE:
            continue
        out.append(
            PdfImage(
                page_index=page_index,
                name=f"page{page_index}_xref{xref}",
                png_bytes=_to_png_bytes(pil),
                width=pil.width,
                height=pil.height,
            )
        )
    return out


def extract_pdf_content(pdf_path: Path) -> PdfContent:
    """Extract text and embedded images from a PDF.

    Raises:
        FileNotFoundError: pdf_path missing.
        ValueError: PDF cannot be parsed.
    """
    doc = _open_pdf(pdf_path)
    page_texts: list[str] = []
    images: list[PdfImage] = []
    ocr_pages: list[int] = []
    try:
        for page_index, page in enumerate(doc):
            text, used_ocr = _extract_page_text(page, page_index)
            page_texts.append(text)
            if used_ocr:
                ocr_pages.append(page_index)
            images.extend(_extract_page_images(doc, page, page_index))
    finally:
        doc.close()

    return PdfContent(
        text="\n\n".join(page_texts),
        page_texts=page_texts,
        images=images,
        ocr_pages=ocr_pages,
    )


# --- Local OCR fallback (RapidOCR / ONNX Runtime) ---------------------------
#
# RapidOCR is a small, pure-pip OCR stack (no system Tesseract binary, no
# PyTorch). It loads PP-OCR ONNX models on first use. We import lazily so
# that test runs which never hit the fallback do not pay the import cost,
# and so the absence of the dependency degrades gracefully (we just return
# whatever native text the PDF gave us, which is the previous behavior).

_ocr_engine_singleton: object | None = None
_ocr_engine_unavailable: bool = False


def _get_ocr_engine() -> object | None:
    global _ocr_engine_singleton, _ocr_engine_unavailable
    if _ocr_engine_singleton is not None:
        return _ocr_engine_singleton
    if _ocr_engine_unavailable:
        return None
    try:
        from rapidocr_onnxruntime import RapidOCR  # type: ignore[import-not-found]
    except ImportError as exc:
        _ocr_engine_unavailable = True
        log.warning(
            "OCR fallback unavailable: rapidocr-onnxruntime not importable (%s). "
            "Scanned-only PDFs will fall back to native text only.",
            exc,
        )
        return None
    try:
        _ocr_engine_singleton = RapidOCR()
    except Exception as exc:
        _ocr_engine_unavailable = True
        log.warning("OCR fallback init FAILED: %s", exc)
        return None
    log.info("OCR fallback engine ready: rapidocr-onnxruntime")
    return _ocr_engine_singleton


def _ocr_page(page: "fitz.Page", page_index: int) -> str:
    """Rasterize a PDF page and OCR it locally. Returns '' on any failure.

    Failures are logged (not silent) but never raised — OCR is a best-effort
    fallback; the upstream vision-LLM call is still authoritative.
    """
    engine = _get_ocr_engine()
    if engine is None:
        return ""
    try:
        matrix = fitz.Matrix(_OCR_RENDER_ZOOM, _OCR_RENDER_ZOOM)
        pix = page.get_pixmap(matrix=matrix, alpha=False)
        png_bytes = pix.tobytes("png")
    except Exception as exc:
        log.warning("OCR fallback: page %d render failed: %s", page_index, exc)
        return ""
    try:
        # RapidOCR accepts raw bytes; returns (results, elapsed) where
        # results is list[ [box, text, score] ] or None.
        # One bounded retry: transient ONNX-runtime hiccups (kernel/threadpool
        # contention on cold start) shouldn't kill OCR — but if the second
        # attempt also fails, degrade gracefully (no exception bubbles).
        from invoice_agent._retry import retry_call

        result, _elapsed = retry_call(
            lambda: engine(png_bytes),  # type: ignore[operator]
            label="ocr",
            attempts=2,
            base_delay=0.1,
        )
    except Exception as exc:
        log.warning("OCR fallback: page %d OCR failed after retries: %s", page_index, exc)
        return ""
    if not result:
        log.info("OCR fallback: page %d produced no text", page_index)
        return ""
    lines = [str(item[1]).strip() for item in result if len(item) >= 2 and item[1]]
    text = "\n".join(line for line in lines if line)
    log.info(
        "OCR fallback: page %d recovered %d lines / %d chars",
        page_index,
        len(lines),
        len(text),
    )
    return text
