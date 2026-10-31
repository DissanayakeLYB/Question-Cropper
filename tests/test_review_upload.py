import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

import uvicorn

from cropper.review import ReviewSession, create_app
from tests.helpers import make_single_column_pdf


class UploadUiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.pdf_path = self.directory / "2019.pdf"
        make_single_column_pdf(self.pdf_path)
        self.session = ReviewSession([])
        self.app = create_app(self.session)
        self.output_dir = self.directory / "output"
        import cropper.review as review

        self.original_output_root = review.OUTPUT_ROOT
        review.OUTPUT_ROOT = self.output_dir

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind(("127.0.0.1", 0))
            self.port = sock.getsockname()[1]
        self.server = uvicorn.Server(
            uvicorn.Config(self.app, host="127.0.0.1", port=self.port, log_level="error")
        )
        self.thread = threading.Thread(target=self.server.run, daemon=True)
        self.thread.start()
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                self.request("GET", "/")
                break
            except urllib.error.URLError:
                time.sleep(0.05)
        else:
            self.fail("Local review server did not start.")

    def tearDown(self) -> None:
        import cropper.review as review

        self.server.should_exit = True
        self.thread.join(timeout=5)
        review.OUTPUT_ROOT = self.original_output_root
        self.session.cleanup()
        self.temp.cleanup()

    def request(self, method: str, path: str, body: bytes = b"", headers: dict[str, str] | None = None):
        request = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=body if method != "GET" else None,
            headers=headers or {},
            method=method,
        )
        try:
            with urllib.request.urlopen(request, timeout=20) as response:
                return response.status, dict(response.headers), response.read()
        except urllib.error.HTTPError as response:
            try:
                return response.code, dict(response.headers), response.read()
            finally:
                response.close()

    def test_upload_runs_crop_and_returns_questions(self) -> None:
        status, headers, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )

        self.assertEqual(status, 200, content.decode(errors="replace"))
        data = json.loads(content)
        self.assertEqual(data["name"], "2019.pdf")
        self.assertEqual(data["page_count"], 2)
        self.assertEqual(sorted({box["question"] for box in data["boxes"]}), [1, 2, 3, 4])
        self.assertEqual(data["files"], ["2019_q01.png", "2019_q02.png", "2019_q03.png", "2019_q04.png"])
        self.assertEqual(data["manifest"], "manifest.json")
        self.assertTrue((self.output_dir / "2019" / "2019_q02.png").is_file())

        manifest_status, _, manifest_body = self.request(
            "GET", f"/outputs/{data['id']}/manifest.json"
        )
        self.assertEqual(manifest_status, 200)
        manifest = json.loads(manifest_body)
        self.assertEqual([entry["question"] for entry in manifest["questions"]], [1, 2, 3, 4])

        image_status, image_headers, image_body = self.request(
            "GET", f"/outputs/{data['id']}/2019_q01.png"
        )
        self.assertEqual(image_status, 200)
        content_type = next(value for key, value in image_headers.items() if key.lower() == "content-type")
        self.assertTrue(content_type.startswith("image/png"))
        self.assertTrue(image_body.startswith(b"\x89PNG"))

    def test_save_keeps_review_reasons_in_the_manifest(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]

        reason = "more than twice the median height (300 px against 100 px)"
        payload = {
            "boxes": [
                {
                    "page": 1,
                    "x": 100,
                    "y": 100,
                    "w": 600,
                    "h": 300,
                    "question": 1,
                    "review": [reason],
                }
            ]
        }
        save_status, _, save_body = self.request(
            "POST",
            f"/api/papers/{paper_id}/save",
            body=json.dumps(payload).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(save_status, 200, save_body.decode(errors="replace"))

        manifest = json.loads((self.output_dir / "2019" / "manifest.json").read_text(encoding="utf-8"))
        question = manifest["questions"][0]
        self.assertEqual(question["review"], [reason])
        self.assertEqual(question["segments"][0]["review"], [reason])

    def test_upload_rejects_non_pdf_and_invalid_pdf(self) -> None:
        wrong_status, _, _ = self.request(
            "POST",
            "/api/upload?filename=notes.txt",
            body=b"not a pdf",
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(wrong_status, 400)

        invalid_status, _, _ = self.request(
            "POST",
            "/api/upload?filename=bad.pdf",
            body=b"not a pdf",
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(invalid_status, 400)
        self.assertEqual(len(self.session.papers), 0)

    def test_upload_rejects_over_limit_content_length(self) -> None:
        status, _, _ = self.request(
            "POST",
            "/api/upload?filename=large.pdf",
            body=b"%PDF",
            headers={
                "Content-Type": "application/pdf",
                "Content-Length": str(100 * 1024 * 1024 + 1),
            },
        )
        self.assertEqual(status, 413)

    def test_page_contains_upload_first_interface(self) -> None:
        status, _, body = self.request("GET", "/")
        page = body.decode()
        self.assertEqual(status, 200)
        self.assertIn('id="upload-input"', page)
        self.assertIn('id="welcome-upload"', page)
        self.assertIn("Crop questions from a past paper", page)
        self.assertIn('id="toolbar" class="bar toolbar hidden"', page)
        self.assertIn('id="save-button" class="btn primary" type="button" disabled', page)
        self.assertIn('id="expected-input"', page)
        self.assertIn('id="detection-summary"', page)


if __name__ == "__main__":
    unittest.main()
