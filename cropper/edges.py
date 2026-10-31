"""Snap crop edges to blank rows and keep the page frame out of the crops.

The page is rendered before cropping, so its horizontal ink profile is known
exactly: a row with no dark pixel is blank. Edges are placed on blank rows, ten
pixels away from the question's own ink, and pulled in to the middle of the
blank gap when two questions are closer than that, so one box can never overlap
the next. The page frame is measured from its long rules: the frame's horizontal
rules clamp the top and bottom edges, and its vertical rules are excluded from
the horizontal trim.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from PIL import Image

from . import PADDING
from .detect import Segment
from .models import Box, PageInfo
from .page import ink_threshold

# A vertical rule is dark in almost every row of the crop it runs past, while a
# column of text is dark only where the letters are.
RULE_COVERAGE = 0.6
# The blurred edge of a scanned frame line stays darker than text for a few
# columns on either side.
RULE_EDGE_COVERAGE = 0.15
RULE_GROW = 3
# A scanned page is speckled with isolated dark pixels. Ink that is part of a
# stroke at least this wide is text; isolated pixels are noise.
MIN_STROKE_RUN = 3
# A row counts as a line of text only when a real share of it is inked.
MIN_INK_ROW_FRACTION = 0.015
# A scanned speck is a few rows tall; a line of text set at 200 DPI is not.
MIN_TEXT_ROWS = 10
# A frame line seen through a broken scan still runs down much of the crop.
RULE_RUN_FRACTION = 0.35
# The page's left and right borders run down a large part of the whole page.
FRAME_SIDE_RUN = 0.25
# A horizontal rule fills most of its row and is unbroken, while a line of text
# is a series of short runs with gaps between the letters.
RULE_FILL = 0.40
RULE_MIN_RUN = 0.15  # of page width, in one continuous run
# Rules are thin: a dense block of text must not be mistaken for the frame.
RULE_MAX_THICKNESS = 0.006  # of page height
FRAME_EDGE_ZONE = (0.15, 0.85)  # a frame rule sits in the outer part of the page
# A real page frame encloses most of the page; a stray underline does not.
FRAME_MIN_SPAN = 0.35
# Rules are scanned, so their blurred edge can bleed a few rows further.
FRAME_STEP_ROWS = 6
MIN_RULE_HEIGHT = 8


@dataclass(frozen=True)
class InkBand:
    """A contiguous run of dark rows or columns, inclusive."""

    start: int
    end: int


def _clusters(values: list[int], gap: int = 0) -> list[tuple[int, int]]:
    """Group ascending positions, tolerating ``gap`` missing values."""
    bands: list[tuple[int, int]] = []
    for value in values:
        if bands and value - bands[-1][1] <= gap + 1:
            bands[-1] = (bands[-1][0], value)
        else:
            bands.append((value, value))
    return bands


def _in_band(bands: list[InkBand], value: float) -> bool:
    return any(band.start <= value <= band.end for band in bands)


def _stroke_pixels(row: bytes, min_run: int = MIN_STROKE_RUN) -> int:
    """Dark pixels that belong to a stroke, not to isolated scanner speckle."""
    return sum(len(part) for part in row.split(b"\x00") if len(part) >= min_run)


class PageInk:
    """Dark-pixel profile of one rendered page."""

    def __init__(self, image: Image.Image, threshold: int | None = None) -> None:
        gray = image.convert("L")
        self.width, self.height = gray.size
        self.threshold = ink_threshold(gray) if threshold is None else threshold
        self.mask = gray.point(lambda value: 255 if value < self.threshold else 0)
        self._data = self.mask.tobytes()
        # The centre of the page, away from the frame's vertical borders, so a
        # border cannot make every row look inked.
        left = int(self.width * 0.1)
        right = max(left + 1, int(self.width * 0.9))
        centre = self.mask.crop((left, 0, right, self.height))
        centre_data = centre.tobytes()
        centre_width = centre.width
        self.row_ink = [
            centre_data[y * centre_width : (y + 1) * centre_width].count(255)
            for y in range(self.height)
        ]
        self.frame_top, self.frame_bottom = self._frame_limits(centre_width)
        self.frame_left, self.frame_right = self._frame_sides()

    def _frame_limits(self, width: int) -> tuple[int, int]:
        """The inside of the page frame, measured from its horizontal rules."""
        if self.width <= 0 or self.height <= 0 or width <= 0:
            return 0, self.height
        fill = RULE_FILL * width
        run = RULE_MIN_RUN * width
        left = int(self.width * 0.1)
        hits = []
        for y, count in enumerate(self.row_ink):
            if count < fill:
                continue
            row = self._data[y * self.width + left : y * self.width + left + width]
            if max(map(len, row.split(b"\x00"))) >= run:
                hits.append(y)
        thickness = max(5, int(round(RULE_MAX_THICKNESS * self.height)))
        rules = [
            (start, end)
            for start, end in _clusters(hits, gap=1)
            if end - start + 1 <= thickness
        ]
        top, bottom = 0, self.height
        if rules and rules[0][0] < FRAME_EDGE_ZONE[0] * self.height:
            top = rules[0][1] + 1
            for _ in range(FRAME_STEP_ROWS):
                if top < self.height and self.row_ink[top] > 0:
                    top += 1
                else:
                    break
        if rules and rules[-1][1] > FRAME_EDGE_ZONE[1] * self.height:
            bottom = rules[-1][0]
            for _ in range(FRAME_STEP_ROWS):
                if bottom > 0 and self.row_ink[bottom] > 0:
                    bottom -= 1
                else:
                    break
        if bottom - top < FRAME_MIN_SPAN * self.height:
            return 0, self.height
        return top, bottom

    def _frame_sides(self) -> tuple[int, int]:
        """The inside of the page frame, measured from its vertical rules."""
        if self.width <= 2 or self.height <= 0:
            return 0, self.width
        need = max(3 * MIN_RULE_HEIGHT, int(self.height * FRAME_SIDE_RUN))
        hits = []
        for x in range(self.width):
            column = self._data[x:: self.width]
            if max(map(len, column.split(b"\x00"))) >= need:
                hits.append(x)
        if not hits:
            return 0, self.width
        # Skew makes a border wander over a few columns; join those.
        bands = _clusters(hits, gap=6)
        left = [band for band in bands if band[0] < FRAME_EDGE_ZONE[0] * self.width]
        right = [band for band in bands if band[1] > FRAME_EDGE_ZONE[1] * self.width]
        frame_left = 0
        frame_right = self.width
        if left:
            frame_left = left[0][1] + 1
        if right:
            frame_right = right[-1][0]
        if frame_right - frame_left < FRAME_MIN_SPAN * self.width:
            return 0, self.width
        return frame_left, frame_right

    def column_counts(self, x0: int, y0: int, x1: int, y1: int) -> list[int]:
        """Dark pixels per column inside a region."""
        region = self.mask.crop((x0, y0, x1, y1))
        data = region.tobytes()
        width = region.width
        if width <= 0:
            return []
        return [data[x::width].count(255) for x in range(width)]

    def column_stats(
        self, x0: int, y0: int, x1: int, y1: int
    ) -> tuple[list[int], list[int]]:
        """Dark pixels and longest dark run per column inside a region."""
        region = self.mask.crop((x0, y0, x1, y1))
        data = region.tobytes()
        width = region.width
        if width <= 0:
            return [], []
        counts: list[int] = []
        runs: list[int] = []
        for x in range(width):
            column = data[x::width]
            counts.append(column.count(255))
            runs.append(max(map(len, column.split(b"\x00"))))
        return counts, runs

    def row_counts(
        self, x0: int, x1: int, y0: int = 0, y1: int | None = None, strokes: bool = False
    ) -> list[int]:
        """Ink per row inside a vertical strip, indexed from ``y0``."""
        y1 = self.height if y1 is None else y1
        region = self.mask.crop((x0, y0, x1, y1))
        data = region.tobytes()
        width = region.width
        if width <= 0 or region.height <= 0:
            return []
        rows = [data[y * width : (y + 1) * width] for y in range(region.height)]
        if strokes:
            return [_stroke_pixels(row) for row in rows]
        return [row.count(255) for row in rows]

    def rule_columns(
        self, counts: list[int], height: int, offset: int = 0, runs: list[int] | None = None
    ) -> list[InkBand]:
        """Columns that are dark down the crop: vertical rules of the frame.

        A scanned frame line can be broken, so a long continuous run counts as
        a rule even when the column is not dark for every row.
        """
        if height < MIN_RULE_HEIGHT:
            return []
        need = max(4.0, height * RULE_COVERAGE)
        edge = need * RULE_EDGE_COVERAGE
        run_need = max(2 * MIN_RULE_HEIGHT, height * RULE_RUN_FRACTION)
        runs = runs or [0] * len(counts)
        hits = [
            x
            for x, count in enumerate(counts)
            if count >= need or runs[x] >= run_need
        ]
        bands: list[InkBand] = []
        for start, end in _clusters(hits, gap=1):
            # The scan blurs the rule over a few extra columns; leave none behind.
            for _ in range(RULE_GROW):
                if start > 0 and counts[start - 1] >= edge:
                    start -= 1
                else:
                    break
            for _ in range(RULE_GROW):
                if end + 1 < len(counts) and counts[end + 1] >= edge:
                    end += 1
                else:
                    break
            bands.append(InkBand(start + offset, end + offset))
        return bands

    def row_is_blank(self, y: int, x0: int, x1: int, min_ink: int = 1) -> bool:
        """True when a row has no text ink across a page span."""
        if not 0 <= y < self.height:
            return False
        row = self.mask.crop((x0, y, x1, y + 1)).tobytes()
        return _stroke_pixels(row) < max(1, min_ink)


def _top_edge(inked: list[bool], first_ink: int, limit: int, padding: int) -> int:
    """A blank row above the question's first line, no more than ``padding`` away."""
    edge = max(first_ink - padding, limit)
    search = first_ink - 1
    stop = max(limit, first_ink - (2 * padding + 1))
    while search >= stop and not inked[search]:
        search -= 1
    if search >= stop and inked[search]:
        # Too close for padding on both sides: sit in the middle of the gap.
        edge = max(edge, (search + first_ink) // 2)
    while edge < first_ink and inked[edge]:
        edge += 1
    return min(edge, first_ink)


def _bottom_edge(inked: list[bool], last_ink: int, limit: int, padding: int) -> int:
    """A blank row below the question's last line, no more than ``padding`` away."""
    edge = min(last_ink + 1 + padding, limit)
    search = last_ink + 1
    stop = min(limit, len(inked) - 1, last_ink + (2 * padding + 1))
    while search <= stop and not inked[search]:
        search += 1
    if search <= stop and inked[search]:
        edge = min(edge, (last_ink + search) // 2)
    edge = max(edge, last_ink + 1)
    while edge > last_ink + 1 and edge < len(inked) and inked[edge]:
        edge -= 1
    return edge


def trim_to_ink(
    ink: PageInk, segment: Segment, page: PageInfo, padding: int = PADDING
) -> Box | None:
    """Turn a raw segment into a box whose edges sit on blank rows."""
    if not 0 <= segment.column < len(page.columns):
        return None
    column = page.columns[segment.column]

    x0 = max(0, int(round(segment.x0)), int(round(column.x0)))
    x1 = min(ink.width, int(round(segment.x1)), int(round(column.x1)))
    # The padding may reach past the segment, but never past the content area
    # or the page frame.
    top_limit = max(0, int(math.ceil(max(page.content_top, ink.frame_top))))
    bottom_limit = min(
        ink.height, int(math.floor(min(page.content_bottom, ink.frame_bottom)))
    )
    segment_top = max(top_limit, int(math.floor(segment.y0)))
    segment_bottom = min(bottom_limit, int(math.ceil(segment.y1)))
    if x1 - x0 <= 1 or segment_bottom - segment_top <= 1:
        return None

    columns, runs = ink.column_stats(x0, segment_top, x1, segment_bottom)
    rules = ink.rule_columns(columns, segment_bottom - segment_top, x0, runs)

    # Ignore isolated speckle; a real text column is inked in several rows.
    min_ink = max(2, int(round((segment_bottom - segment_top) * 0.01)))
    hits = [
        index + x0
        for index, count in enumerate(columns)
        if count >= min_ink and not _in_band(rules, index + x0)
    ]
    if not hits:
        return None
    # Drop narrow marks far from the text block (scan speckle, page-edge noise).
    groups = _clusters(hits, gap=padding)
    widest = max(groups, key=lambda band: (band[1] - band[0], -band[0]))
    keep = [band for band in groups if band[1] - band[0] + 1 >= MIN_RULE_HEIGHT]
    if widest not in keep:
        keep.append(widest)
    first_col = min(band[0] for band in keep)
    last_col = max(band[1] for band in keep)

    left = first_col - padding
    left_rule = max((band.end for band in rules if band.end < first_col), default=None)
    if left_rule is None:
        # Stray ink left of the text still must not pull the crop outside the frame.
        left_rule = max(
            (
                band.end
                for band in rules
                if band.start <= column.left_margin + padding
            ),
            default=None,
        )
    if left_rule is not None:
        # Never include the frame's vertical rule, even when padding reaches it.
        left = max(left, left_rule + 1 + RULE_GROW)
    right = last_col + 1 + padding
    right_rule = min((band.start for band in rules if band.start > last_col), default=None)
    if right_rule is None:
        # Blur just past a rule is still the rule, not text.
        tail = [band.start for band in rules if band.start >= last_col - 2 * padding]
        if tail:
            right_rule = min(tail)
    if right_rule is not None:
        right = min(right, right_rule - RULE_GROW)
    left = max(left, int(round(column.x0)), ink.frame_left, 0)
    right = min(right, int(round(column.x1)), ink.frame_right, ink.width)
    if right - left <= 1:
        return None

    # A rule that reached the edge through padding is still the frame: step in
    # until the edge column is text again.
    span = max(1, segment_bottom - segment_top)
    cover = 0.5 * span

    def solid(x: int) -> bool:
        index = x - x0
        return 0 <= index < len(columns) and columns[index] >= cover

    stop = min(left + 3 * RULE_GROW * 2, right - MIN_RULE_HEIGHT)
    while left < stop and solid(left):
        left += 1
    stop = max(left + MIN_RULE_HEIGHT, right - 3 * RULE_GROW * 2)
    while right > stop and solid(right - 1):
        right -= 1

    # Keep the border's blurred columns out of the row profile, so a rule cannot
    # masquerade as a line of text at the crop edge.
    row_left, row_right = left, right
    if row_right - row_left < MIN_RULE_HEIGHT:
        row_left, row_right = first_col, last_col + 1
    rows = ink.row_counts(row_left, row_right, strokes=True)
    min_row_ink = max(8, int((row_right - row_left) * MIN_INK_ROW_FRACTION))
    inked = [count >= min_row_ink for count in rows]
    # The question's first line can start a few pixels above the extracted line
    # box, but the previous question's line must not be mistaken for it: take the
    # start of a fresh ink band, never the tail of the band above.
    first_ink = next(
        (
            y
            for y in range(max(top_limit, segment_top - padding), segment_bottom)
            if inked[y] and not inked[y - 1]
        ),
        None,
    )
    if first_ink is None:
        return None
    last_ink = next(
        (y for y in range(segment_bottom - 1, segment_top - 1, -1) if inked[y]), None
    )
    if last_ink is None:
        return None
    if last_ink - first_ink < MIN_TEXT_ROWS - 1:
        # Taller ink than this is a line of text; shorter ink is a speck or the
        # blurred tail of something outside the segment, which has no question.
        return None

    top = _top_edge(inked, first_ink, top_limit, padding)
    bottom = _bottom_edge(inked, last_ink, bottom_limit, padding)

    if right - left <= 1 or bottom - top <= 1:
        return None
    return Box(
        question=segment.question,
        page=page.number,
        x0=left,
        y0=top,
        x1=right,
        y1=bottom,
    )


# Edges moved by hand are snapped to the nearest blank row within this reach.
SNAP_RADIUS = 80

def _nearest_blank(ink: PageInk, x0: int, x1: int, y: int, low: int, high: int) -> int:
    """The blank row closest to ``y``, searched outward and kept within bounds."""
    if low > high:
        return y
    if ink.row_is_blank(y, x0, x1):
        return y
    for offset in range(1, SNAP_RADIUS + 1):
        for candidate in (y - offset, y + offset):
            if low <= candidate <= high and ink.row_is_blank(candidate, x0, x1):
                return candidate
    return y


def snap_rows(
    ink: PageInk, x0: int, y0: int, x1: int, y1: int, min_height: int = 20
) -> tuple[int, int]:
    """Move a box's top and bottom edges onto the nearest blank rows.

    A hand-dragged edge lands wherever the pointer was, often a few pixels into
    a line of text. Both edges are pushed to the nearest row with no ink, and
    the box is kept at least ``min_height`` tall.
    """
    left = max(0, min(x0, ink.width - 1))
    right = max(left + 1, min(x1, ink.width))
    top = max(0, min(y0, ink.height - 1))
    bottom = max(top + 1, min(y1, ink.height))
    snapped_top = _nearest_blank(ink, left, right, top, 0, bottom - min_height)
    snapped_bottom = _nearest_blank(
        ink, left, right, bottom, snapped_top + min_height, ink.height
    )
    return snapped_top, max(snapped_bottom, snapped_top + min_height)


def resolve_overlaps(boxes: list[Box], inks: dict[int, PageInk]) -> list[Box]:
    """Keep boxes on one page from overlapping.

    Each bottom edge is moved to a blank row at or above the next box's top
    edge, so no row of a page is claimed by two boxes.
    """
    by_page: dict[int, list[Box]] = {}
    for box in boxes:
        by_page.setdefault(box.page, []).append(box)

    for page_number, page_boxes in by_page.items():
        ink = inks.get(page_number)
        page_boxes.sort(key=lambda box: (box.y0, box.x0))
        for index, first in enumerate(page_boxes):
            for second in page_boxes[index + 1 :]:
                if second.y0 >= first.y1:
                    break
                if first.x1 <= second.x0 or second.x1 <= first.x0:
                    continue
                boundary = _shared_boundary(ink, first, second)
                first.y1 = min(first.y1, boundary)
                second.y0 = max(second.y0, boundary)
    return boxes


def _shared_boundary(ink: PageInk | None, first: Box, second: Box) -> int:
    """A blank row between the two boxes, as close to halfway as possible."""
    low, high = second.y0, first.y1
    middle = (low + high) // 2
    if ink is None:
        return middle
    x0 = min(first.x0, second.x0)
    x1 = max(first.x1, second.x1)
    min_ink = max(8, int((x1 - x0) * MIN_INK_ROW_FRACTION))
    span = max(middle - low, high - middle)
    for offset in range(span + 1):
        for candidate in (middle - offset, middle + offset):
            if low <= candidate <= high and ink.row_is_blank(candidate, x0, x1, min_ink):
                return candidate
    return middle
