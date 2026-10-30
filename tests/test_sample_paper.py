import shutil
import unittest
from pathlib import Path

from cropper.pipeline import analyze_pdf

SAMPLE = Path(__file__).resolve().parent.parent / "test_pdf" / "EM_2024_Physics_MCQ.pdf"


@unittest.skipUnless(SAMPLE.is_file(), "sample paper test_pdf/EM_2024_Physics_MCQ.pdf not present")
@unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
class SamplePaperTests(unittest.TestCase):
    """The scanned A/L paper whose question numbers OCR used to drop."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis = analyze_pdf(SAMPLE, expected_count=50)

    def test_questions_5_6_and_14_are_found(self) -> None:
        found = {box.question for box in self.analysis.boxes}

        for number in (5, 6, 14):
            self.assertIn(number, found, f"Q{number} was not found on the sample paper")

    def test_every_question_number_is_found(self) -> None:
        found = {box.question for box in self.analysis.boxes}

        self.assertEqual(
            [number for number in range(1, 51) if number not in found],
            [],
            "some question numbers were not detected",
        )
        self.assertEqual(self.analysis.gaps, [])


if __name__ == "__main__":
    unittest.main()
