import shutil
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from cropper.crop import question_filename, save_questions
from cropper.deskew import find_page_quad, straighten
from cropper.page import has_text_layer, open_pdf, page_image, render_page, text_lines
from cropper.pipeline import analyze_pdf
from tests.helpers import (
    make_perspective_photo_pdf,
    make_rotated_photo_pdf,
    make_rotated_photo_pdf_with_text_layer,
    make_single_column_pdf,
)


def make_bordered_photo(angle: float = 7.0) -> Image.Image:
    """A page with a thick black frame, photographed at an angle."""
    canvas = Image.new("RGB", (1000, 1400), "white")
    draw = ImageDraw.Draw(canvas)
    draw.rectangle([100, 120, 900, 1280], fill="white", outline=(0, 0, 0), width=14)
    for y in range(220, 1200, 70):
        draw.rectangle([160, y, 840, y + 12], fill=(20, 20, 20))
    return canvas.rotate(
        angle, resample=Image.Resampling.BICUBIC, expand=False, fillcolor="white"
    )


def first_dark_column(image: Image.Image, y: int) -> int | None:
    gray = image.convert("L")
    width = gray.width
    data = gray.tobytes()
    row = data[y * width : (y + 1) * width]
    found = row.find(0)
    if found != -1:
        return found
    for x, value in enumerate(row):
        if value < 110:
            return x
    return None


class StraightenTests(unittest.TestCase):
    def test_rotated_frame_is_found_and_squared_up(self) -> None:
        photo = make_bordered_photo(7.0)
        quad = find_page_quad(photo)
        self.assertIsNotNone(quad)

        straight = straighten(photo)
        self.assertEqual(straight.size, photo.size)

        # The frame's sides now run straight down the page.
        edges = [first_dark_column(straight, y) for y in (400, 700, 1000)]
        self.assertNotIn(None, edges)
        self.assertLessEqual(max(edges) - min(edges), 6)

        # Nothing left to straighten, so a second pass leaves it alone.
        self.assertIsNone(find_page_quad(straight))

    def test_page_without_a_frame_is_left_alone(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            pdf = Path(temp.name) / "2019.pdf"
            make_single_column_pdf(pdf)
            doc = open_pdf(pdf)
            try:
                image = render_page(doc[0], 200)
            finally:
                doc.close()
        finally:
            temp.cleanup()

        self.assertIsNone(find_page_quad(image))
        self.assertIs(straighten(image), image)

    def test_straight_page_is_not_resampled(self) -> None:
        straight_photo = make_bordered_photo(0.0)
        self.assertIsNone(find_page_quad(straight_photo))


class PhotoPipelineTests(unittest.TestCase):
    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_questions_are_detected_through_a_rotated_photo(self) -> None:
        temp = tempfile.TemporaryDirectory()
        try:
            for angle in (6.0, 12.0):
                with self.subTest(angle=angle):
                    pdf = Path(temp.name) / f"photo{angle}.pdf"
                    make_rotated_photo_pdf(pdf, angle=angle)
                    analysis = analyze_pdf(pdf, expected_count=2)
                    page = analysis.pages[0]

                    questions = sorted({box.question for box in analysis.boxes})
                    self.assertIn(1, questions)
                    self.assertIn(2, questions)
                    for box in analysis.boxes:
                        self.assertGreaterEqual(box.y0, 0)
                        self.assertLessEqual(box.y1, page.height)
                        self.assertGreater(box.width, 20)
                        self.assertGreater(box.height, 10)
        finally:
            temp.cleanup()


    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_handheld_photo_on_a_dark_desk_is_straightened_and_read(self) -> None:
        """Perspective, uneven light, a shadow and a dark desk, as a phone sees it."""
        temp = tempfile.TemporaryDirectory()
        try:
            pdf = Path(temp.name) / "desk.pdf"
            make_perspective_photo_pdf(pdf)

            doc = open_pdf(pdf)
            try:
                raw = render_page(doc[0], 200)
                # The paper is crooked and off-square, so there is work to do.
                self.assertIsNotNone(find_page_quad(raw))
                straight = page_image(doc, 0, 200)
            finally:
                doc.close()

            self.assertEqual(straight.size, raw.size)
            rows = [int(straight.height * frac) for frac in (0.2, 0.5, 0.8)]
            edges = [first_dark_column(straight, y) for y in rows]
            self.assertNotIn(None, edges)
            self.assertLessEqual(max(edges) - min(edges), 8)

            analysis = analyze_pdf(pdf, expected_count=2)
            questions = sorted({box.question for box in analysis.boxes})
            self.assertEqual(questions, [1, 2])
        finally:
            temp.cleanup()

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_page_with_a_text_layer_is_not_straightened(self) -> None:
        """Text lines come from the PDF, so the image must not move under them."""
        temp = tempfile.TemporaryDirectory()
        try:
            pdf = Path(temp.name) / "photo-text.pdf"
            make_rotated_photo_pdf_with_text_layer(pdf, angle=6.0)

            doc = open_pdf(pdf)
            try:
                page = doc[0]
                self.assertTrue(has_text_layer(page))
                raw = render_page(page, 200)
                # There is a frame worth straightening, so only the text layer
                # can be the reason it is left alone.
                self.assertIsNotNone(find_page_quad(raw))
                self.assertEqual(page_image(doc, 0, 200).tobytes(), raw.tobytes())
            finally:
                doc.close()

            analysis = analyze_pdf(pdf, expected_count=2)
            page_info = analysis.pages[0]

            doc = open_pdf(pdf)
            try:
                expected = text_lines(doc[0], page_info.width, page_info.height)
            finally:
                doc.close()

            self.assertEqual(len(page_info.lines), len(expected))
            for found, wanted in zip(page_info.lines, expected):
                self.assertAlmostEqual(found.x0, wanted.x0, places=3)
                self.assertAlmostEqual(found.y0, wanted.y0, places=3)
                self.assertAlmostEqual(found.x1, wanted.x1, places=3)
                self.assertAlmostEqual(found.y1, wanted.y1, places=3)
        finally:
            temp.cleanup()

    @unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
    def test_saved_crops_use_the_straightened_page(self) -> None:
        """A PNG is exactly its box, so saving crops the straightened pixels."""
        temp = tempfile.TemporaryDirectory()
        try:
            pdf = Path(temp.name) / "photo.pdf"
            make_rotated_photo_pdf(pdf, angle=10.0)
            analysis = analyze_pdf(pdf, expected_count=2)
            result = save_questions(pdf, analysis.boxes, output_root=Path(temp.name) / "out")

            for box in analysis.boxes:
                path = result.output_dir / question_filename(pdf.stem, box.question)
                with Image.open(path) as crop:
                    self.assertEqual(crop.size, (box.width, box.height))
        finally:
            temp.cleanup()


if __name__ == "__main__":
    unittest.main()
