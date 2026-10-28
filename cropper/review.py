"""Local FastAPI server for reviewing and fixing detected boxes."""

from __future__ import annotations

import atexit
import shutil
import tempfile
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .crop import save_questions
from .models import Box, PaperAnalysis
from .page import CropperError, open_pdf
from .pipeline import analyze_pdf

WEB_DIR = Path(__file__).resolve().parent / "web"
OUTPUT_ROOT = Path("output")


class BoxPayload(BaseModel):
    page: int
    x: float
    y: float
    w: float
    h: float
    question: int = Field(ge=1)


class SavePayload(BaseModel):
    boxes: list[BoxPayload]


class ReviewPaper:
    def __init__(self, paper_id: int, path: Path, preview_dir: Path) -> None:
        self.id = paper_id
        self.path = path
        self.preview_dir = preview_dir
        self.analysis: PaperAnalysis | None = None
        self._lock = threading.Lock()
        doc = open_pdf(path)
        try:
            self.page_count = doc.page_count
        finally:
            doc.close()

    @property
    def name(self) -> str:
        return self.path.name

    def load(self) -> PaperAnalysis:
        if self.analysis is not None:
            return self.analysis
        with self._lock:
            if self.analysis is None:
                self.preview_dir.mkdir(parents=True, exist_ok=True)
                self.analysis = analyze_pdf(self.path, preview_dir=self.preview_dir)
            return self.analysis

    def preview_path(self, page_number: int) -> Path:
        return self.preview_dir / f"{page_number:03d}.jpg"


class ReviewSession:
    def __init__(self, pdfs: list[Path]) -> None:
        self.temp_dir = Path(tempfile.mkdtemp(prefix="question-cropper-review-"))
        atexit.register(self.cleanup)
        self.papers = [
            ReviewPaper(index, path, self.temp_dir / str(index)) for index, path in enumerate(pdfs)
        ]

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

    @app.get("/api/papers")
    def list_papers() -> list[dict]:
        return [
            {"id": paper.id, "name": paper.name, "page_count": paper.page_count}
            for paper in session.papers
        ]

    @app.get("/api/papers/{paper_id}")
    def get_paper(paper_id: int) -> dict:
        paper = session.get(paper_id)
        try:
            analysis = paper.load()
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
            boxes.append(Box(item.question, item.page, x0, y0, x1, y1))

        try:
            result = save_questions(paper.path, boxes, output_root=OUTPUT_ROOT)
        except (CropperError, ValueError, OSError) as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        return {
            "files": [path.name for path in result.files],
            "manifest": result.manifest_path.name,
            "output_dir": str(result.output_dir.resolve()),
        }

    return app
