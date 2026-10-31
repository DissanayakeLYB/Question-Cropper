import unittest

from PIL import Image, ImageDraw

from cropper.detect import QuestionStart, build_segments
from cropper.edges import PageInk, trim_to_ink
from cropper.models import Box, Column, PageInfo, TextLine
from cropper.selfcheck import (
    BOTTOM_INK,
    OVERLAP,
    TALL,
    TOP_INK,
    review_flags,
    split_swallowed_questions,
)

WIDTH, HEIGHT = 1000, 1500
FRAME = (50, 60, 950, 1340)
TEXT_LEFT = 90
WORD_WIDTH = 52
WORD_GAP = 14
INK_HEIGHT = 13

# Question 2's number and text sit inside the band that question 1 would claim
# when question 2 is not detected as a start of its own.
Q1_LINES = (180, 210, 240)
Q2_NUMBER = 300
Q2_LINES = (300, 330, 360)
Q3_LINES = (700, 730)


def _draw_line(draw: ImageDraw.ImageDraw, y: int, words: int = 10) -> None:
    for index in range(words):
        left = TEXT_LEFT + index * (WORD_WIDTH + WORD_GAP)
        draw.rectangle((left, y, left + WORD_WIDTH, y + INK_HEIGHT), fill=0)


def make_image() -> Image.Image:
    image = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle(FRAME, outline=0, width=2)
    _draw_line(draw, 70, words=6)  # page header
    for y in Q1_LINES:
        _draw_line(draw, y)
    for y in Q2_LINES:
        _draw_line(draw, y)
    for y in Q3_LINES:
        _draw_line(draw, y)
    return image


def make_page() -> PageInfo:
    lines = [TextLine(f"{number}. question", TEXT_LEFT, y, TEXT_LEFT + 60, y + 13)
             for number, y in ((1, Q1_LINES[0]), (2, Q2_NUMBER), (3, Q3_LINES[0]))]
    return PageInfo(
        number=1,
        width=WIDTH,
        height=HEIGHT,
        lines=lines,
        thumb=Image.new("L", (100, 150), 255),
        thumb_scale=0.1,
        content_top=100.0,
        content_bottom=float(FRAME[3]),
        columns=[Column(0, WIDTH, left_margin=float(TEXT_LEFT))],
    )


def swallowed_box() -> tuple[Image.Image, PageInfo, PageInk, Box]:
    """One box for Q1 that runs over Q2 because Q2's start was not detected."""
    image = make_image()
    page = make_page()
    ink = PageInk(image)
    starts = [QuestionStart(1, 1, 0, float(Q1_LINES[0])), QuestionStart(3, 1, 0, float(Q3_LINES[0]))]
    segments = build_segments(starts, [page])
    box = trim_to_ink(ink, segments[0], page)
    assert box is not None and box.question == 1
    return image, page, ink, box


def row_has_ink(image: Image.Image, box: Box, y: int) -> bool:
    mask = image.point(lambda value: 255 if value < 128 else 0)
    return mask.crop((box.x0, y, box.x1, y + 1)).getbbox() is not None


class SplitTests(unittest.TestCase):
    """A box that holds a second question number is cut in two."""

    def setUp(self) -> None:
        self.image, self.page, self.ink, self.box = swallowed_box()
        self.boxes = split_swallowed_questions([self.box], [self.page], {1: self.ink})

    def test_box_that_swallowed_a_question_is_split(self) -> None:
        self.assertEqual(len(self.boxes), 2)
        self.assertEqual([box.question for box in self.boxes], [1, 2])

    def test_split_puts_the_new_question_on_its_own_number(self) -> None:
        first, second = self.boxes
        # The second box starts at question 2's number, which fills the
        # number's rows; the first box ends above it.
        self.assertLessEqual(first.y1, Q2_NUMBER)
        self.assertLess(second.y0, Q2_NUMBER + INK_HEIGHT)
        self.assertGreaterEqual(second.y1, Q2_NUMBER + INK_HEIGHT)

    def test_split_halves_do_not_overlap_and_keep_their_edges_blank(self) -> None:
        first, second = self.boxes
        self.assertLessEqual(first.y1, second.y0)
        for box in self.boxes:
            for y in (box.y0, box.y1 - 1):
                self.assertFalse(
                    row_has_ink(self.image, box, y),
                    f"Q{box.question}: edge at row {y} has ink",
                )

    def test_split_box_still_contains_its_own_text(self) -> None:
        first = self.boxes[0]
        self.assertLessEqual(first.y0, Q1_LINES[0])
        self.assertGreaterEqual(first.y1, Q1_LINES[-1] + INK_HEIGHT)


class FlagTests(unittest.TestCase):
    """Each rules marks the box for review, and a clean box stays clear."""

    def setUp(self) -> None:
        self.image = make_image()
        self.page = make_page()
        self.ink = PageInk(self.image)

    def _flags(self, boxes: list[Box], expected: int = 50) -> list[list[str]]:
        review_flags(boxes, [self.page], {1: self.ink}, expected)
        return [box.review for box in boxes]

    @staticmethod
    def _box(question: int, y0: int, y1: int, x0: int = TEXT_LEFT, x1: int = 900) -> Box:
        return Box(question, 1, x0, y0, x1, y1)

    def test_clean_boxes_are_not_flagged(self) -> None:
        boxes = [self._box(1, Q1_LINES[0] - 10, Q1_LINES[-1] + 20),
                 self._box(2, Q2_LINES[0] - 10, Q2_LINES[-1] + 20),
                 self._box(3, Q3_LINES[0] - 10, Q3_LINES[-1] + 20)]
        self.assertEqual(self._flags(boxes[:2] + [boxes[2]]), [[], [], []])

    def test_ink_on_an_edge_is_flagged(self) -> None:
        boxes = [
            self._box(1, Q1_LINES[0], Q1_LINES[-1] + 20),  # top row is text
            self._box(2, Q1_LINES[0] - 10, Q2_LINES[-1] + 13),  # bottom row is text
            self._box(3, Q3_LINES[0] - 10, Q3_LINES[-1] + 20),
        ]
        flags = self._flags(boxes)
        self.assertIn(TOP_INK, flags[0])
        self.assertIn(BOTTOM_INK, flags[1])
        self.assertEqual(flags[2], [])

    def test_tall_box_is_flagged(self) -> None:
        boxes = [
            self._box(1, 200, 240),
            self._box(2, 400, 440),
            self._box(3, 600, 1000),  # much taller than the others
        ]
        flags = self._flags(boxes, expected=3)
        self.assertEqual(flags[0], [])
        self.assertEqual(flags[1], [])
        self.assertTrue(any(reason.startswith(TALL) for reason in flags[2]))

    def test_overlapping_boxes_are_flagged(self) -> None:
        boxes = [
            self._box(1, Q1_LINES[0] - 10, Q1_LINES[-1] + 20),
            self._box(2, Q1_LINES[-1] + 5, Q2_LINES[-1] + 20),
        ]
        flags = self._flags(boxes[:1] + boxes[1:], expected=2)
        self.assertTrue(any(reason.startswith(OVERLAP) for reason in flags[0]))
        self.assertTrue(any(reason.startswith(OVERLAP) for reason in flags[1]))

    def test_box_after_a_missing_number_is_flagged(self) -> None:
        boxes = [
            self._box(1, Q1_LINES[0] - 10, Q1_LINES[-1] + 20),
            self._box(3, Q3_LINES[0] - 10, Q3_LINES[-1] + 20),
        ]
        flags = self._flags(boxes, expected=3)
        self.assertEqual(flags[0], [])
        self.assertIn("question 2 is missing before it", flags[1])

    def test_number_outside_the_expected_run_is_flagged(self) -> None:
        boxes = [
            self._box(1, Q1_LINES[0] - 10, Q1_LINES[-1] + 20),
            self._box(51, Q2_LINES[0] - 10, Q2_LINES[-1] + 20),
        ]
        flags = self._flags(boxes, expected=50)
        self.assertEqual(flags[0], [])
        self.assertIn("question 51 is outside 1-50", flags[1])

    def test_second_question_number_that_cannot_be_split_is_flagged(self) -> None:
        _, _, _, box = swallowed_box()
        flags = self._flags([box], expected=3)
        self.assertIn("contains a second question number (2)", flags[0])


if __name__ == "__main__":
    unittest.main()
