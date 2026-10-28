"""Synthetic PDF fixtures for the tests."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image

PAGE_WIDTH = 595
PAGE_HEIGHT = 842
MARGIN = 52


def _wrap(text: str, max_width: float, fontsize: int) -> list[str]:
    words = text.split()
    lines: list[str] = []
    current = ""
    for word in words:
        trial = f"{current} {word}".strip()
        if pymupdf.get_text_length(trial, fontname="helv", fontsize=fontsize) <= max_width:
            current = trial
        else:
            if current:
                lines.append(current)
            current = word
    if current:
        lines.append(current)
    return lines


def _add_header_footer(page: pymupdf.Page, number: int, total: int) -> None:
    page.insert_text((MARGIN, 32), "G.C.E. (A/L) PHYSICS 2019", fontsize=8, fontname="helv")
    page.insert_text((245, 815), f"Page {number} of {total}", fontsize=8, fontname="helv")


def _add_question(
    page: pymupdf.Page,
    x: float,
    y: float,
    number: int,
    text: str,
    max_width: float = 480,
) -> float:
    fontsize = 10
    lines = _wrap(f"{number}. {text}", max_width, fontsize)
    for line in lines:
        page.insert_text((x, y), line, fontsize=fontsize, fontname="helv")
        y += fontsize * 1.45
    return y


def _add_paragraph(
    page: pymupdf.Page, x: float, y: float, text: str, max_width: float = 480
) -> float:
    fontsize = 10
    for line in _wrap(text, max_width, fontsize):
        page.insert_text((x, y), line, fontsize=fontsize, fontname="helv")
        y += fontsize * 1.45
    return y


def make_single_column_pdf(path: Path) -> None:
    """Two pages, one column, with question 2 continuing onto page 2."""
    doc = pymupdf.open()
    doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page1 = doc[0]
    page2 = doc[1]
    _add_header_footer(page1, 1, 2)
    _add_header_footer(page2, 2, 2)

    _add_question(
        page1,
        MARGIN,
        95,
        1,
        "A ball is thrown vertically upwards with a speed of 20 m/s. "
        "Sketch the velocity-time graph and calculate the maximum height reached.",
    )
    _add_question(
        page1,
        MARGIN,
        430,
        2,
        "A block of mass 2 kg rests on a rough horizontal surface. "
        "A horizontal force of 8 N is applied to the block.",
    )

    _add_paragraph(
        page2,
        MARGIN,
        95,
        "The coefficient of static friction between the block and the surface is 0.4. "
        "Determine whether the block moves and find its acceleration.",
    )
    _add_question(
        page2,
        MARGIN,
        330,
        3,
        "A uniform rod of length 2 m and weight 60 N is hinged at one end.",
    )
    _add_question(
        page2,
        MARGIN,
        650,
        4,
        "A student measures the resistance of a wire using a metre bridge.",
    )
    doc.save(path)
    doc.close()


def make_two_column_pdf(path: Path) -> None:
    """One page, two columns, questions numbered down each column."""
    doc = pymupdf.open()
    doc.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page = doc[0]
    _add_header_footer(page, 1, 1)

    left = 50
    right = 320
    width = 225
    _add_question(page, left, 95, 1, "Define the terms work and power, giving their SI units.", width)
    _add_question(
        page,
        left,
        420,
        2,
        "A car accelerates uniformly from rest to 20 m/s in 8 s. Find its acceleration.",
        width,
    )
    _add_question(
        page,
        right,
        95,
        3,
        "State Newton's three laws of motion and give one example of each.",
        width,
    )
    _add_question(
        page,
        right,
        420,
        4,
        "A 5 kg object is dropped from a height of 45 m. Find the time of fall.",
        width,
    )
    doc.save(path)
    doc.close()


def make_scanned_pdf(text_pdf: Path, scanned_pdf: Path) -> None:
    """Render the first page of a text PDF into an image-only PDF."""
    source = pymupdf.open(text_pdf)
    try:
        pix = source[0].get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72))
        image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    finally:
        source.close()

    buffer = BytesIO()
    image.save(buffer, format="PNG")
    output = pymupdf.open()
    try:
        output.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
        page = output[0]
        page.insert_image(page.rect, stream=buffer.getvalue())
        output.save(scanned_pdf)
    finally:
        output.close()
