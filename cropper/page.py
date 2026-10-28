"""Rendering pages and extracting text (from a text layer or Tesseract)."""

from __future__ import annotations

from pathlib import Path

import pymupdf
import pytesseract
from PIL import Image

from .models import TextLine

TEXT_LAYER_MIN_CHARS = 40


class CropperError(RuntimeError):
    """A user-facing error."""


def open_pdf(path: Path) -> pymupdf.Document:
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # PyMuPDF raises several exception types
        raise CropperError(f"Could not open PDF {path.name}: {exc}") from exc
    if doc.needs_pass:
        doc.close()
        raise CropperError(f"{path.name} is password protected; remove the password first.")
    if doc.page_count == 0:
        doc.close()
        raise CropperError(f"{path.name} has no pages.")
    return doc


def render_page(page: pymupdf.Page, dpi: int) -> Image.Image:
    """Render one page as an RGB Pillow image at the requested DPI."""
    zoom = dpi / 72.0
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def has_text_layer(page: pymupdf.Page) -> bool:
    """True when the page has enough real text to trust instead of OCR."""
    text = page.get_text("text").strip()
    return len(text) >= TEXT_LAYER_MIN_CHARS


def text_lines(page: pymupdf.Page, width: int, height: int) -> list[TextLine]:
    """Extract text lines with coordinates scaled to the rendered image."""
    rect = page.rect
    if rect.width <= 0 or rect.height <= 0:
        return []
    sx = width / rect.width
    sy = height / rect.height

    lines: list[TextLine] = []
    data = page.get_text("dict")
    for block in data.get("blocks", []):
        if block.get("type") != 0:
            continue
        for line in block.get("lines", []):
            text = "".join(span.get("text", "") for span in line.get("spans", [])).strip()
            if not text:
                continue
            x0, y0, x1, y1 = line["bbox"]
            lines.append(
                TextLine(
                    text=text,
                    x0=x0 * sx,
                    y0=y0 * sy,
                    x1=x1 * sx,
                    y1=y1 * sy,
                )
            )
    return lines


def ocr_lines(image: Image.Image) -> list[TextLine]:
    """OCR a page with Tesseract and return line boxes in image pixels."""
    try:
        data = pytesseract.image_to_data(
            image.convert("L"),
            output_type=pytesseract.Output.DICT,
            config="--psm 3",
        )
    except pytesseract.TesseractNotFoundError as exc:
        raise CropperError(
            "This PDF has no text layer, so Tesseract OCR is required. "
            "Install Tesseract and make sure the 'tesseract' command is on PATH."
        ) from exc
    except Exception as exc:
        raise CropperError(f"Tesseract OCR failed: {exc}") from exc

    groups: dict[tuple[int, int, int], dict] = {}
    for i, raw_text in enumerate(data.get("text", [])):
        word = raw_text.strip()
        if not word:
            continue
        try:
            confidence = float(data["conf"][i])
        except (KeyError, TypeError, ValueError):
            continue
        if confidence < 30:
            continue

        key = (int(data["block_num"][i]), int(data["par_num"][i]), int(data["line_num"][i]))
        left = float(data["left"][i])
        top = float(data["top"][i])
        right = left + float(data["width"][i])
        bottom = top + float(data["height"][i])

        group = groups.get(key)
        if group is None:
            groups[key] = {
                "words": [word],
                "x0": left,
                "y0": top,
                "x1": right,
                "y1": bottom,
            }
        else:
            group["words"].append(word)
            group["x0"] = min(group["x0"], left)
            group["y0"] = min(group["y0"], top)
            group["x1"] = max(group["x1"], right)
            group["y1"] = max(group["y1"], bottom)

    lines = [
        TextLine(
            text=" ".join(group["words"]).strip(),
            x0=group["x0"],
            y0=group["y0"],
            x1=group["x1"],
            y1=group["y1"],
        )
        for group in groups.values()
    ]
    lines.sort(key=lambda line: (line.y0, line.x0))
    return lines


def make_thumbnail(image: Image.Image, target_width: int = 480) -> tuple[Image.Image, float]:
    """Return a grayscale thumbnail and the scale from full page to thumbnail."""
    gray = image.convert("L")
    width, height = gray.size
    if width <= target_width:
        return gray, 1.0
    new_height = max(1, round(height * target_width / width))
    thumb = gray.resize((target_width, new_height), Image.Resampling.BILINEAR)
    return thumb, target_width / width


def save_preview(image: Image.Image, path: Path, quality: int = 80) -> None:
    """Save a JPEG used by the review page."""
    path.parent.mkdir(parents=True, exist_ok=True)
    image.convert("RGB").save(path, "JPEG", quality=quality, optimize=False)


def ink_threshold(image: Image.Image) -> int:
    """Pick a foreground threshold adapted to the page background."""
    histogram = image.convert("L").histogram()
    peak = max(range(180, 256), key=lambda value: histogram[value])
    return max(200, peak - 15)


def ink_bbox(image: Image.Image) -> tuple[int, int, int, int] | None:
    """Bounding box of non-background pixels, or None for a blank image."""
    gray = image.convert("L")
    threshold = ink_threshold(gray)
    mask = gray.point(lambda value: 255 if value < threshold else 0)
    return mask.getbbox()

