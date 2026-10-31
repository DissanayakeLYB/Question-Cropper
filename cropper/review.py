"""Local FastAPI server for reviewing and fixing detected boxes."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .crop import save_questions
from .models import Box, PaperAnalysis
from .page import CropperError, open_pdf
from .pipeline import analyze_pdf

WEB_DIR = Path(__file__).resolve().parent / "web"
OUTPUT_ROOT = Path("output")
MAX_UPLOAD_BYTES = 100 * 1024 * 1024


class BoxPayload(BaseModel):
    page: int
    x: float
    y: float
    w: float
    h: float
    question: int = Field(ge=1)
    # Reasons the box was flagged on the page; kept so the manifest can record
    # them even when the boxes come from the review page rather than detection.
    review: list[str] = Field(default_factory=list)


class SavePayload(BaseModel):
    boxes: list[BoxPayload]


class ReviewPaper:
    def __init__(
        self,
        paper_id: int,
        path: Path,
        preview_dir: Path,
        display_name: str | None = None,
        expected_count: int = 50,
    ) -> None:
        self.id = paper_id
        self.expected_count = expected_count
        self.path = path
        self.preview_dir = preview_dir
        self.display_name = display_name or path.name
        self.analysis: PaperAnalysis | None = None
        self.saved_files: list[str] = []
        self.saved_manifest = False
        self._lock = threading.Lock()
        doc = open_pdf(path)
        try:
            self.page_count = doc.page_count
        finally:
            doc.close()

    @property
    def name(self) -> str:
        return self.display_name

    def load(self, expected_count: int | None = None) -> PaperAnalysis:
        target = self.expected_count if expected_count is None else expected_count
        with self._lock:
            if self.analysis is None or self.analysis.expected != target:
                self.preview_dir.mkdir(parents=True, exist_ok=True)
                self.analysis = analyze_pdf(
                    self.path, preview_dir=self.preview_dir, expected_count=target
                )
            self.expected_count = target
            return self.analysis

    def preview_path(self, page_number: int) -> Path:
        return self.preview_dir / f"{page_number:03d}.jpg"


class ReviewSession:
    def __init__(self, pdfs: list[Path], expected_count: int = 50) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="question-cropper-review-"))
        atexit.register(self.cleanup)
        self.expected_count = expected_count
        self.papers = [
            ReviewPaper(index, path, self.temp_dir / str(index), expected_count=expected_count)
            for index, path in enumerate(pdfs)
        ]

    def add_pdf(
        self, path: Path, display_name: str | None = None, expected_count: int | None = None
    ) -> ReviewPaper:
        paper = ReviewPaper(
            len(self.papers),
            path,
            self.temp_dir / str(len(self.papers)),
            display_name,
            expected_count or self.expected_count,
        )
        self.papers.append(paper)
        return paper

    def get(self, paper_id: int) -> ReviewPaper:
        if not 0 <= paper_id < len(self.papers):
            raise HTTPException(status_code=404, detail="Unknown paper.")
        return self.papers[paper_id]

    def cleanup(self) -> None:
        shutil.rmtree(self.temp_dir, ignore_errors=True)


def create_app(session: ReviewSession) -> FastAPI:
    app = FastAPI(title="Question Cropper", docs_url=None, redoc_url=None)
    app.state.session = session
    app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(WEB_DIR / "index.html")

    @app.post("/api/upload")
    async def upload_pdf(
        request: Request, filename: str, expected: int = Query(50, ge=1, le=200)
    ) -> dict:
        safe_name = Path(filename.replace("\\", "/")).name
        safe_name = "".join(char for char in safe_name if char.isprintable() and char not in '<>:"|?*')
        if not safe_name or Path(safe_name).suffix.lower() != ".pdf":
            raise HTTPException(status_code=400, detail="Choose a PDF file to upload.")

        content_length = request.headers.get("content-length")
        if content_length:
            try:
                if int(content_length) > MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="PDF is larger than the 100 MB upload limit.")
            except ValueError:
                raise HTTPException(status_code=400, detail="Invalid upload size.") from None

        upload_dir = session.temp_dir / "uploads" / uuid.uuid4().hex
        upload_dir.mkdir(parents=True, exist_ok=True)
        pdf_path = upload_dir / safe_name
        size = 0
        paper = None
        try:
            with pdf_path.open("wb") as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > MAX_UPLOAD_BYTES:
                        raise HTTPException(status_code=413, detail="PDF is larger than the 100 MB upload limit.")
                    output.write(chunk)

            try:
                paper = session.add_pdf(pdf_path, safe_name, expected)
                analysis = paper.load()
                if analysis.boxes:
                    result = save_questions(pdf_path, analysis.boxes, output_root=OUTPUT_ROOT)
                    paper.saved_files = [path.name for path in result.files]
                    paper.saved_manifest = True
            except Exception as exc:
                if paper is not None and paper in session.papers:
                    session.papers.remove(paper)
                raise HTTPException(status_code=400, detail=f"Could not process PDF: {exc}") from exc

            return {
                "id": paper.id,
                "name": paper.name,
                "page_count": paper.page_count,
                "output": str((OUTPUT_ROOT / paper.path.stem).resolve()),
                "pages": [
                    {"number": page.number, "width": page.width, "height": page.height,
                     "url": f"/pages/{paper.id}/{page.number}.jpg"}
                    for page in analysis.pages
                ],
                "boxes": [box.to_dict() for box in analysis.boxes],
                "gaps": analysis.gaps,
                "expected": analysis.expected,
                "found": len({box.question for box in analysis.boxes}),
                "files": paper.saved_files,
                "manifest": "manifest.json" if paper.saved_manifest else None,
            }
        except HTTPException:
            if paper is not None and paper in session.papers:
                session.papers.remove(paper)
            raise
        except Exception as exc:
            if paper is not None and paper in session.papers:
                session.papers.remove(paper)
            raise HTTPException(status_code=400, detail=f"Could not process PDF: {exc}") from exc

    @app.get("/api/papers")
    def list_papers() -> list[dict]:
        return [
            {"id": paper.id, "name": paper.name, "page_count": paper.page_count}
            for paper in session.papers
        ]

    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: int, expected: int = Query(50, ge=1, le=200)) -> dict:
        paper = session.get(paper_id)
        try:
            analysis = paper.load(expected)
        except CropperError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"Could not read PDF: {exc}") from exc

        return {
            "id": paper.id,
            "name": paper.name,
            "page_count": paper.page_count,
            "output": str((OUTPUT_ROOT / paper.path.stem).resolve()),
            "pages": [
                {
                    "number": page.number,
                    "width": page.width,
                    "height": page.height,
                    "url": f"/pages/{paper.id}/{page.number}.jpg",
                }
                for page in analysis.pages
            ],
            "boxes": [box.to_dict() for box in analysis.boxes],
            "gaps": analysis.gaps,
            "expected": analysis.expected,
            "found": len({box.question for box in analysis.boxes}),
            "files": paper.saved_files,
            "manifest": "manifest.json" if paper.saved_manifest else None,
        }

    @app.get("/pages/{paper_id}/{page_number}.jpg")
    def get_page_image(paper_id: int, page_number: int) -> FileResponse:
        paper = session.get(paper_id)
        try:
            paper.load()
        except CropperError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if not 1 <= page_number <= paper.page_count:
            raise HTTPException(status_code=404, detail="Unknown page.")
        path = paper.preview_path(page_number)
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Page preview not found.")
        return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "public, max-age=3600"})

    @app.post("/api/papers/{paper_id}/save")
    def save(paper_id: int, payload: SavePayload) -> dict:
        paper = session.get(paper_id)
        try:
            analysis = paper.load()
        except CropperError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not payload.boxes:
            raise HTTPException(status_code=400, detail="Add at least one box before saving.")

        page_sizes = {page.number: (page.width, page.height) for page in analysis.pages}
        boxes: list[Box] = []
        for item in payload.boxes:
            if item.page not in page_sizes:
                raise HTTPException(status_code=400, detail=f"Page {item.page} does not exist.")
            width, height = page_sizes[item.page]
            x0 = int(round(item.x))
            y0 = int(round(item.y))
            x1 = x0 + int(round(item.w))
            y1 = y0 + int(round(item.h))
            if x0 < 0 or y0 < 0 or x1 > width or y1 > height:
                raise HTTPException(
                    status_code=400,
                    detail=f"Question {item.question}: box is outside page {item.page}.",
                )
            if x1 - x0 < 2 or y1 - y0 < 2:
                raise HTTPException(
                    status_code=400, detail=f"Question {item.question}: box is too small."
                )
            boxes.append(Box(item.question, item.page, x0, y0, x1, y1, list(item.review)))

        try:
            result = save_questions(paper.path, boxes, output_root=OUTPUT_ROOT)
        except (CropperError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        paper.saved_files = [path.name for path in result.files]
        paper.saved_manifest = True
        return {
            "files": paper.saved_files,
            "manifest": result.manifest_path.name,
            "output_dir": str(result.output_dir.resolve()),
        }

    @app.get("/outputs/{paper_id}/{filename}")
    def download_output(paper_id: int, filename: str) -> FileResponse:
        paper = session.get(paper_id)
        if filename == "manifest.json":
            allowed = paper.saved_manifest
        else:
            allowed = filename in paper.saved_files and Path(filename).name == filename
        if not allowed:
            raise HTTPException(status_code=404, detail="Output file not found.")
        path = OUTPUT_ROOT / paper.path.stem / filename
        if not path.is_file():
            raise HTTPException(status_code=404, detail="Output file not found.")
        media_type = "image/png" if path.suffix.lower() == ".png" else "application/json"
        return FileResponse(path, media_type=media_type)

    return app
