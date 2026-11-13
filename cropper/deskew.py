"""Straighten a photographed page before anything is detected on it.

A phone photo of a paper is rotated and often skewed, so the page's thick black
frame is the only reliable reference for "up". The frame's four sides are fitted
as lines, their intersections give the page's corners, and the page is warped
into a straight rectangle of the same canvas size, so every later coordinate
(boxes, margins, columns) is measured on an upright page.

Nothing here uses NumPy or OpenCV: the scan lines are read out of a downscaled
mask with Pillow and the four lines are fitted with plain least squares.
"""

from __future__ import annotations

import math

from PIL import Image

# The page frame is printed in black; paper shading and shadows stay lighter.
DARK_LEVEL = 110
# The frame is a page-sized straight line, so a downscaled copy loses nothing.
SCAN_WIDTH = 700
# A frame side must be visible on this share of the scan lines crossing the page.
MIN_EDGE_POINTS = 0.25
# A printed border is continuous, so most samples along the fitted line are dark.
MIN_SOLID = 0.85
SOLID_TOLERANCE = 4
# Solidity is sampled away from the corners: near them the neighbouring border
# is the outermost dark pixel, which would make a good line look broken.
SOLID_WINDOW = (0.25, 0.75)
# A photo of a page is never this crooked; beyond it the "frame" is something
# else in the picture and the page is left alone.
MAX_SKEW = 20.0
# Under this the page already sits square in the image, so warping would only
# blur it.
STRAIGHT_TOLERANCE = 0.5
# A page frame encloses most of the picture.
MIN_PAGE_FRACTION = 0.45
# A photo shot on a desk has a dark surround; the page is then found from the
# bright paper, because the printed frame and the desk are both dark.
DARK_SURROUND = 0.5
# Brightness used to split paper from desk, kept inside a sane band.
PAPER_LEVEL = (45, 200)
# A paper that fills less of the picture than this is not a page.
MIN_PAPER_FRACTION = 0.15

Quad = tuple[tuple[float, float], tuple[float, float], tuple[float, float], tuple[float, float]]


def _downscale(image: Image.Image) -> Image.Image:
    gray = image.convert("L")
    if gray.width <= SCAN_WIDTH:
        return gray
    height = max(1, round(gray.height * SCAN_WIDTH / gray.width))
    return gray.resize((SCAN_WIDTH, height), Image.Resampling.BOX)


def _dark_mask(gray: Image.Image) -> Image.Image:
    return gray.point(lambda value: 255 if value < DARK_LEVEL else 0)


def _scan_edges(mask: Image.Image) -> dict[str, list[tuple[float, float]]]:
    """The outermost dark pixel per row and per column, as (x, y) points."""
    width, height = mask.size
    data = mask.tobytes()
    left: list[tuple[float, float]] = []
    right: list[tuple[float, float]] = []
    for y in range(height):
        row = data[y * width : (y + 1) * width]
        first = row.find(255)
        if first == -1:
            continue
        left.append((float(first), float(y)))
        right.append((float(row.rfind(255)), float(y)))

    # Transposing turns each column into a row, so the same scan gives the top
    # and bottom edges without a Python loop over every pixel.
    transposed = mask.transpose(Image.Transpose.TRANSPOSE)
    t_width = transposed.width
    t_data = transposed.tobytes()
    top: list[tuple[float, float]] = []
    bottom: list[tuple[float, float]] = []
    for x in range(transposed.height):
        row = t_data[x * t_width : (x + 1) * t_width]
        first = row.find(255)
        if first == -1:
            continue
        top.append((float(x), float(first)))
        bottom.append((float(x), float(row.rfind(255))))
    return {"left": left, "right": right, "top": top, "bottom": bottom}


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


def _least_squares(independent: list[float], dependent: list[float]) -> tuple[float, float] | None:
    count = len(independent)
    if count < 2:
        return None
    mean_x = sum(independent) / count
    mean_y = sum(dependent) / count
    spread = sum((value - mean_x) ** 2 for value in independent)
    if spread <= 0:
        return None
    slope = sum(
        (value - mean_x) * (target - mean_y)
        for value, target in zip(independent, dependent)
    ) / spread
    return slope, mean_y - slope * mean_x


def _fit_line(
    points: list[tuple[float, float]], vertical: bool
) -> tuple[float, float, float] | None:
    """Fit ``x = a*y + b`` (vertical) or ``y = a*x + b``, dropping outliers.

    Returns the slope, intercept and the median residual. A frame is a clean
    line, so a large residual after trimming means these points are ragged page
    content rather than a border.
    """
    independent = [point[1] if vertical else point[0] for point in points]
    dependent = [point[0] if vertical else point[1] for point in points]
    for _ in range(4):
        fit = _least_squares(independent, dependent)
        if fit is None:
            return None
        slope, intercept = fit
        residuals = [
            abs(value - (slope * key + intercept))
            for key, value in zip(independent, dependent)
        ]
        cutoff = max(1.5, 2.0 * _median(residuals))
        keep = [index for index, value in enumerate(residuals) if value <= cutoff]
        if len(keep) == len(independent) or len(keep) < 2:
            break
        independent = [independent[index] for index in keep]
        dependent = [dependent[index] for index in keep]
    fit = _least_squares(independent, dependent)
    if fit is None:
        return None
    slope, intercept = fit
    residuals = [
        abs(value - (slope * key + intercept))
        for key, value in zip(independent, dependent)
    ]
    return slope, intercept, _median(residuals)


def _line_is_printed(mask: Image.Image, slope: float, intercept: float, vertical: bool) -> bool:
    """True when the fitted line actually lies on a continuous dark border."""
    width, height = mask.size
    data = mask.tobytes()
    extent = height if vertical else width
    start = int(extent * SOLID_WINDOW[0])
    stop = int(extent * SOLID_WINDOW[1])
    if stop - start < 20:
        return False
    samples = 60
    step = max(1, (stop - start) // samples)
    hits = 0
    taken = 0
    for key in range(start, stop, step):
        taken += 1
        value = slope * key + intercept
        if vertical:
            x, y = int(round(value)), key
            if not 0 <= x < width:
                continue
            for offset in range(-SOLID_TOLERANCE, SOLID_TOLERANCE + 1):
                column = x + offset
                if 0 <= column < width and data[y * width + column] == 255:
                    hits += 1
                    break
        else:
            x, y = key, int(round(value))
            if not 0 <= y < height:
                continue
            row = data[y * width : (y + 1) * width]
            left = max(0, x - SOLID_TOLERANCE)
            right = min(width, x + SOLID_TOLERANCE + 1)
            if row.find(255, left, right) != -1:
                hits += 1
    if taken == 0:
        return False
    return hits / taken >= MIN_SOLID


def _intersect(vertical: tuple[float, float], horizontal: tuple[float, float]) -> tuple[float, float] | None:
    """Where ``x = aL*y + bL`` meets ``y = aT*x + bT``."""
    a_l, b_l = vertical
    a_t, b_t = horizontal
    denominator = 1.0 - a_t * a_l
    if abs(denominator) < 1e-6:
        return None
    y = (a_t * b_l + b_t) / denominator
    return a_l * y + b_l, y


def _area(corners: Quad) -> float:
    total = 0.0
    for index, (x0, y0) in enumerate(corners):
        x1, y1 = corners[(index + 1) % 4]
        total += x0 * y1 - x1 * y0
    return abs(total) / 2


def _plausible(corners: Quad, width: int, height: int) -> bool:
    for x, y in corners:
        if not -0.05 * width <= x <= 1.05 * width:
            return False
        if not -0.05 * height <= y <= 1.05 * height:
            return False
    return _area(corners) >= MIN_PAGE_FRACTION * width * height


def _surround_is_dark(gray: Image.Image) -> bool:
    """True when the picture sits on a dark desk rather than on white paper."""
    width, height = gray.size
    data = gray.tobytes()
    step_x = max(1, width // 50)
    step_y = max(1, height // 50)
    samples = [data[x] for x in range(0, width, step_x)]
    samples += [data[(height - 1) * width + x] for x in range(0, width, step_x)]
    samples += [data[y * width] for y in range(0, height, step_y)]
    samples += [data[y * width + width - 1] for y in range(0, height, step_y)]
    if not samples:
        return False
    return sum(1 for value in samples if value < DARK_LEVEL) / len(samples) > DARK_SURROUND


def _otsu_level(histogram: list[int]) -> int:
    """The brightness that best splits the picture into two groups."""
    total = sum(histogram)
    if total <= 0:
        return 128
    weighted = sum(value * count for value, count in enumerate(histogram))
    weight_back = 0
    sum_back = 0
    best = -1.0
    level = 128
    for value, count in enumerate(histogram):
        weight_back += count
        if weight_back == 0:
            continue
        weight_fore = total - weight_back
        if weight_fore == 0:
            break
        sum_back += value * count
        mean_back = sum_back / weight_back
        mean_fore = (weighted - sum_back) / weight_fore
        between = weight_back * weight_fore * (mean_back - mean_fore) ** 2
        if between > best:
            best = between
            level = value
    return level


def _paper_mask(gray: Image.Image) -> Image.Image | None:
    """The bright paper, on a photo where the desk around it is dark."""
    level = _otsu_level(gray.histogram())
    level = max(PAPER_LEVEL[0], min(PAPER_LEVEL[1], level))
    mask = gray.point(lambda value: 255 if value > level else 0)
    bright = mask.histogram()[255]
    if bright < MIN_PAPER_FRACTION * gray.width * gray.height:
        return None
    return mask


def _quad_skew(quad: Quad) -> float:
    """The largest deviation from square, in degrees, over the four edges."""
    worst = 0.0
    for index in range(4):
        x0, y0 = quad[index]
        x1, y1 = quad[(index + 1) % 4]
        dx = x1 - x0
        dy = y1 - y0
        if abs(dx) >= abs(dy):
            angle = math.degrees(math.atan2(dy, abs(dx)))
        else:
            angle = math.degrees(math.atan2(dx, abs(dy)))
        worst = max(worst, abs(angle))
    return worst


def _quad_from_mask(mask: Image.Image) -> Quad | None:
    """Fit the four outer edges of a mask and intersect them into corners."""
    edges = _scan_edges(mask)
    width, height = mask.size
    needed = {
        "left": height * MIN_EDGE_POINTS,
        "right": height * MIN_EDGE_POINTS,
        "top": width * MIN_EDGE_POINTS,
        "bottom": width * MIN_EDGE_POINTS,
    }
    for name, points in edges.items():
        if len(points) < needed[name]:
            return None

    fits = {
        "left": _fit_line(edges["left"], vertical=True),
        "right": _fit_line(edges["right"], vertical=True),
        "top": _fit_line(edges["top"], vertical=False),
        "bottom": _fit_line(edges["bottom"], vertical=False),
    }
    if any(fit is None for fit in fits.values()):
        return None

    for name in ("left", "right"):
        slope, _, residual = fits[name]
        if residual > 4.0 or abs(math.degrees(math.atan(slope))) > MAX_SKEW:
            return None
    for name in ("top", "bottom"):
        slope, _, residual = fits[name]
        if residual > 4.0 or abs(math.degrees(math.atan(slope))) > MAX_SKEW:
            return None

    for name, vertical in (("left", True), ("right", True), ("top", False), ("bottom", False)):
        slope, intercept, _ = fits[name]
        if not _line_is_printed(mask, slope, intercept, vertical):
            return None

    left = fits["left"][:2]
    right = fits["right"][:2]
    top = fits["top"][:2]
    bottom = fits["bottom"][:2]
    corners = (
        _intersect(left, top),
        _intersect(right, top),
        _intersect(right, bottom),
        _intersect(left, bottom),
    )
    if any(corner is None for corner in corners):
        return None
    quad: Quad = corners  # type: ignore[assignment]
    if not _plausible(quad, width, height):
        return None
    return quad


def find_page_quad(image: Image.Image) -> Quad | None:
    """The four corners of the page, clockwise from the top left.

    Returns ``None`` when the picture has no usable page or the page is already
    square to the image, in which case it must be left as it is.
    """
    if image.width < 80 or image.height < 80:
        return None
    small = _downscale(image)
    gray = small.convert("L")

    quad = None
    if _surround_is_dark(gray):
        # The desk and the printed frame are both dark, so the frame's outer
        # edge cannot be told from the background: measure the bright paper.
        paper = _paper_mask(gray)
        if paper is not None:
            quad = _quad_from_mask(paper)
    else:
        quad = _quad_from_mask(_dark_mask(gray))
    if quad is None:
        return None

    skew = _quad_skew(quad)
    if skew > MAX_SKEW or skew < STRAIGHT_TOLERANCE:
        # Too crooked to be a page, or already square: no warp either way.
        return None

    scale_x = image.width / small.width
    scale_y = image.height / small.height
    return tuple((x * scale_x, y * scale_y) for x, y in quad)  # type: ignore[return-value]


def _perspective_coeffs(
    destination: Quad, source: Quad
) -> tuple[float, float, float, float, float, float, float, float]:
    """Pillow's coefficients mapping output pixels back to source pixels."""
    rows: list[list[float]] = []
    for (x, y), (u, v) in zip(destination, source):
        rows.append([x, y, 1, 0, 0, 0, -u * x, -u * y, u])
        rows.append([0, 0, 0, x, y, 1, -v * x, -v * y, v])

    size = 8
    for column in range(size):
        pivot = max(range(column, size), key=lambda row: abs(rows[row][column]))
        if abs(rows[pivot][column]) < 1e-12:
            raise ValueError("Page corners are degenerate.")
        rows[column], rows[pivot] = rows[pivot], rows[column]
        lead = rows[column][column]
        rows[column] = [value / lead for value in rows[column]]
        for row in range(size):
            if row == column:
                continue
            factor = rows[row][column]
            if factor == 0:
                continue
            rows[row] = [value - factor * lead_value for value, lead_value in zip(rows[row], rows[column])]
    return tuple(rows[index][size] for index in range(size))  # type: ignore[return-value]


def _distance(first: tuple[float, float], second: tuple[float, float]) -> float:
    return math.hypot(first[0] - second[0], first[1] - second[1])


def straighten(image: Image.Image) -> Image.Image:
    """Return the page squared up, on a canvas of the same size, or unchanged.

    The canvas size is kept so page coordinates stay comparable with a plain
    render; only the page inside it moves.
    """
    quad = find_page_quad(image)
    if quad is None:
        return image

    top, right, bottom, left = quad
    target_width = max(1, round(max(_distance(top, right), _distance(left, bottom))))
    target_height = max(1, round(max(_distance(top, left), _distance(right, bottom))))
    origin_x = round(min(point[0] for point in quad))
    origin_y = round(min(point[1] for point in quad))

    destination: Quad = (
        (0.0, 0.0),
        (float(target_width), 0.0),
        (float(target_width), float(target_height)),
        (0.0, float(target_height)),
    )
    coeffs = _perspective_coeffs(destination, quad)
    warped = image.transform(
        (target_width, target_height),
        Image.Transform.PERSPECTIVE,
        coeffs,
        resample=Image.Resampling.BICUBIC,
        fillcolor=(255, 255, 255),
    )

    canvas = Image.new("RGB", image.size, "white")
    # A page that was rotated fills less than its bounding box, so this lands
    # inside the canvas unless the photo was cropped to the frame's edge.
    offset_x = max(0, min(origin_x, canvas.width - target_width))
    offset_y = max(0, min(origin_y, canvas.height - target_height))
    canvas.paste(warped.convert("RGB"), (offset_x, offset_y))
    return canvas
