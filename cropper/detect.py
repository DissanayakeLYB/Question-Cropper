"""Detect page margins, columns, question starts, and question segments."""

from __future__ import annotations

import math
import re
from collections import defaultdict
from dataclasses import dataclass

from .models import Column, PageInfo
from .page import ink_threshold

# "1.", "2)" and similar markers at the left edge of a column.
# A comma is tolerated because OCR sometimes reads the full stop as one.
QUESTION_RE = re.compile(r"^\s*(\d{1,2})\s*[.,)](?!\d)")

_PAGE_NUMBER_RES = (
    re.compile(r"^(?:page\s*)?\d{1,4}(?:\s*(?:of|/)\s*\d{1,4})?$", re.IGNORECASE),
    re.compile(r"^[-–—\s]*\d{1,4}[-–—\s]*$"),
    re.compile(r"^[ivxlcdm]{1,7}$", re.IGNORECASE),
)

HEADER_ZONE = 0.16
FOOTER_ZONE = 0.84
COLUMN_INK_LIMIT = 0.004
COLUMN_MIN_WIDTH = 0.015
COLUMN_SEARCH = (0.20, 0.80)


@dataclass
class QuestionStart:
    question: int
    page: int  # 1-based
    column: int
    y0: float


@dataclass
class Segment:
    question: int
    page: int  # 1-based
    column: int
    x0: float
    y0: float
    x1: float
    y1: float


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"\d+", "#", text.lower())).strip()


def _is_page_number(text: str) -> bool:
    stripped = text.strip()
    if not stripped or len(stripped) > 24:
        return False
    return any(pattern.match(stripped) for pattern in _PAGE_NUMBER_RES)


def detect_margins(pages: list[PageInfo]) -> None:
    """Set content_top/content_bottom, removing headers, footers and page numbers."""
    page_count = len(pages)
    repeat_min = max(2, math.ceil(page_count * 0.4)) if page_count > 1 else page_count + 1

    header_pages: dict[str, set[int]] = defaultdict(set)
    footer_pages: dict[str, set[int]] = defaultdict(set)
    for page in pages:
        header_limit = page.height * HEADER_ZONE
        footer_limit = page.height * FOOTER_ZONE
        for line in page.lines:
            key = _normalise(line.text)
            if not key:
                continue
            if line.y1 <= header_limit:
                header_pages[key].add(page.number)
            if line.y0 >= footer_limit:
                footer_pages[key].add(page.number)

    for page in pages:
        margin = max(4.0, page.height * 0.006)
        header_limit = page.height * HEADER_ZONE
        footer_limit = page.height * FOOTER_ZONE

        header_bottoms = [
            line.y1
            for line in page.lines
            if line.y1 <= header_limit
            and (
                _is_page_number(line.text)
                or len(header_pages.get(_normalise(line.text), ())) >= repeat_min
                or (
                    page_count == 1
                    and line.y1 <= page.height * 0.07
                    and not QUESTION_RE.match(line.text)
                )
            )
        ]
        if header_bottoms:
            page.content_top = min(max(header_bottoms) + margin, header_limit)
        else:
            page.content_top = 0.0

        footer_tops = [
            line.y0
            for line in page.lines
            if line.y0 >= footer_limit
            and (
                _is_page_number(line.text)
                or len(footer_pages.get(_normalise(line.text), ())) >= repeat_min
            )
        ]
        if footer_tops:
            page.content_bottom = max(min(footer_tops) - margin, footer_limit)
        else:
            page.content_bottom = float(page.height)
            band = _isolated_bottom_band(page.thumb, page.thumb_scale)
            if band is not None:
                page.content_bottom = max(band - margin, page.height * 0.85)


def _isolated_bottom_band(thumb, scale: float) -> float | None:
    """Find a small isolated ink band at the very bottom of the page (a page number)."""
    width, height = thumb.size
    if width < 10 or height < 10:
        return None
    threshold = ink_threshold(thumb)
    mask = thumb.point(lambda value: 255 if value < threshold else 0)
    data = mask.tobytes()
    ink_rows = [y for y in range(height) if data[y * width : (y + 1) * width].count(255) >= 2]
    if not ink_rows:
        return None

    runs: list[list[int]] = [[ink_rows[0]]]
    for y in ink_rows[1:]:
        if y - runs[-1][-1] <= 1:
            runs[-1].append(y)
        else:
            runs.append([y])

    last = runs[-1]
    previous_end = runs[-2][-1] if len(runs) > 1 else None
    y0, y1 = last[0], last[-1]
    if y0 < 0.93 * height or (y1 - y0) > 0.05 * height:
        return None
    if previous_end is not None and (y0 - previous_end) < 0.012 * height:
        return None

    box = mask.crop((0, y0, width, y1 + 1)).getbbox()
    if box is None:
        return None
    x0, _, x1, _ = box
    if (x1 - x0) <= 0 or (x1 - x0) > 0.35 * width:
        return None
    return y0 / scale


def detect_columns(page: PageInfo) -> None:
    """Split the page into one or two columns using the ink profile."""
    page.columns = []
    if page.width <= 0 or page.height <= 0:
        return

    top = max(page.content_top, page.height * 0.05)
    bottom = min(page.content_bottom, page.height * 0.95)
    if bottom - top < page.height * 0.2:
        top, bottom = page.height * 0.05, page.height * 0.95

    thumb = page.thumb
    scale = page.thumb_scale
    width, height = thumb.size
    row_start = max(0, int(top * scale))
    row_end = min(height, int(bottom * scale))
    if row_end - row_start < 10:
        page.columns = [Column(0, page.width)]
        _assign_left_margin(page)
        return

    threshold = ink_threshold(thumb)
    mask = thumb.point(lambda value: 255 if value < threshold else 0)
    data = mask.tobytes()
    rows = row_end - row_start
    counts = [0] * width
    for y in range(row_start, row_end):
        row = data[y * width : (y + 1) * width]
        index = row.find(255)
        while index != -1:
            counts[index] += 1
            index = row.find(255, index + 1)

    low_limit = max(1.0, rows * COLUMN_INK_LIMIT)
    search_x0 = int(width * COLUMN_SEARCH[0])
    search_x1 = int(width * COLUMN_SEARCH[1])
    min_run = max(4, int(width * COLUMN_MIN_WIDTH))

    runs: list[tuple[int, int]] = []
    start = None
    for x in range(search_x0, search_x1):
        if counts[x] <= low_limit:
            if start is None:
                start = x
        elif start is not None:
            if x - start >= min_run:
                runs.append((start, x))
            start = None
    if start is not None and search_x1 - start >= min_run:
        runs.append((start, search_x1))

    split = None
    if runs:
        best = min(runs, key=lambda run: (-(run[1] - run[0]), abs((run[0] + run[1]) / 2 - width / 2)))
        center = (best[0] + best[1]) / 2
        left_density = sum(counts[: int(center)]) / max(1, rows * int(center))
        right_density = sum(counts[int(center) :]) / max(1, rows * (width - int(center)))
        if left_density >= 0.004 and right_density >= 0.004:
            split = center / scale

    if split is None:
        page.columns = [Column(0, page.width)]
    else:
        page.columns = [Column(0, split), Column(split, page.width)]
    _assign_left_margin(page)


def _assign_left_margin(page: PageInfo) -> None:
    body = [
        line
        for line in page.lines
        if line.y1 > page.content_top + 1
        and line.y0 < page.content_bottom - 1
        and line.width > 4
    ]
    for column in page.columns:
        if len(page.columns) == 1:
            own = [line.x0 for line in body]
        else:
            own = [line.x0 for line in body if column.x0 <= line.center_x < column.x1]
        if own:
            column.left_margin = _percentile(own, 0.1)
        else:
            column.left_margin = column.x0 + 0.04 * (column.x1 - column.x0)


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, int(len(ordered) * fraction)))
    return ordered[index]


def find_question_starts(pages: list[PageInfo]) -> list[QuestionStart]:
    starts: list[QuestionStart] = []
    for page in pages:
        tolerance = max(12.0, page.width * 0.015)
        for line in page.lines:
            if line.y1 <= page.content_top + 1 or line.y0 >= page.content_bottom - 1:
                continue
            match = QUESTION_RE.match(line.text)
            if not match:
                continue
            question = int(match.group(1))
            if not 1 <= question <= 99:
                continue
            if not page.columns:
                continue
            column_index = 0 if len(page.columns) == 1 else int(line.x0 >= page.columns[0].x1)
            column = page.columns[column_index]
            if line.x0 > column.left_margin + tolerance:
                continue
            starts.append(QuestionStart(question, page.number, column_index, line.y0))

    starts.sort(key=lambda start: (start.page, start.column, start.y0))
    deduped: list[QuestionStart] = []
    seen: dict[tuple[int, int, int], float] = {}
    for start in starts:
        key = (start.page, start.column, start.question)
        band = max(6.0, pages[start.page - 1].height * 0.02)
        last_y = seen.get(key)
        if last_y is not None and abs(last_y - start.y0) < band:
            continue
        seen[key] = start.y0
        deduped.append(start)
    return deduped


def find_question_gaps(starts: list[QuestionStart]) -> list[int]:
    """Question numbers missing from the expected run of the numbering.

    Numbering normally starts at 1, so a missing first question is reported.
    A paper that starts later (a page or section excerpt) is only checked for
    gaps after its first detected number.
    """
    numbers = sorted({start.question for start in starts})
    if not numbers:
        return []
    first = 1 if numbers[0] <= 2 else numbers[0]
    present = set(numbers)
    return [value for value in range(first, numbers[-1] + 1) if value not in present]


def format_number_ranges(numbers: list[int]) -> str:
    """Compress [1, 4, 5, 9] into '1, 4-5, 9'."""
    ordered = sorted(set(numbers))
    if not ordered:
        return ""
    parts: list[str] = []
    start = previous = ordered[0]
    for value in ordered[1:]:
        if value == previous + 1:
            previous = value
            continue
        parts.append(f"{start}-{previous}" if start != previous else str(start))
        start = previous = value
    parts.append(f"{start}-{previous}" if start != previous else str(start))
    return ", ".join(parts)


def build_segments(starts: list[QuestionStart], pages: list[PageInfo]) -> list[Segment]:
    """Turn question starts into raw crop rectangles in reading order."""
    segments: list[Segment] = []
    for index, start in enumerate(starts):
        following = starts[index + 1] if index + 1 < len(starts) else None
        segments.extend(_segments_between(start, following, pages))
    return segments


def _segments_between(
    start: QuestionStart, following: QuestionStart | None, pages: list[PageInfo]
) -> list[Segment]:
    start_page = start.page - 1
    if following is None:
        end_page = len(pages) - 1
        end_column = len(pages[-1].columns) - 1
        end_y = pages[-1].content_bottom
    else:
        end_page = following.page - 1
        end_column = following.column
        end_y = following.y0

    segments: list[Segment] = []

    def add(page_index: int, column_index: int, y0: float, y1: float) -> None:
        if not 0 <= page_index < len(pages):
            return
        page = pages[page_index]
        if not 0 <= column_index < len(page.columns):
            return
        column = page.columns[column_index]
        y0 = max(y0, page.content_top)
        y1 = min(y1, page.content_bottom)
        if y1 - y0 <= 1:
            return
        segments.append(
            Segment(
                question=start.question,
                page=page.number,
                column=column_index,
                x0=column.x0,
                y0=y0,
                x1=column.x1,
                y1=y1,
            )
        )

    if start_page == end_page:
        if start.column == end_column:
            add(start_page, start.column, start.y0, end_y)
        else:
            page = pages[start_page]
            add(start_page, start.column, start.y0, page.content_bottom)
            for column_index in range(start.column + 1, end_column):
                add(start_page, column_index, page.content_top, page.content_bottom)
            add(end_page, end_column, pages[end_page].content_top, end_y)
    else:
        page = pages[start_page]
        add(start_page, start.column, start.y0, page.content_bottom)
        for column_index in range(start.column + 1, len(page.columns)):
            add(start_page, column_index, page.content_top, page.content_bottom)
        for page_index in range(start_page + 1, end_page):
            for column_index in range(len(pages[page_index].columns)):
                add(
                    page_index,
                    column_index,
                    pages[page_index].content_top,
                    pages[page_index].content_bottom,
                )
        for column_index in range(0, end_column):
            add(end_page, column_index, pages[end_page].content_top, pages[end_page].content_bottom)
        add(end_page, end_column, pages[end_page].content_top, end_y)

    return segments
