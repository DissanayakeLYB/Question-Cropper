"""Trim, pad, join and save the final question images."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from . import DPI, JOIN_GAP, PADDING
from .detect import Segment
from .edges import PageInk, trim_to_ink
from .models import Box, PageInfo
from .page import open_pdf, render_page


@dataclass
class SaveResult:
    output_dir: Path
    files: list[Path]
    manifest_path: Path


def trim_segment(
    page_image: Image.Image,
    segment: Segment,
    page: PageInfo,
    padding: int = PADDING,
    ink: PageInk | None = None,
) -> Box | None:
    """Trim a raw segment to its ink, keeping every edge on a blank row."""
    profile = ink if ink is not None else PageInk(page_image)
    return trim_to_ink(profile, segment, page, padding)


def join_crops(crops: list[Image.Image], gap: int = JOIN_GAP) -> Image.Image:
    """Stack crops vertically with a white gap between page pieces."""
    if not crops:
        raise ValueError("No crops to join.")
    if len(crops) == 1:
        return crops[0].convert("RGB")

    width = max(crop.width for crop in crops)
    height = sum(crop.height for crop in crops) + gap * (len(crops) - 1)
    canvas = Image.new("RGB", (width, height), "white")
    offset = 0
    for crop in crops:
        canvas.paste(crop.convert("RGB"), (0, offset))
        offset += crop.height + gap
    return canvas


def question_filename(stem: str, question: int) -> str:
    return f"{stem}_q{question:02d}.png"


def _reading_order(box: Box, page_width: int) -> tuple[int, int, int, int]:
    column = 0 if (box.x0 + box.x1) / 2 < page_width / 2 else 1
    return (box.page, column, box.y0, box.x0)


def _page_size(page, dpi: int) -> tuple[int, int]:
    zoom = dpi / 72.0
    return (round(page.rect.width * zoom), round(page.rect.height * zoom))


def save_questions(
    pdf_path: Path,
    boxes: list[Box],
    output_root: Path = Path("output"),
    dpi: int = DPI,
) -> SaveResult:
    """Write one PNG per question and a manifest.json for the PDF."""
    if not boxes:
        raise ValueError("No boxes to save.")

    doc = open_pdf(pdf_path)
    output_dir = output_root / pdf_path.stem
    output_dir.mkdir(parents=True, exist_ok=True)

    try:
        sizes = [_page_size(page, dpi) for page in doc]
        for box in boxes:
            if not 1 <= box.page <= len(sizes):
                raise ValueError(f"Question {box.question}: page {box.page} does not exist.")
            width, height = sizes[box.page - 1]
            if box.x0 < 0 or box.y0 < 0 or box.x1 > width or box.y1 > height:
                raise ValueError(
                    f"Question {box.question}: box {box.x0},{box.y0},{box.x1},{box.y1} "
                    f"is outside page {box.page} ({width}x{height})."
                )
            if box.x1 - box.x0 <= 1 or box.y1 - box.y0 <= 1:
                raise ValueError(f"Question {box.question}: box on page {box.page} is too small.")

        _remove_old_outputs(output_dir, pdf_path.stem)

        cache: dict[int, Image.Image] = {}

        def page_image(page_index: int) -> Image.Image:
            image = cache.pop(page_index, None)
            if image is None:
                image = render_page(doc[page_index], dpi)
            cache[page_index] = image
            while len(cache) > 3:
                oldest = next(iter(cache))
                if oldest != page_index:
                    cache.pop(oldest)
                else:
                    break
            return image

        grouped: dict[int, list[Box]] = {}
        for box in boxes:
            grouped.setdefault(box.question, []).append(box)

        files: list[Path] = []
        manifest_questions: list[dict] = []
        for question in sorted(grouped):
            ordered = sorted(grouped[question], key=lambda box: _reading_order(box, sizes[box.page - 1][0]))
            crops = [
                page_image(box.page - 1).crop((box.x0, box.y0, box.x1, box.y1)) for box in ordered
            ]
            image = join_crops(crops)
            filename = question_filename(pdf_path.stem, question)
            destination = output_dir / filename
            image.save(destination, "PNG")
            files.append(destination)

            segments = [{"page": box.page, "box": [box.x0, box.y0, box.x1, box.y1]} for box in ordered]
            manifest_questions.append(
                {
                    "question": question,
                    "file": filename,
                    "page": segments[0]["page"],
                    "box": segments[0]["box"],
                    "segments": segments,
                }
            )

        manifest_path = output_dir / "manifest.json"
        manifest = {
            "pdf": pdf_path.name,
            "dpi": dpi,
            "questions": manifest_questions,
        }
        manifest_path.write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    finally:
        doc.close()

    return SaveResult(output_dir=output_dir, files=files, manifest_path=manifest_path)


def _remove_old_outputs(output_dir: Path, stem: str) -> None:
    prefix = f"{stem}_q"
    for entry in output_dir.iterdir():
        if entry.is_file() and entry.name.startswith(prefix) and entry.suffix.lower() == ".png":
            entry.unlink()
