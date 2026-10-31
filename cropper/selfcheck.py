"""Check every crop for problems, and split boxes that swallowed a question.

Detection can still go wrong in ways a reader can name: a box may run past the
last line it owns and swallow the next question, stand far taller than the rest,
stop with ink on its edge, overlap a neighbour, or carry a number that breaks
the paper's numbering. A box that holds a second question number is split at
that number, and both halves are trimmed again so their edges sit on blank rows.
"""

from __future__ import annotations

import statistics
from dataclasses import replace

from . import PADDING
from .detect import QUESTION_RE, Segment, question_alignments
from .edges import PageInk, trim_to_ink
from .models import Box, Column, PageInfo, TextLine

# A question twice as tall as the typical one is worth a look.
TALL_FACTOR = 2.0

TALL = "more than twice the median height"
TOP_INK = "ink touches the top edge"
BOTTOM_INK = "ink touches the bottom edge"
OVERLAP = "overlaps another box"


def _column_index(box: Box, page: PageInfo) -> int:
    if len(page.columns) <= 1:
        return 0
    midpoint = (box.x0 + box.x1) / 2
    index = 0 if midpoint < page.columns[0].x1 else 1
    return min(index, len(page.columns) - 1)


def _column_alignment(page: PageInfo, box: Box) -> tuple[float | None, float]:
    """The shared left edge of the page's question numbers, and its tolerance."""
    tolerance = max(12.0, page.width * 0.015)
    alignments = question_alignments(page, tolerance)
    return alignments.get(_column_index(box, page)), tolerance


def _inside(box: Box, line: TextLine) -> bool:
    return box.y0 < line.y0 and line.y1 < box.y1


def _first_intruding_number(
    box: Box, page: PageInfo, alignment: float | None, tolerance: float
) -> tuple[int, TextLine] | None:
    """The earliest question number printed inside the box that is not its own."""
    column = page.columns[_column_index(box, page)] if page.columns else None
    best: tuple[int, TextLine] | None = None
    for line in page.lines:
        if not _inside(box, line):
            continue
        if not box.x0 <= line.x0 <= box.x1:
            continue
        match = QUESTION_RE.match(line.text)
        if not match:
            continue
        number = int(match.group(1))
        if number == box.question or not 1 <= number <= 99:
            continue
        if alignment is not None:
            # A number in the body of a question does not line up with the
            # question numbers; this rejects text such as "2.0 s".
            if abs(line.x0 - alignment) > tolerance:
                continue
        elif column is not None and line.x0 > column.left_margin + tolerance:
            continue
        if best is None or line.y0 < best[1].y0:
            best = (number, line)
    return best


def _gap_middle(ink: PageInk, box: Box, y: float) -> float:
    """A blank row halfway between the text above ``y`` and the text at ``y``.

    The new bottom edge of the first half and the new top edge of the second
    both land on this row, so the halves meet without overlapping and neither
    edge cuts a line.
    """
    start = max(box.y0 + 1, int(y))
    stop = min(box.y1, start + 2 * PADDING)
    first = next((row for row in range(start, stop) if not ink.row_is_blank(row, box.x0, box.x1)), None)
    if first is None or first - 1 <= box.y0:
        return float(y)
    above = next(
        (row for row in range(first - 1, box.y0, -1) if not ink.row_is_blank(row, box.x0, box.x1)),
        None,
    )
    if above is None or first - above < 2:
        return float(first)
    return (above + first) / 2


def _split_box(
    box: Box, number: int, cut: float, page: PageInfo, ink: PageInk
) -> list[Box] | None:
    """Trim the two halves of ``box`` above and below ``cut``, or give up."""
    column = page.columns[_column_index(box, page)] if page.columns else None
    margin = column.left_margin if column is not None else float(box.x0)
    spans = (
        (box.question, float(box.y0), cut),
        (number, cut, float(box.y1)),
    )
    parts: list[Box] = []
    for question, top, bottom in spans:
        # Each half is trimmed inside its own slice of the page, so the new
        # edges cannot wander into the other half.
        sub_page = replace(
            page,
            columns=[Column(float(box.x0), float(box.x1), left_margin=margin)],
            content_top=top,
            content_bottom=bottom,
        )
        segment = Segment(question, box.page, 0, float(box.x0), top, float(box.x1), bottom)
        trimmed = trim_to_ink(ink, segment, sub_page, padding=PADDING)
        if trimmed is None:
            return None
        parts.append(trimmed)
    return parts


def split_swallowed_questions(
    boxes: list[Box], pages: list[PageInfo], inks: dict[int, PageInk]
) -> list[Box]:
    """Cut a box in two at a second question number printed inside it."""
    present = {box.question for box in boxes}
    result: list[Box] = []
    for box in boxes:
        page = pages[box.page - 1]
        ink = inks.get(box.page)
        alignment, tolerance = _column_alignment(page, box)
        intruder = (
            None if ink is None else _first_intruding_number(box, page, alignment, tolerance)
        )
        if intruder is None or intruder[0] in present:
            # Nothing inside, or the number already has its own box: the flag
            # for a second number is what the reader needs here.
            result.append(box)
            continue
        number, line = intruder
        parts = _split_box(box, number, _gap_middle(ink, box, line.y0), page, ink)
        if parts is None:
            result.append(box)
            continue
        present.add(number)
        result.extend(parts)
    return result


def review_flags(
    boxes: list[Box], pages: list[PageInfo], inks: dict[int, PageInk], expected: int
) -> None:
    """Fill each box's ``review`` list with the reasons it needs a human look."""
    if not boxes:
        return
    heights = sorted(box.height for box in boxes)
    median = statistics.median(heights)
    present = {box.question for box in boxes}

    for box in boxes:
        reasons: list[str] = []
        if not 1 <= box.question <= expected:
            reasons.append(f"question {box.question} is outside 1-{expected}")
        elif box.question > 1 and (box.question - 1) not in present:
            reasons.append(f"question {box.question - 1} is missing before it")

        if median > 0 and box.height > TALL_FACTOR * median:
            reasons.append(f"{TALL} ({box.height} px against {int(median)} px)")

        ink = inks.get(box.page)
        if ink is not None:
            if not ink.row_is_blank(box.y0, box.x0, box.x1):
                reasons.append(TOP_INK)
            if not ink.row_is_blank(box.y1 - 1, box.x0, box.x1):
                reasons.append(BOTTOM_INK)

        for other in boxes:
            if other is box or other.page != box.page:
                continue
            if other.x1 <= box.x0 or box.x1 <= other.x0:
                continue
            if other.y1 <= box.y0 or box.y1 <= other.y0:
                continue
            reasons.append(f"{OVERLAP} (Q{other.question})")
            break

        page = pages[box.page - 1]
        alignment, tolerance = _column_alignment(page, box)
        intruder = _first_intruding_number(box, page, alignment, tolerance)
        if intruder is not None:
            reasons.append(f"contains a second question number ({intruder[0]})")

        box.review = reasons
