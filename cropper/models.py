"""Shared data models used by the cropper modules."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

from PIL import Image


@dataclass
class TextLine:
    """A line of text on a page, in rendered-pixel coordinates."""

    text: str
    x0: float
    y0: float
    x1: float
    y1: float

    @property
    def center_x(self) -> float:
        return (self.x0 + self.x1) / 2

    @property
    def width(self) -> float:
        return self.x1 - self.x0


@dataclass
class Column:
    """A column region, in rendered-pixel coordinates."""

    x0: float
    x1: float
    left_margin: float = 0.0


@dataclass
class PageInfo:
    """Everything detected about one page."""

    number: int  # 1-based
    width: int
    height: int
    lines: list[TextLine]
    thumb: Image.Image
    thumb_scale: float
    content_top: float = 0.0
    content_bottom: float = 0.0
    columns: list[Column] = field(default_factory=list)

    @property
    def page_index(self) -> int:
        return self.number - 1


@dataclass
class Box:
    """A crop rectangle in 200-DPI page pixels, with its question number."""

    question: int
    page: int  # 1-based
    x0: int
    y0: int
    x1: int
    y1: int

    @property
    def width(self) -> int:
        return self.x1 - self.x0

    @property
    def height(self) -> int:
        return self.y1 - self.y0

    def to_dict(self) -> dict:
        return {
            "question": self.question,
            "page": self.page,
            "x": self.x0,
            "y": self.y0,
            "w": self.width,
            "h": self.height,
        }

    @classmethod
    def from_xywh(cls, question: int, page: int, x: float, y: float, w: float, h: float) -> "Box":
        x0 = int(round(x))
        y0 = int(round(y))
        return cls(
            question=int(question),
            page=int(page),
            x0=x0,
            y0=y0,
            x1=x0 + int(round(w)),
            y1=y0 + int(round(h)),
        )


@dataclass
class PaperAnalysis:
    """Result of analysing one PDF."""

    path: Path
    dpi: int
    pages: list[PageInfo]
    boxes: list[Box]
    gaps: list[int] = field(default_factory=list)

    @property
    def stem(self) -> str:
        return self.path.stem
