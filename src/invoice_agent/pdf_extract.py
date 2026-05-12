"""Deterministic PDF text + embedded-image extraction (no network I/O)."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image


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


_MIN_IMAGE_SIDE = 60  # skip icons / decorative slivers


def extract_pdf_content(pdf_path: Path) -> PdfContent:
    """Extract text and embedded images from a PDF.

    Raises:
        FileNotFoundError: pdf_path missing.
        ValueError: PDF cannot be parsed.
    """
    if not pdf_path.is_file():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    try:
        doc = fitz.open(pdf_path)
    except Exception as exc:
        raise ValueError(f"Unreadable PDF: {pdf_path} ({exc})") from exc

    page_texts: list[str] = []
    images: list[PdfImage] = []

    try:
        for page_index, page in enumerate(doc):
            try:
                page_texts.append(page.get_text("text") or "")
            except Exception:
                page_texts.append("")

            try:
                img_list = page.get_images(full=True)
            except Exception:
                img_list = []

            for img_info in img_list:
                xref = img_info[0]
                try:
                    base = doc.extract_image(xref)
                except Exception:
                    continue
                raw = base.get("image")
                if not raw:
                    continue
                try:
                    pil = Image.open(io.BytesIO(raw))
                except Exception:
                    continue
                if pil.width < _MIN_IMAGE_SIDE or pil.height < _MIN_IMAGE_SIDE:
                    continue
                buf = io.BytesIO()
                pil.convert("RGB").save(buf, format="PNG")
                images.append(
                    PdfImage(
                        page_index=page_index,
                        name=f"page{page_index}_xref{xref}",
                        png_bytes=buf.getvalue(),
                        width=pil.width,
                        height=pil.height,
                    )
                )
    finally:
        doc.close()

    return PdfContent(
        text="\n\n".join(page_texts),
        page_texts=page_texts,
        images=images,
    )
