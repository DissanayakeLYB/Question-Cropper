import shutil
import tempfile
import unittest
from pathlib import Path

from unittest import mock

from PIL import Image

from cropper.detect import (
    QuestionStart,
    find_question_gaps,
    find_question_starts,
    format_number_ranges,
)
from cropper.models import Column, PageInfo, TextLine
from cropper.page import ocr_lines
from cropper.pipeline import analyze_pdf
from tests.helpers import make_scanned_pdf, make_single_column_pdf, make_two_column_pdf


class DetectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_single_column_questions_margins_and_continuation(self) -> None:
        pdf = self.dir / "2019.pdf"
        make_single_column_pdf(pdf)

        analysis = analyze_pdf(pdf)
        by_question: dict[int, list] = {}
        for box in analysis.boxes:
            by_question.setdefault(box.question, []).append(box)

        self.assertEqual(sorted(by_question), [1, 2, 3, 4])
        self.assertEqual(len(by_question[2]), 2)
        self.assertEqual({box.page for box in by_question[2]}, {1, 2})

        first_page = analysis.pages[0]
        second_page = analysis.pages[1]
        self.assertGreater(first_page.content_top, 0)
        self.assertLess(first_page.content_bottom, first_page.height)
        self.assertGreater(second_page.content_top, 0)
        self.assertLess(second_page.content_bottom, second_page.height)

        for box in analysis.boxes:
            page = analysis.pages[box.page - 1]
            self.assertGreaterEqual(box.y0, page.content_top - 1)
            self.assertLessEqual(box.y1, page.content_bottom + 1)
            self.assertGreater(box.width, 20)
            self.assertGreater(box.height, 10)

        # Q1 sits below the header, not inside it.
        self.assertGreater(by_question[1][0].y0, first_page.content_top)

    def test_two_columns_detected_and_read_in_column_order(self) -> None:
        pdf = self.dir / "2020.pdf"
        make_two_column_pdf(pdf)

        analysis = analyze_pdf(pdf)
        page = analysis.pages[0]
        self.assertEqual(len(page.columns), 2)
        self.assertEqual([box.question for box in analysis.boxes], [1, 2, 3, 4])

        midpoint = page.width / 2
        left_questions = {box.question for box in analysis.boxes if box.x0 < midpoint}
        right_questions = {box.question for box in analysis.boxes if box.x0 >= midpoint}
        self.assertEqual(left_questions, {1, 2})
        self.assertEqual(right_questions, {3, 4})

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_image_only_page_uses_ocr(self) -> None:
        text_pdf = self.dir / "text.pdf"
        scanned_pdf = self.dir / "scan.pdf"
        make_single_column_pdf(text_pdf)
        make_scanned_pdf(text_pdf, scanned_pdf)

        analysis = analyze_pdf(scanned_pdf)
        questions = sorted({box.question for box in analysis.boxes})
        self.assertEqual(questions, [1, 2])

    def test_ocr_merges_both_passes_and_drops_duplicates(self) -> None:
        """A number dropped by one pass must survive the other, without duplicates."""

        def fake_image_to_data(image, lang=None, config="", output_type=None, **kwargs):
            extra = "6" in (config or "")
            words = [
                ("2.", 10, 20, 20, 14, 1, 1, 1),
                ("two", 40, 20, 30, 14, 1, 1, 1),
            ]
            if extra:
                words.append(("9.", 10, 60, 20, 14, 2, 1, 1))
                words.append(("nine", 40, 60, 30, 14, 2, 1, 1))
            data = {
                key: []
                for key in ("text", "conf", "block_num", "par_num", "line_num", "left", "top", "width", "height")
            }
            for text, left, top, width, height, block, par, line in words:
                data["text"].append(text)
                data["conf"].append(95)
                data["block_num"].append(block)
                data["par_num"].append(par)
                data["line_num"].append(line)
                data["left"].append(left)
                data["top"].append(top)
                data["width"].append(width)
                data["height"].append(height)
            return data

        image = Image.new("RGB", (120, 120), "white")
        with mock.patch("cropper.page.pytesseract.image_to_data", side_effect=fake_image_to_data):
            lines = ocr_lines(image)

        self.assertEqual([line.text for line in lines], ["2. two", "9. nine"])

    def test_same_question_read_by_two_passes_is_one_start(self) -> None:
        page = PageInfo(
            number=1,
            width=1653,
            height=2339,
            lines=[
                TextLine("27. first read", 210, 1927, 1400, 1955),
                TextLine("27. second read", 214, 1949, 1400, 1977),
            ],
            thumb=Image.new("L", (10, 10), 255),
            thumb_scale=0.1,
            content_top=0.0,
            content_bottom=2258.0,
            columns=[Column(0, 1653, left_margin=200.0)],
        )

        starts = find_question_starts([page])
        self.assertEqual([start.question for start in starts], [27])
        self.assertEqual(starts[0].y0, 1927)

    def test_missing_numbers_are_reported_as_gaps(self) -> None:
        starts = [QuestionStart(number, 1, 0, number * 10.0) for number in (1, 2, 3, 6, 7)]

        self.assertEqual(find_question_gaps(starts), [4, 5])
        self.assertEqual(format_number_ranges([1, 4, 5, 9, 10]), "1, 4-5, 9-10")
        self.assertEqual(format_number_ranges([]), "")

    def test_missing_first_question_is_reported(self) -> None:
        starts = [QuestionStart(number, 1, 0, number * 10.0) for number in (2, 3, 4)]

        self.assertEqual(find_question_gaps(starts), [1])

    def test_later_starting_paper_is_not_reported_as_gaps(self) -> None:
        starts = [QuestionStart(number, 1, 0, number * 10.0) for number in (15, 16, 17)]

        self.assertEqual(find_question_gaps(starts), [])

    def test_complete_paper_has_no_gaps(self) -> None:
        pdf = self.dir / "2019.pdf"
        make_single_column_pdf(pdf)

        # The fixture has four questions; the default expected count is 50.
        analysis = analyze_pdf(pdf, expected_count=4)
        self.assertEqual(analysis.gaps, [])


if __name__ == "__main__":
    unittest.main()
