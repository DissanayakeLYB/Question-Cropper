import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from cropper.detect import find_question_gaps, find_question_starts
from cropper.models import Column, PageInfo, TextLine
from cropper.page import make_thumbnail, open_pdf, render_page
from cropper.repair import _render_strip, rescue_missing_starts
from tests.helpers import make_single_column_pdf


def make_page(lines, thumb=None, thumb_scale=0.25):
    page = PageInfo(
        number=1,
        width=800,
        height=1000,
        lines=lines,
        thumb=thumb or Image.new("L", (200, 250), 255),
        thumb_scale=thumb_scale,
        content_top=0.0,
        content_bottom=1000.0,
        columns=[Column(0, 800, left_margin=100.0)],
    )
    return page


class AlignmentTests(unittest.TestCase):
    def test_numbers_are_accepted_only_on_the_shared_left_edge(self) -> None:
        page = make_page(
            [
                TextLine("1. alpha", 100, 100, 600, 130),
                TextLine("2. beta", 100, 200, 600, 230),
                # A number inside body text, far from the number column.
                TextLine("3. gamma", 500, 300, 780, 330),
                # A measurement, not a question number.
                TextLine("4.0 s later", 100, 400, 600, 430),
            ]
        )

        starts = find_question_starts([page])
        self.assertEqual([start.question for start in starts], [1, 2])

    def test_page_with_one_number_falls_back_to_the_column_margin(self) -> None:
        page = make_page([TextLine("7. only question", 100, 100, 600, 130)])

        starts = find_question_starts([page])
        self.assertEqual([start.question for start in starts], [7])


class GapTests(unittest.TestCase):
    def test_expected_count_checks_the_whole_run(self) -> None:
        starts = [
            stub_start(number, 1, 0, number * 10.0) for number in (1, 2, 3, 4)
        ]

        self.assertEqual(find_question_gaps(starts, 6), [5, 6])
        self.assertEqual(find_question_gaps(starts), [])


def stub_start(number, page, column, y0):
    from cropper.detect import QuestionStart

    return QuestionStart(number, page, column, y0)


class RescueTests(unittest.TestCase):
    def test_gap_is_recovered_from_an_already_extracted_line(self) -> None:
        page = make_page(
            [
                TextLine("1. alpha", 100, 100, 600, 130),
                TextLine("2. beta", 100, 200, 600, 230),
                # Misaligned, so detection rejects it; numbering says it belongs.
                TextLine("3. gamma", 700, 400, 780, 430),
                TextLine("4. delta", 100, 800, 600, 830),
            ]
        )
        starts = find_question_starts([page])
        self.assertEqual([start.question for start in starts], [1, 2, 4])

        rescued = rescue_missing_starts(None, [page], starts, expected=4)
        by_question = {start.question: start for start in rescued}
        self.assertEqual(sorted(by_question), [1, 2, 3, 4])
        self.assertEqual(by_question[3].y0, 400)

    def test_gap_without_a_number_uses_the_blank_run_as_a_boundary(self) -> None:
        thumb = Image.new("L", (200, 250), 255)
        draw = ImageDraw.Draw(thumb)
        draw.rectangle([0, 25, 199, 49], fill=0)
        draw.rectangle([0, 150, 199, 174], fill=0)
        page = make_page(
            [
                TextLine("2. beta", 100, 100, 600, 130),
                TextLine("4. delta", 100, 700, 600, 730),
            ],
            thumb=thumb,
        )
        starts = find_question_starts([page])
        self.assertEqual([start.question for start in starts], [2, 4])

        rescued = rescue_missing_starts(None, [page], starts, expected=4)
        by_question = {start.question: start for start in rescued}
        self.assertIn(3, by_question)
        # The blank band spans page y 200-600, so the boundary lands at 600.
        self.assertAlmostEqual(by_question[3].y0, 600.0, delta=8.0)

    def test_trailing_gaps_are_reported_not_invented(self) -> None:
        page = make_page(
            [
                TextLine("1. alpha", 100, 100, 600, 130),
                TextLine("2. beta", 100, 200, 600, 230),
                TextLine("3. gamma", 100, 300, 600, 330),
                TextLine("4. delta", 100, 400, 600, 430),
            ]
        )
        starts = find_question_starts([page])

        rescued = rescue_missing_starts(None, [page], starts, expected=50)
        self.assertEqual(len(rescued), 4)


class RescueOcrGeometryTests(unittest.TestCase):
    def test_band_is_rendered_at_300_dpi(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            pdf = Path(directory) / "paper.pdf"
            make_single_column_pdf(pdf)
            doc = open_pdf(pdf)
            try:
                image = render_page(doc[0], 200)
                thumb, scale = make_thumbnail(image)
                page = PageInfo(
                    number=1,
                    width=image.width,
                    height=image.height,
                    lines=[],
                    thumb=thumb,
                    thumb_scale=scale,
                    content_top=0.0,
                    content_bottom=float(image.height),
                    columns=[Column(0, image.width, left_margin=image.width * 0.1)],
                )

                rendered = _render_strip(doc, page, 100.0, 400.0)

                self.assertIsNotNone(rendered)
                strip, _, top, factor = rendered
                self.assertEqual(top, 100.0)
                self.assertAlmostEqual(factor, 1.5)
                self.assertAlmostEqual(strip.height, 300 * 1.5, delta=3)
            finally:
                doc.close()


if __name__ == "__main__":
    unittest.main()
