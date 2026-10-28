import shutil
import tempfile
import unittest
from pathlib import Path

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


if __name__ == "__main__":
    unittest.main()
