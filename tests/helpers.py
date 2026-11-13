"""Synthetic PDF fixtures for the tests."""

from __future__ import annotations

from io import BytesIO
from pathlib import Path

import pymupdf
from PIL import Image, ImageChops, ImageDraw, ImageFilter

from cropper.deskew import _perspective_coeffs

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


def make_rotated_photo_pdf(path: Path, angle: float = 6.0) -> None:
    """A page as a phone photo shows it: a thick-bordered paper, rotated."""
    source = pymupdf.open()
    source.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page = source[0]
    _add_header_footer(page, 1, 1)
    _add_question(
        page,
        MARGIN,
        95,
        1,
        "A ball is thrown vertically upwards with a speed of 20 m/s. "
        "Sketch the velocity-time graph and calculate the maximum height reached.",
    )
    _add_question(
        page,
        MARGIN,
        520,
        2,
        "A block of mass 2 kg rests on a rough horizontal surface. "
        "A horizontal force of 8 N is applied to the block.",
    )
    pix = page.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    source.close()

    framed = Image.new("RGB", (image.width + 120, image.height + 120), "white")
    framed.paste(image, (60, 60))
    draw = ImageDraw.Draw(framed)
    draw.rectangle(
        [40, 40, framed.width - 41, framed.height - 41], outline=(0, 0, 0), width=16
    )
    photo = framed.rotate(
        angle, resample=Image.Resampling.BICUBIC, expand=True, fillcolor="white"
    )

    buffer = BytesIO()
    photo.save(buffer, format="PNG")
    output = pymupdf.open()
    try:
        output.new_page(
            width=photo.width * 72 / 200,
            height=photo.height * 72 / 200,
        )
        target = output[0]
        target.insert_image(target.rect, stream=buffer.getvalue())
        output.save(path)
    finally:
        output.close()


def make_rotated_photo_pdf_with_text_layer(path: Path, angle: float = 6.0) -> None:
    """A rotated bordered page that also carries a text layer.

    The layer is what a scan with OCR text looks like: the words sit where the
    rotated image put them, so the image and the text agree with each other.
    """
    make_rotated_photo_pdf(path, angle=angle)
    doc = pymupdf.open(path)
    try:
        page = doc[0]
        textpage = page.get_textpage_ocr(flags=0, dpi=200, full=True)
        for block in textpage.extractDICT().get("blocks", []):
            for line in block.get("lines", []):
                for span in line.get("spans", []):
                    text = span.get("text", "").strip()
                    if not text:
                        continue
                    page.insert_text(
                        span["origin"],
                        text,
                        fontsize=max(4.0, float(span.get("size", 8.0))),
                        render_mode=3,
                    )
        doc.saveIncr()
    finally:
        doc.close()


DESK = (30, 32, 36)


def make_perspective_photo_pdf(path: Path) -> None:
    """A realistic phone photo: dark desk, perspective, uneven light, shadow."""
    source = pymupdf.open()
    source.new_page(width=PAGE_WIDTH, height=PAGE_HEIGHT)
    page = source[0]
    _add_header_footer(page, 1, 1)
    _add_question(
        page,
        MARGIN,
        95,
        1,
        "A ball is thrown vertically upwards with a speed of 20 m/s. "
        "Sketch the velocity-time graph and calculate the maximum height reached.",
    )
    _add_question(
        page,
        MARGIN,
        520,
        2,
        "A block of mass 2 kg rests on a rough horizontal surface. "
        "A horizontal force of 8 N is applied to the block.",
    )
    pix = page.get_pixmap(matrix=pymupdf.Matrix(200 / 72, 200 / 72))
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    source.close()

    margin = 70
    framed = Image.new("RGB", (image.width + 2 * margin, image.height + 2 * margin), "white")
    framed.paste(image, (margin, margin))
    draw = ImageDraw.Draw(framed)
    draw.rectangle([45, 45, framed.width - 46, framed.height - 46], outline=(0, 0, 0), width=18)

    width, height = framed.size
    canvas = (width + 400, height + 300)
    # The printed frame, and where it lands in a hand-held shot: the far edge is
    # visibly shorter and the page leans, so the paper is not a rectangle.
    source_quad = ((45, 45), (width - 46, 45), (width - 46, height - 46), (45, height - 46))
    target_quad = (
        (360, 150),
        (width - 40, 90),
        (width - 140, height + 150),
        (60, height + 220),
    )
    photo = framed.transform(
        canvas,
        Image.Transform.PERSPECTIVE,
        _perspective_coeffs(target_quad, source_quad),
        resample=Image.Resampling.BICUBIC,
        fillcolor=DESK,
    )

    # Uneven light: bright at the top left, falling off to the right, with a
    # soft shadow across the lower part of the page.
    falloff = Image.linear_gradient("L").rotate(90, expand=True).resize(photo.size)
    falloff = falloff.point(lambda value: 140 + value * 115 // 255)
    shadow = Image.new("L", photo.size, 255)
    shadow_draw = ImageDraw.Draw(shadow)
    shadow_draw.ellipse(
        [-photo.width * 0.2, photo.height * 0.55, photo.width * 1.1, photo.height * 1.25],
        fill=170,
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(90))
    light = ImageChops.multiply(falloff, shadow)
    photo = ImageChops.multiply(photo, Image.merge("RGB", (light, light, light)))
    photo = photo.filter(ImageFilter.GaussianBlur(0.6))

    buffer = BytesIO()
    photo.save(buffer, format="PNG")
    output = pymupdf.open()
    try:
        output.new_page(width=photo.width * 72 / 200, height=photo.height * 72 / 200)
        target = output[0]
        target.insert_image(target.rect, stream=buffer.getvalue())
        output.save(path)
    finally:
        output.close()


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
