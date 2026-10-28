"""End-to-end analysis of one PDF."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from . import DPI
from .crop import trim_segment
from .detect import build_segments, detect_columns, detect_margins, find_question_starts
from .models import Box, PageInfo, PaperAnalysis
from .page import (
    has_text_layer,
    make_thumbnail,
    ocr_lines,
    open_pdf,
    render_page,
    save_preview,
    text_lines,
)


def analyze_pdf(
    path: Path,
    dpi: int = DPI,
    preview_dir: Path | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> PaperAnalysis:
    """Read a PDF and return the detected question boxes.

    Rendering happens in two passes: the first pass collects text lines and
    thumbnails for detection, the second renders only the pages that need
    trimming to the exact ink bounds.
    """
    doc = open_pdf(path)
    try:
        pages: list[PageInfo] = []
        for index in range(doc.page_count):
            page = doc[index]
            image = render_page(page, dpi)
            if preview_dir is not None:
                save_preview(image, preview_dir / f"{index + 1:03d}.jpg")
            if has_text_layer(page):
                lines = text_lines(page, image.width, image.height)
            else:
                lines = ocr_lines(image)
            thumb, scale = make_thumbnail(image)
            pages.append(
                PageInfo(
                    number=index + 1,
                    width=image.width,
                    height=image.height,
                    lines=lines,
                    thumb=thumb,
                    thumb_scale=scale,
                )
            )
            if progress is not None:
                progress(index + 1, doc.page_count)

        detect_margins(pages)
        for page in pages:
            detect_columns(page)
        starts = find_question_starts(pages)
        segments = build_segments(starts, pages)

        segments_by_page: dict[int, list] = {}
        for segment in segments:
            segments_by_page.setdefault(segment.page, []).append(segment)

        boxes: list[Box] = []
        for page_number in sorted(segments_by_page):
            page = pages[page_number - 1]
            image = render_page(doc[page_number - 1], dpi)
            for segment in segments_by_page[page_number]:
                box = trim_segment(image, segment, page, padding=10)
                if box is not None:
                    boxes.append(box)

        boxes.sort(key=lambda box: (box.question, box.page, box.y0, box.x0))
        return PaperAnalysis(path=path, dpi=dpi, pages=pages, boxes=boxes)
    finally:
        doc.close()
