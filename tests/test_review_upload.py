import io
import json
import socket
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import zipfile
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

    def test_save_recrops_from_the_posted_boxes_and_keeps_them(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]

        box = {"page": 1, "x": 120, "y": 140, "w": 520, "h": 260, "question": 1, "review": []}
        save_status, _, save_body = self.request(
            "POST",
            f"/api/papers/{paper_id}/save",
            body=json.dumps({"boxes": [box]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(save_status, 200, save_body.decode(errors="replace"))
        self.assertEqual(json.loads(save_body)["revision"], 1)

        from PIL import Image

        with Image.open(self.output_dir / "2019" / "2019_q01.png") as crop:
            self.assertEqual(crop.size, (520, 260))

        # Reloading the page returns the boxes that were saved, not the ones
        # detection picked, so a refresh cannot silently undo the recrop.
        reload_status, _, reload_body = self.request("GET", f"/api/papers/{paper_id}?expected=50")
        self.assertEqual(reload_status, 200)
        reloaded = json.loads(reload_body)
        self.assertEqual(reloaded["revision"], 1)
        self.assertEqual(len(reloaded["boxes"]), 1)
        self.assertEqual(reloaded["boxes"][0]["x"], 120)
        self.assertEqual(reloaded["boxes"][0]["y"], 140)
        self.assertEqual(reloaded["boxes"][0]["w"], 520)
        self.assertEqual(reloaded["boxes"][0]["h"], 260)

        again_status, _, again_body = self.request(
            "POST",
            f"/api/papers/{paper_id}/save",
            body=json.dumps({"boxes": [box]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(again_status, 200, again_body.decode(errors="replace"))
        self.assertEqual(json.loads(again_body)["revision"], 2)

    def test_page_and_assets_are_not_cached(self) -> None:
        # A cached page would keep an old editor and buttons that no longer match
        # the running code.
        for path in ("/", "/static/app.js", "/static/style.css"):
            status, headers, _ = self.request("GET", path)
            self.assertEqual(status, 200, path)
            cache = next(
                (value for key, value in headers.items() if key.lower() == "cache-control"), ""
            )
            self.assertEqual(cache, "no-store", f"{path} sent Cache-Control: {cache!r}")

    def test_save_clamps_an_edge_that_rounds_past_the_page(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        data = json.loads(content)
        page = data["pages"][0]

        # Dragging to the page edge can round a point past it; that must crop to
        # the edge instead of failing every other box in the save.
        box = {
            "page": 1,
            "x": page["width"] - 40,
            "y": page["height"] - 30,
            "w": 60,
            "h": 60,
            "question": 7,
            "review": [],
        }
        save_status, _, save_body = self.request(
            "POST",
            f"/api/papers/{data['id']}/save",
            body=json.dumps({"boxes": [box]}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(save_status, 200, save_body.decode(errors="replace"))

        from PIL import Image

        with Image.open(self.output_dir / "2019" / "2019_q07.png") as crop:
            self.assertEqual(crop.size, (40, 30))

    def test_save_writes_crops_around_a_locked_old_output(self) -> None:
        # Windows will not delete a file another program holds open. The save
        # used to delete every old crop first, so one locked PNG aborted the
        # recrop and the crops on disk never changed.
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]
        stale_crop = self.output_dir / "2019" / "2019_q02.png"
        self.assertTrue(stale_crop.is_file())

        box = {"page": 1, "x": 50, "y": 60, "w": 300, "h": 200, "question": 1, "review": []}
        with stale_crop.open("rb"):
            save_status, _, save_body = self.request(
                "POST",
                f"/api/papers/{paper_id}/save",
                body=json.dumps({"boxes": [box]}).encode(),
                headers={"Content-Type": "application/json"},
            )
            self.assertEqual(save_status, 200, save_body.decode(errors="replace"))

        from PIL import Image

        with Image.open(self.output_dir / "2019" / "2019_q01.png") as crop:
            self.assertEqual(crop.size, (300, 200))

    def test_download_all_returns_every_png_and_the_manifest(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]

        zip_status, zip_headers, zip_body = self.request(
            "GET", f"/outputs/{paper_id}/download.zip"
        )
        self.assertEqual(zip_status, 200)
        content_type = next(
            value for key, value in zip_headers.items() if key.lower() == "content-type"
        )
        self.assertEqual(content_type, "application/zip")
        with zipfile.ZipFile(io.BytesIO(zip_body)) as archive:
            self.assertEqual(
                sorted(archive.namelist()),
                [
                    "2019_q01.png",
                    "2019_q02.png",
                    "2019_q03.png",
                    "2019_q04.png",
                    "manifest.json",
                ],
            )

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
        # The declared length is rejected before the body is read, so the server
        # closes the connection while a client is still sending. Reading that as
        # a urllib response sometimes raises a reset instead of the 413, so talk
        # to the socket directly and assert on the raw response.
        request = (
            "POST /api/upload?filename=large.pdf HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{self.port}\r\n"
            "Content-Type: application/pdf\r\n"
            f"Content-Length: {100 * 1024 * 1024 + 1}\r\n"
            "\r\n"
        ).encode()
        with socket.create_connection(("127.0.0.1", self.port), timeout=10) as sock:
            sock.sendall(request)
            response = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                response += chunk
        self.assertIn(b" 413 ", response, response[:200])

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
        # The review tools: flagged list with a count, next flagged, snap toggle.
        self.assertIn('id="review-panel"', page)
        self.assertIn('id="review-count"', page)
        self.assertIn('id="review-list"', page)
        self.assertIn('id="next-flagged"', page)
        self.assertIn('id="snap-toggle"', page)
        # Recropping from the reader's boxes and downloading them all.
        self.assertIn('id="recrop-button"', page)
        self.assertIn('id="download-all"', page)
        self.assertIn('id="delete-paper"', page)
        self.assertIn('id="clear-project"', page)

    def test_delete_paper_removes_review_files_and_outputs(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]
        paper = self.session.papers[paper_id]
        preview_dir = paper.preview_dir
        upload_path = paper.path
        output_dir = self.output_dir / "2019"
        self.assertTrue(preview_dir.is_dir())
        self.assertTrue(upload_path.is_file())
        self.assertTrue(output_dir.is_dir())

        delete_status, _, delete_body = self.request("DELETE", f"/api/papers/{paper_id}")
        self.assertEqual(delete_status, 200, delete_body.decode(errors="replace"))
        self.assertEqual(json.loads(delete_body)["papers"], [])
        self.assertFalse(preview_dir.exists())
        self.assertFalse(upload_path.exists())
        self.assertFalse(output_dir.exists())

    def test_clear_project_removes_all_papers_and_outputs(self) -> None:
        for filename in ("first.pdf", "second.pdf"):
            status, _, content = self.request(
                "POST",
                f"/api/upload?filename={filename}",
                body=self.pdf_path.read_bytes(),
                headers={"Content-Type": "application/pdf"},
            )
            self.assertEqual(status, 200, content.decode(errors="replace"))

        clear_status, _, clear_body = self.request("POST", "/api/clear")
        self.assertEqual(clear_status, 200, clear_body.decode(errors="replace"))
        self.assertEqual(json.loads(clear_body)["papers"], [])
        self.assertEqual(self.session.papers, [])
        self.assertFalse((self.output_dir / "first").exists())
        self.assertFalse((self.output_dir / "second").exists())

    def test_snap_puts_a_dragged_edge_on_a_blank_row(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]

        body = json.dumps({"page": 1, "x": 100, "y": 300, "w": 800, "h": 200}).encode()
        snap_status, _, snap_body = self.request(
            "POST",
            f"/api/papers/{paper_id}/snap",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(snap_status, 200, snap_body.decode(errors="replace"))
        snapped = json.loads(snap_body)
        self.assertEqual(snapped["x"], 100)
        self.assertEqual(snapped["w"], 800)
        self.assertGreaterEqual(snapped["h"], 20)

        from cropper.edges import PageInk
        from cropper.page import open_pdf, render_page

        doc = open_pdf(self.pdf_path)
        try:
            ink = PageInk(render_page(doc[0], 200))
        finally:
            doc.close()
        left, right = snapped["x"], snapped["x"] + snapped["w"]
        self.assertTrue(
            ink.row_is_blank(snapped["y"], left, right),
            "top edge is still on a row with ink",
        )
        bottom_row = min(snapped["y"] + snapped["h"], ink.height - 1)
        self.assertTrue(
            ink.row_is_blank(bottom_row, left, right),
            "bottom edge is still on a row with ink",
        )

        # Snapping an already snapped box changes nothing.
        again_status, _, again_body = self.request(
            "POST",
            f"/api/papers/{paper_id}/snap",
            body=json.dumps({**snapped, "page": 1}).encode(),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(again_status, 200)
        self.assertEqual(json.loads(again_body), snapped)

    def test_snap_rejects_an_unknown_page(self) -> None:
        status, _, content = self.request(
            "POST",
            "/api/upload?filename=2019.pdf",
            body=self.pdf_path.read_bytes(),
            headers={"Content-Type": "application/pdf"},
        )
        self.assertEqual(status, 200, content.decode(errors="replace"))
        paper_id = json.loads(content)["id"]
        body = json.dumps({"page": 99, "x": 10, "y": 10, "w": 100, "h": 100}).encode()
        snap_status, _, _ = self.request(
            "POST",
            f"/api/papers/{paper_id}/snap",
            body=body,
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(snap_status, 404)


if __name__ == "__main__":
    unittest.main()
