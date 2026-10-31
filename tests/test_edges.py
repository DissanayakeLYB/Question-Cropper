import tempfile
import unittest
from itertools import combinations
from pathlib import Path

from PIL import Image, ImageDraw

from cropper import PADDING
from cropper.detect import QuestionStart, build_segments
from cropper.edges import PageInk, resolve_overlaps, trim_to_ink
from cropper.models import Column, PageInfo
from cropper.page import open_pdf, render_page
from cropper.pipeline import analyze_pdf
from tests.helpers import make_single_column_pdf

WIDTH, HEIGHT = 1000, 1500
FRAME = (50, 60, 950, 1340)
TEXT_LEFT = 90
WORD_WIDTH = 52
WORD_GAP = 14
INK_HEIGHT = 13
NOTE_TOP = 1370

# (question number, y of its first line)
STARTS = [(1, 180), (2, 300), (3, 380), (4, 492), (5, 620), (6, 800)]


def _draw_line(draw: ImageDraw.ImageDraw, y: int, words: int = 10) -> None:
    for index in range(words):
        left = TEXT_LEFT + index * (WORD_WIDTH + WORD_GAP)
        draw.rectangle((left, y, left + WORD_WIDTH, y + INK_HEIGHT), fill=0)


def make_page_image() -> Image.Image:
    """A frame, six questions (one pair almost touching) and a footer note."""
    image = Image.new("L", (WIDTH, HEIGHT), 255)
    draw = ImageDraw.Draw(image)
    draw.rectangle(FRAME, outline=0, width=2)
    _draw_line(draw, 70, words=6)  # page header, ignored through content_top
    for y in (180, 210, 240):  # Q1
        _draw_line(draw, y)
    for y in (300, 330):  # Q2
        _draw_line(draw, y)
    for y in (380, 410, 440, 470):  # Q3, its last line ends at 482
        _draw_line(draw, y)
    for y in (492, 522):  # Q4 starts only nine rows below Q3
        _draw_line(draw, y)
    for y in (620, 650, 680, 710, 740):  # Q5
        _draw_line(draw, y)
    for y in (800, 830, 860, 890, 920, 1210):  # Q6, the last question
        _draw_line(draw, y)
    draw.text((300, NOTE_TOP), "[See page two]", fill=0)
    return image


def make_page(image: Image.Image) -> PageInfo:
    return PageInfo(
        number=1,
        width=WIDTH,
        height=HEIGHT,
        lines=[],
        thumb=Image.new("L", (100, 150), 255),
        thumb_scale=0.1,
        content_top=100.0,
        content_bottom=float(HEIGHT),
        columns=[Column(0, WIDTH, left_margin=float(TEXT_LEFT))],
    )


def build_boxes():
    image = make_page_image()
    page = make_page(image)
    ink = PageInk(image)
    starts = [QuestionStart(number, 1, 0, float(y)) for number, y in STARTS]
    segments = build_segments(starts, [page])
    boxes = [
        box for box in (trim_to_ink(ink, segment, page) for segment in segments) if box is not None
    ]
    resolve_overlaps(boxes, {1: ink})
    return image, ink, boxes


def as_mask(image: Image.Image) -> Image.Image:
    return image.point(lambda value: 255 if value < 128 else 0)


def row_has_ink(mask: Image.Image, box, y: int) -> bool:
    if not 0 <= y < mask.height:
        return False
    return mask.crop((box.x0, y, box.x1, y + 1)).getbbox() is not None


class EdgeTests(unittest.TestCase):
    """Edges must fall on blank rows and never cut a line of text."""

    def setUp(self) -> None:
        self.image, self.ink, self.boxes = build_boxes()
        self.mask = as_mask(self.image)

    def test_every_question_got_a_box(self) -> None:
        self.assertEqual([box.question for box in self.boxes], [1, 2, 3, 4, 5, 6])

    def test_edges_sit_on_blank_rows(self) -> None:
        for box in self.boxes:
            for edge in (box.y0, box.y1):
                self.assertFalse(
                    row_has_ink(self.mask, box, edge),
                    f"Q{box.question}: edge at row {edge} cuts through text",
                )

    def test_no_text_line_is_cut(self) -> None:
        for box in self.boxes:
            bands = self._bands(box)
            self.assertTrue(bands, f"Q{box.question} crop contains no text")
            for start, end in bands:
                inside = start >= box.y0 and end < box.y1
                outside = end < box.y0 or start >= box.y1
                self.assertTrue(
                    inside or outside,
                    f"Q{box.question}: rows {start}-{end} are only partly inside the crop",
                )

    def test_boxes_do_not_overlap(self) -> None:
        for first, second in combinations(self.boxes, 2):
            if first.x1 <= second.x0 or second.x1 <= first.x0:
                continue
            self.assertTrue(
                first.y1 <= second.y0 or second.y1 <= first.y0,
                f"Q{first.question} and Q{second.question} overlap",
            )

    def test_tight_gap_is_split_at_its_middle(self) -> None:
        by_question = {box.question: box for box in self.boxes}
        third, fourth = by_question[3], by_question[4]

        # Q3's ink ends at 482, Q4's starts at 492; ten pixels do not fit, so
        # both edges sit in the blank gap between them.
        self.assertEqual(third.y1, fourth.y0)
        self.assertGreater(third.y1, 482)
        self.assertLess(third.y1, 492)
        self.assertFalse(row_has_ink(self.mask, third, third.y1))

    def test_padding_is_ten_pixels_of_blank_rows(self) -> None:
        for box in self.boxes:
            bands = self._bands(box)
            first, last = bands[0][0], bands[-1][1]
            self.assertLessEqual(first - box.y0, PADDING, f"Q{box.question} has too much white above")
            self.assertLessEqual(box.y1 - 1 - last, PADDING, f"Q{box.question} has too much white below")
            self.assertGreaterEqual(first - box.y0, 1)
            self.assertGreaterEqual(box.y1 - 1 - last, 1)

    def test_frame_and_note_stay_out_of_the_crop(self) -> None:
        self.assertGreaterEqual(self.ink.frame_bottom, 1338)
        for box in self.boxes:
            self.assertGreater(box.x0, FRAME[0] + 2)
            self.assertLess(box.x1, FRAME[2] - 1)
            self.assertGreaterEqual(box.y0, FRAME[1])
            self.assertLessEqual(box.y1, FRAME[3])
            self.assertLess(box.y1, NOTE_TOP, "the [See page two] note ended up in a crop")

    def test_no_crop_contains_a_vertical_frame_line(self) -> None:
        for box in self.boxes:
            crop = self.mask.crop((box.x0, box.y0, box.x1, box.y1))
            data = crop.tobytes()
            width, height = crop.size
            darkest_column = max(data[x::width].count(255) for x in range(width))
            self.assertLess(
                darkest_column, 0.9 * height, f"Q{box.question} contains the frame border"
            )

    def _bands(self, box) -> list[tuple[int, int]]:
        """Ink bands in the box's columns that touch the crop."""
        data = self.mask.crop((box.x0, 0, box.x1, HEIGHT)).tobytes()
        width = box.x1 - box.x0
        bands: list[tuple[int, int]] = []
        start = None
        for y in range(HEIGHT):
            has_ink = data[y * width : (y + 1) * width].count(255) > 0
            if has_ink and start is None:
                start = y
            elif not has_ink and start is not None:
                bands.append((start, y - 1))
                start = None
        if start is not None:
            bands.append((start, HEIGHT - 1))
        return [
            (start, end) for start, end in bands if end >= box.y0 and start < box.y1
        ]


class PipelineEdgeTests(unittest.TestCase):
    """The same guarantees on a real rendered PDF."""

    def test_text_pdf_edges_are_blank_and_boxes_do_not_overlap(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "2019.pdf"
            make_single_column_pdf(pdf)
            analysis = analyze_pdf(pdf)

            doc = open_pdf(pdf)
            try:
                masks = {
                    number: as_mask(render_page(doc[number - 1], analysis.dpi))
                    for number in range(1, doc.page_count + 1)
                }
            finally:
                doc.close()

            for box in analysis.boxes:
                mask = masks[box.page]
                for edge in (box.y0, box.y1):
                    self.assertIsNone(
                        mask.crop((box.x0, edge, box.x1, edge + 1)).getbbox(),
                        f"Q{box.question}: edge at row {edge} cuts through text",
                    )

            for first, second in combinations(analysis.boxes, 2):
                if first.page != second.page:
                    continue
                if first.x1 <= second.x0 or second.x1 <= first.x0:
                    continue
                self.assertTrue(
                    first.y1 <= second.y0 or second.y1 <= first.y0,
                    f"Q{first.question} and Q{second.question} overlap on page {first.page}",
                )

            # Q2 continues onto page 2 and must still be joined from two crops.
            self.assertEqual(
                [(box.question, box.page) for box in analysis.boxes if box.question == 2],
                [(2, 1), (2, 2)],
            )


if __name__ == "__main__":
    unittest.main()
