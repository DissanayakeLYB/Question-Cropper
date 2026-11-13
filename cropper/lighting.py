"""Evening out the lighting of a page photographed under a lamp or a shadow."""

from __future__ import annotations

from PIL import Image, ImageChops, ImageFilter

# One part of a photo can be much brighter than another. Every global threshold
# then reads the shadowed half of the paper as ink, so text and shadow cannot be
# told apart. Comparing each pixel with the picture's own blurred background
# keeps the paper white and the text black under any light; a page that is
# evenly lit needs none of it.
# Paper is the brightest thing in each region of a page, so a region's paper
# level is its 90th brightness percentile. Text moves that level a little; a
# lamp or a shadow moves it a lot.
PAPER_PERCENTILE = 0.9
REGION_ROWS = 3
REGION_COLS = 3
FLAT_LIGHT_SPREAD = 40
BLUR_SHARE = 40
INK_DROP = 30


def _paper_level(gray: Image.Image) -> int:
    """The brightness of the paper in one region of the page."""
    counts = gray.histogram()
    total = sum(counts)
    if total <= 0:
        return 255
    seen = 0
    for value, count in enumerate(counts):
        seen += count
        if seen >= total * PAPER_PERCENTILE:
            return value
    return 255


def light_is_uneven(gray: Image.Image) -> bool:
    """True when the page's own paper is much darker in one place than another."""
    width, height = gray.size
    if width < REGION_COLS * 10 or height < REGION_ROWS * 10:
        return False
    levels = []
    for row in range(REGION_ROWS):
        for column in range(REGION_COLS):
            box = (
                column * width // REGION_COLS,
                row * height // REGION_ROWS,
                (column + 1) * width // REGION_COLS,
                (row + 1) * height // REGION_ROWS,
            )
            levels.append(_paper_level(gray.crop(box)))
    return max(levels) - min(levels) > FLAT_LIGHT_SPREAD


def even_out(gray: Image.Image) -> Image.Image:
    """Black text on white, whatever the lighting, by comparing to the paper."""
    radius = max(12, gray.width // BLUR_SHARE)
    background = gray.filter(ImageFilter.GaussianBlur(radius))
    ink = ImageChops.subtract(background, gray)
    return ink.point(lambda value: 0 if value > INK_DROP else 255)


def even_light(image: Image.Image) -> Image.Image:
    """The page as black text on white, when its lighting is uneven."""
    gray = image.convert("L")
    if not light_is_uneven(gray):
        return image
    return even_out(gray)
