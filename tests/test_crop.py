import json
import tempfile
import unittest
from pathlib import Path

from PIL import Image, ImageDraw

from cropper.crop import join_crops, save_questions, trim_segment
from cropper.detect import Segment
from cropper.models import Box, Column, PageInfo
from tests.helpers import make_single_column_pdf


class TrimAndJoinTests(unittest.TestCase):
    def test_trim_removes_white_space_and_adds_padding(self) -> None:
        image = Image.new("RGB", (400, 300), "white")
        draw = ImageDraw.Draw(image)
        draw.rectangle((100, 50, 149, 89), fill="black")

        page = PageInfo(
            number=1,
            width=400,
            height=300,
            lines=[],
            thumb=Image.new("L", (100, 75), "white"),
            thumb_scale=0.25,
            content_top=0,
            content_bottom=300,
            columns=[Column(0, 400)],
        )
        segment = Segment(question=1, page=1, column=0, x0=0, y0=0, x1=400, y1=300)
        box = trim_segment(image, segment, page, padding=10)

        self.assertIsNotNone(box)
        assert box is not None
        self.assertEqual((box.x0, box.y0, box.x1, box.y1), (90, 40, 160, 100))

    def test_join_stacks_with_a_gap_and_shared_width(self) -> None:
        first = Image.new("RGB", (100, 40), "white")
        second = Image.new("RGB", (80, 30), "white")
        joined = join_crops([first, second], gap=20)
        self.assertEqual(joined.size, (100, 90))

    def test_join_single_crop_returns_it(self) -> None:
        image = Image.new("RGB", (100, 40), "white")
        self.assertEqual(join_crops([image]).size, (100, 40))


class SaveTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.dir = Path(self.temp.name)
        self.pdf = self.dir / "2019.pdf"
        make_single_column_pdf(self.pdf)
        self.output = self.dir / "output"

    def tearDown(self) -> None:
        self.temp.cleanup()

    def test_save_writes_padded_names_manifest_and_joined_image(self) -> None:
        boxes = [
            Box(1, 1, 120, 200, 700, 500),
            Box(2, 1, 120, 250, 700, 500),
            Box(2, 2, 120, 100, 700, 300),
        ]
        result = save_questions(self.pdf, boxes, output_root=self.output)

        self.assertEqual(result.files, [self.output / "2019" / "2019_q01.png", self.output / "2019" / "2019_q02.png"])
        self.assertTrue((self.output / "2019" / "2019_q01.png").is_file())

        with Image.open(self.output / "2019" / "2019_q02.png") as joined:
            self.assertEqual(joined.size, (580, 470))  # 250px + 20px gap + 200px

        manifest = json.loads((self.output / "2019" / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["pdf"], "2019.pdf")
        self.assertEqual(manifest["dpi"], 200)
        self.assertEqual([item["question"] for item in manifest["questions"]], [1, 2])
        second = manifest["questions"][1]
        self.assertEqual(second["file"], "2019_q02.png")
        self.assertEqual(second["page"], 1)
        self.assertEqual(second["box"], [120, 250, 700, 500])
        self.assertEqual(second["segments"], [
            {"page": 1, "box": [120, 250, 700, 500]},
            {"page": 2, "box": [120, 100, 700, 300]},
        ])

    def test_resaving_removes_stale_question_images(self) -> None:
        save_questions(
            self.pdf,
            [Box(1, 1, 120, 200, 700, 500), Box(2, 1, 120, 250, 700, 500)],
            output_root=self.output,
        )
        self.assertTrue((self.output / "2019" / "2019_q02.png").exists())

        save_questions(self.pdf, [Box(1, 1, 120, 200, 700, 500)], output_root=self.output)
        self.assertFalse((self.output / "2019" / "2019_q02.png").exists())

    def test_save_rejects_box_outside_page(self) -> None:
        with self.assertRaises(ValueError):
            save_questions(self.pdf, [Box(1, 1, 0, 0, 5000, 500)], output_root=self.output)


if __name__ == "__main__":
    unittest.main()
