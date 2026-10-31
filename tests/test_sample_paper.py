import shutil
import unittest
from itertools import combinations
from pathlib import Path

from cropper.edges import PageInk
from cropper.page import ink_threshold, open_pdf, render_page
from cropper.pipeline import analyze_pdf

SAMPLE = Path(__file__).resolve().parent.parent / "test_pdf" / "EM_2024_Physics_MCQ.pdf"


def _ink_mask(gray) -> "object":
    """Dark pixels only, using the same adaptive threshold as the cropper."""
    threshold = ink_threshold(gray)
    return gray.point(lambda value: 255 if value < threshold else 0)


@unittest.skipUnless(SAMPLE.is_file(), "sample paper test_pdf/EM_2024_Physics_MCQ.pdf not present")
@unittest.skipUnless(shutil.which("tesseract"), "Tesseract is not installed")
class SamplePaperTests(unittest.TestCase):
    """The scanned A/L paper whose question numbers OCR used to drop."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.analysis = analyze_pdf(SAMPLE, expected_count=50)
        cls.grays: dict[int, object] = {}
        cls.masks: dict[int, object] = {}
        cls.inks: dict[int, PageInk] = {}
        doc = open_pdf(SAMPLE)
        try:
            for page_number in range(1, doc.page_count + 1):
                gray = render_page(doc[page_number - 1], cls.analysis.dpi).convert("L")
                cls.grays[page_number] = gray
                cls.masks[page_number] = _ink_mask(gray)
                cls.inks[page_number] = PageInk(gray)
        finally:
            doc.close()

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

    def test_no_crop_edge_cuts_a_text_line(self) -> None:
        for box in self.analysis.boxes:
            ink = self.inks[box.page]
            mask = self.masks[box.page]
            # The page frame is not text: look only inside it.
            left = max(box.x0, ink.frame_left)
            right = min(box.x1, ink.frame_right)
            self.assertGreater(right - left, 20, f"Q{box.question} has no text inside the frame")
            for start, end in self._bands(
                mask, box.page, left, right, ink.frame_top, ink.frame_bottom
            ):
                inside = start >= box.y0 and end < box.y1
                outside = end < box.y0 or start >= box.y1
                self.assertTrue(
                    inside or outside,
                    f"Q{box.question} (page {box.page}): text rows {start}-{end} are "
                    f"cut by an edge of {box.y0}-{box.y1}",
                )

    def test_boxes_on_a_page_do_not_overlap(self) -> None:
        for first, second in combinations(self.analysis.boxes, 2):
            if first.page != second.page:
                continue
            if first.x1 <= second.x0 or second.x1 <= first.x0:
                continue
            self.assertTrue(
                first.y1 <= second.y0 or second.y1 <= first.y0,
                f"Q{first.question} and Q{second.question} overlap on page {first.page}",
            )

    def test_no_text_is_left_between_neighbouring_boxes(self) -> None:
        for page_number, mask in self.masks.items():
            ink = self.inks[page_number]
            boxes = sorted(
                (box for box in self.analysis.boxes if box.page == page_number),
                key=lambda box: box.y0,
            )
            for first, second in zip(boxes, boxes[1:]):
                left = max(first.x0, second.x0, ink.frame_left)
                right = min(first.x1, second.x1, ink.frame_right)
                if right - left <= 0 or second.y0 <= first.y1:
                    continue
                # Only text counts; the page rules and scan speckle are not text.
                leftover = self._bands(
                    mask, page_number, left, right, first.y1, second.y0
                )
                self.assertEqual(
                    leftover,
                    [],
                    f"text rows {leftover} were left between Q{first.question} and "
                    f"Q{second.question} on page {page_number}",
                )

    def test_no_crop_includes_the_page_frame(self) -> None:
        for box in self.analysis.boxes:
            ink = self.inks[box.page]
            self.assertGreaterEqual(
                box.y0, ink.frame_top, f"Q{box.question} starts above the frame"
            )
            self.assertLessEqual(
                box.y1, ink.frame_bottom, f"Q{box.question} reaches the frame bottom"
            )

    def test_no_frame_border_at_a_crop_edge(self) -> None:
        for box in self.analysis.boxes:
            crop = self.masks[box.page].crop((box.x0, box.y0, box.x1, box.y1))
            data = crop.tobytes()
            width, height = crop.size
            if width < 20 or height < 20:
                continue
            edge = range(8)
            for x in list(edge) + [width - 1 - offset for offset in edge]:
                coverage = data[x::width].count(255)
                self.assertLess(
                    coverage,
                    0.9 * height,
                    f"Q{box.question} (page {box.page}) has the frame border at x={box.x0 + x}",
                )

    def _bands(
        self, mask, page_number: int, x0: int, x1: int, y0: int, y1: int
    ) -> list[tuple[int, int]]:
        """Ink bands inside the page frame, so frame rules are not read as text."""
        key = (page_number, x0, x1, y0, y1)
        cached = getattr(self, "_band_cache", None)
        if cached is None:
            cached = self._band_cache = {}
        if key in cached:
            return cached[key]
        data = mask.crop((x0, y0, x1, y1)).tobytes()
        width = x1 - x0
        # Text ink is part of a stroke; isolated scanner speckle is not.
        min_ink = max(8, int(width * 0.015))
        bands: list[tuple[int, int]] = []
        start = None
        for row in range(y1 - y0):
            cells = data[row * width : (row + 1) * width]
            strokes = sum(len(part) for part in cells.split(b"\x00") if len(part) >= 3)
            has_ink = strokes >= min_ink
            if has_ink and start is None:
                start = row + y0
            elif not has_ink and start is not None:
                bands.append((start, row + y0 - 1))
                start = None
        if start is not None:
            bands.append((start, y1 - 1))
        cached[key] = bands
        return bands


if __name__ == "__main__":
    unittest.main()
