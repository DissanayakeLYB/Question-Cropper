# Question Cropper

Cuts each question out of a Sri Lankan A/L Physics past-paper PDF and saves it as a PNG. It keeps diagrams, graphs, and equations because it crops the rendered page instead of extracting text.

There is also a small local review page for fixing boxes that automatic detection gets wrong.

## Requirements

- Python 3.11 or newer
- Tesseract OCR, only for scanned PDFs with no text layer
- The Python packages in `requirements.txt`

The tool uses PyMuPDF, Pillow, FastAPI, uvicorn, and pytesseract. It has no database and no login.

## Setup

```bash
python -m venv .venv
```

Activate the environment:

```bash
# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

Install the Python packages:

```bash
pip install -r requirements.txt
```

### Tesseract

PDFs with a text layer do not need Tesseract. Scanned PDFs do.

- **Windows:** install the [UB Mannheim Tesseract build](https://github.com/UB-Mannheim/tesseract/wiki), or run `scoop install tesseract`, and make sure `tesseract` is on your `PATH`. The English language data (`eng.traineddata`) must be installed.
- **macOS:** `brew install tesseract`
- **Debian / Ubuntu:** `sudo apt install tesseract-ocr`

Check it with:

```bash
tesseract --version
tesseract --list-langs
```

## Usage

Run the commands from the project folder.

### Automatic cropping

One PDF:

```bash
python -m cropper auto paper.pdf
```

A folder of PDFs (every `.pdf` directly inside it):

```bash
python -m cropper auto papers/
```

Each PDF gets its own output folder under `output/`:

```text
output/2019/
  2019_q01.png
  2019_q02.png
  ...
  manifest.json
```

### Review

```bash
python -m cropper review paper.pdf
```

A folder works too; use the Paper dropdown to switch between PDFs:

```bash
python -m cropper review papers/
```

This starts a local server on `127.0.0.1` (on an available port), prints the URL, and opens it in your browser.

On the review page:

1. Use **Previous page / Next page** or the page dropdown to inspect every page.
2. Click a box to select it. Drag the box body to move it, or drag one of its eight handles to resize it.
3. Click **+ Add box**, then drag on the page to draw a missing question box. A click without dragging creates a default-sized box. Edit **Question** to set its number.
4. Select a box and click **Delete box** (or press Delete) to remove a wrong box.
5. Click **Save PNGs** to write the final images and `manifest.json`.

A question that continues onto another page is represented by one box on each page, both with the same question number. On Save, boxes with the same number are stacked into one PNG, in page order. To join a continuation, set both boxes to the same number.

## Output format

`manifest.json` describes every image in its PDF's folder:

```json
{
  "pdf": "2019.pdf",
  "dpi": 200,
  "questions": [
    {
      "question": 1,
      "file": "2019_q01.png",
      "page": 2,
      "box": [134, 233, 1446, 320],
      "segments": [
        { "page": 2, "box": [134, 233, 1446, 320] },
        { "page": 3, "box": [135, 233, 1402, 315] }
      ]
    }
  ]
}
```

- `box` is `[left, top, right, bottom]` in pixels at 200 DPI, measured from the top-left of the page.
- `page` and `box` are the first segment of the image.
- `segments` lists every page piece that was stacked to make the PNG. Pieces are stacked vertically with a 20 px white gap.
- PNG filenames use the PDF name and a two-digit question number, for example `2019_q01.png`.

## How it works

1. Every page is rendered at 200 DPI with PyMuPDF.
2. Question starts are found at the left edge of each column. The PDF text layer is used when available; otherwise Tesseract OCR reads the page.
3. Repeating headers and footers, and page numbers, are detected and excluded from the content area.
4. A central gutter in the ink profile selects one-column or two-column mode. Questions are read down the left column, then down the right column.
5. Each question runs from its number to the next question start. The crop is trimmed to its ink and expanded by 10 px of padding.
6. If the next question starts on a later page, the content at the top of the next page is included and the pieces are joined into one PNG.

## Tests

```bash
python -m unittest discover -v
```

The tests generate their own single-column, two-column, and image-only PDFs. The OCR test is skipped when Tesseract is not installed.

## Limitations

Automatic detection is a heuristic, so always check the review page for a new paper.

- OCR quality depends on scan quality and resolution.
- Two-column detection expects a clear central gutter and column-major question order.
- A figure that crosses the central gutter can be split by column cropping.
- Header/footer removal depends on repeated text, page numbers, or a clear isolated bottom band. Unusual layouts may need manual boxes.
- Questions with unusual numbering (for example sub-parts numbered at the left edge) may need correction in review.
