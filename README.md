# Question Cropper

Crops each question out of a Sri Lankan A/L Physics past-paper PDF and saves it as a PNG. Cropping the rendered page keeps diagrams, graphs, and equations intact. A small local review page lets you fix boxes that automatic detection gets wrong.

## Requirements

- Python 3.11 or newer
- Tesseract OCR, only for scanned PDFs with no text layer

## Setup

```bash
python -m venv .venv
```

Activate it with `.venv\Scripts\activate` on Windows or `source .venv/bin/activate` on macOS and Linux, then install the packages:

```bash
pip install -r requirements.txt
```

For scanned papers, install Tesseract with English language data and make sure `tesseract` is on your `PATH`:

- **Windows:** the [UB Mannheim build](https://github.com/UB-Mannheim/tesseract/wiki), or `scoop install tesseract`
- **macOS:** `brew install tesseract`
- **Debian / Ubuntu:** `sudo apt install tesseract-ocr`

## Usage

Crop a PDF, or every PDF directly inside a folder:

```bash
python -m cropper auto paper.pdf
python -m cropper auto papers/
```

Papers are assumed to have 50 questions; pass the real count with `-n` (or `--expected`) for anything else:

```bash
python -m cropper auto paper.pdf -n 40
```

Open the review page with no arguments to start from an upload, then pick a PDF (up to 100 MB):

```bash
python -m cropper review
```

Or load an existing PDF or folder straight into it:

```bash
python -m cropper review paper.pdf
python -m cropper review papers/
```

In review, click a box to select it, drag it to move it, or drag a handle to resize it. Click **+ Add box** and drag on the page to draw a missing box, and **Save PNGs** to write the images and manifest. Boxes that share a question number are stacked into one PNG, so a question that continues onto another page can be joined by giving both boxes the same number.

## Output

Each PDF writes to `output/<pdf name>/`, with one PNG per question named like `2019_q01.png` plus a `manifest.json`. The manifest records each question's number, file, and boxes, which are `[left, top, right, bottom]` in pixels at 200 DPI, along with every page segment stacked into the image and any reasons the box needs review.

## How it works

Pages are rendered at 200 DPI, and question starts are found where the PDF text layer, the OCR text and the column geometry agree. Scanned pages are read twice with two different page-segmentation modes and the results are combined, because a single pass can silently drop a question number next to the page edge. A number is only accepted when it sits on the same left edge as the other question numbers in its column, which rejects numbers inside body text. Missing numbers are then searched between their neighbours: first in the extracted text, then with a 300 DPI OCR pass over the left margin at a lower confidence threshold, and finally as a blank gap between text blocks. Headers, footers, and page numbers are excluded, and a detected central gutter selects one- or two-column reading order. Each crop runs from one question number to the next. Its edges are snapped to the page's horizontal ink profile, which marks a row blank only when it has no dark pixel: the top edge sits in the blank gap above the question number, the bottom edge in the blank gap below the question's last line, and an edge is never left on a row with ink. Boxes on a page cannot overlap, because each bottom edge is kept above the next top edge, and a tight gap is split down its middle. The page frame, the page header and footer notes such as "[See page two]" are kept out of the crop, the remaining white space is trimmed and 10 px of padding is kept. A question that continues onto the next page is still joined into a single image.

Every box is then checked. A box that still holds a second question number is split at that number and both halves are trimmed again, so the question that was swallowed gets its own image. Boxes are marked **needs review** when they are more than twice the median box height, when ink touches their top or bottom edge, when they overlap another box, or when their number breaks the paper's numbering. Flagged boxes are dashed amber on the review page, the top bar shows how many need a check, the CLI lists their numbers, and `manifest.json` records the reasons.

## Tests

```bash
python -m unittest discover -v
```

The tests generate their own PDFs; OCR tests are skipped when Tesseract is not installed.

## Limitations

Automatic detection is a heuristic, so check the review page for each new paper. OCR quality depends on the scan, a figure crossing the central gutter can be split, and unusual layouts or numbering may need manual boxes. When a question number cannot be found, the paper is assumed to have 50 questions unless you set the count; the review page shows how many were found and which numbers are missing so you can add those boxes yourself.
