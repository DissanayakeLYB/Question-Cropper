"""Recover question starts that the first pass missed, using numbering order.

Detection works on the whole page at 200 DPI. When a number is missing from the
1..N sequence, this module looks only at the vertical band between its
neighbours, and tries progressively more forgiving signals there:

1. an already-extracted line that starts with the number,
2. a 300 DPI OCR pass on the left margin with a lower confidence threshold,
3. the widest blank run between text blocks, treated as a question boundary.
"""

from __future__ import annotations

import re

import pymupdf
from PIL import Image

from .detect import QuestionStart, find_question_gaps
from .models import PageInfo
from .page import ink_threshold, ocr_lines

RESCUE_DPI = 300
RESCUE_MIN_CONFIDENCE = 10
STRIP_LEFT_FRACTION = 0.06
STRIP_RIGHT_FRACTION = 0.10
MIN_BLANK_FRACTION = 0.008


def _number_pattern(number: int) -> re.Pattern[str]:
    """Match '12.' at the start of a line, tolerating scanner edge noise."""
    return re.compile(rf"^[\s|\[\]lI]{{0,3}}{number}\s*[.,)]")


def _column_index(page: PageInfo, x: float) -> int:
    if len(page.columns) <= 1:
        return 0
    return int(x >= page.columns[0].x1)


def _ordered(starts: list[QuestionStart]) -> list[QuestionStart]:
    return sorted(starts, key=lambda start: (start.page, start.column, start.y0))


def rescue_missing_starts(
    doc: pymupdf.Document, pages: list[PageInfo], starts: list[QuestionStart], expected: int
) -> list[QuestionStart]:
    """Fill numbering gaps by searching between each pair of neighbours."""
    gaps = find_question_gaps(starts, expected)
    if not gaps:
        return starts

    rescued = list(starts)
    for number in gaps:
        start = _rescue_one(doc, pages, rescued, number)
        if start is not None:
            rescued.append(start)
    return _ordered(rescued)


def _rescue_one(
    doc: pymupdf.Document, pages: list[PageInfo], starts: list[QuestionStart], number: int
) -> QuestionStart | None:
    ordered = _ordered(starts)
    previous = None
    following = None
    for start in ordered:
        if start.question < number:
            previous = start
        elif start.question > number and following is None:
            following = start
    # Only interior gaps are recoverable: without both neighbours there is no
    # vertical window to search, and a wrong expected count must not invent
    # trailing questions.
    if previous is None or following is None:
        return None

    bands = _search_bands(pages, previous, following)
    for page_number, y0, y1 in bands:
        page = pages[page_number - 1]
        start = _from_existing_lines(page, number, y0, y1)
        if start is not None:
            return start
    if doc is not None:
        for page_number, y0, y1 in bands:
            page = pages[page_number - 1]
            start = _from_rescue_ocr(doc, page, number, y0, y1)
            if start is not None:
                return start
    for page_number, y0, y1 in bands:
        page = pages[page_number - 1]
        start = _from_blank_gap(page, number, y0, y1)
        if start is not None:
            return start
    return None


def _search_bands(
    pages: list[PageInfo], previous: QuestionStart | None, following: QuestionStart | None
) -> list[tuple[int, float, float]]:
    """Page bands strictly between the neighbouring question starts."""
    last_page = len(pages)
    start_page = previous.page if previous is not None else (following.page if following else 1)
    end_page = following.page if following is not None else last_page
    start_page = max(1, min(start_page, last_page))
    end_page = max(start_page, min(end_page, last_page))

    bands: list[tuple[int, float, float]] = []
    for page_number in range(start_page, end_page + 1):
        page = pages[page_number - 1]
        top = page.content_top
        bottom = page.content_bottom
        if previous is not None and page_number == previous.page:
            top = max(top, previous.y0 + 1.0)
        if following is not None and page_number == following.page:
            bottom = min(bottom, following.y0 - 1.0)
        if bottom - top > 1.0:
            bands.append((page_number, top, bottom))
    return bands


def _from_existing_lines(
    page: PageInfo, number: int, y0: float, y1: float
) -> QuestionStart | None:
    pattern = _number_pattern(number)
    for line in page.lines:
        if not y0 <= line.y0 <= y1:
            continue
        if pattern.match(line.text):
            return QuestionStart(number, page.number, _column_index(page, line.x0), line.y0)
    return None


def _from_rescue_ocr(
    doc: pymupdf.Document, page: PageInfo, number: int, y0: float, y1: float
) -> QuestionStart | None:
    rendered = _render_strip(doc, page, y0, y1)
    if rendered is None:
        return None
    image, left, top, scale = rendered
    pattern = _number_pattern(number)
    for psm in (6, 7):
        lines = ocr_lines(image, psms=(psm,), min_confidence=RESCUE_MIN_CONFIDENCE)
        for line in lines:
            match = pattern.match(line.text)
            if match is None:
                continue
            x = left + line.x0 / scale
            y = top + line.y0 / scale
            return QuestionStart(number, page.number, _column_index(page, x), y)
    return None


def _render_strip(
    doc: pymupdf.Document, page: PageInfo, y0: float, y1: float
) -> tuple[Image.Image, float, float, float] | None:
    """Render the left margin of a band at 300 DPI for a second OCR attempt."""
    if not page.columns or y1 - y0 < 4:
        return None
    column = page.columns[0]
    left = max(0.0, column.left_margin - STRIP_LEFT_FRACTION * page.width)
    right = min(page.width, column.left_margin + STRIP_RIGHT_FRACTION * page.width)
    if right - left < 8:
        return None

    to_points = 72.0 / 200.0
    scale = RESCUE_DPI / 200.0
    clip = pymupdf.Rect(left * to_points, y0 * to_points, right * to_points, y1 * to_points)
    zoom = RESCUE_DPI / 72.0
    pix = doc[page.number - 1].get_pixmap(
        matrix=pymupdf.Matrix(zoom, zoom), clip=clip, colorspace=pymupdf.csRGB, alpha=False
    )
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return image, left, y0, scale


def _from_blank_gap(
    page: PageInfo, number: int, y0: float, y1: float
) -> QuestionStart | None:
    """Treat the widest blank run in the band as the boundary before the question."""
    thumb = page.thumb
    scale = page.thumb_scale
    if scale <= 0:
        return None
    width, height = thumb.size
    row_start = max(0, int(y0 * scale))
    row_end = min(height, int(y1 * scale))
    if row_end - row_start < 6:
        return None

    threshold = ink_threshold(thumb)
    data = thumb.tobytes()
    blank: list[list[int]] = []
    run_start = None
    for y in range(row_start, row_end):
        row = data[y * width : (y + 1) * width]
        has_ink = min(row) < threshold
        if has_ink:
            if run_start is not None:
                blank.append([run_start, y])
                run_start = None
        elif run_start is None:
            run_start = y
    if run_start is not None:
        blank.append([run_start, row_end])

    min_length = max(4, int(MIN_BLANK_FRACTION * height))
    wide = [run for run in blank if run[1] - run[0] >= min_length]
    if not wide:
        return None
    widest = max(wide, key=lambda run: run[1] - run[0])
    boundary = widest[1] / scale
    column = page.columns[0] if page.columns else None
    x = column.left_margin if column is not None else 0.0
    return QuestionStart(number, page.number, _column_index(page, x), boundary)
