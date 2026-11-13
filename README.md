# Question Cropper

Cuts each question out of Sri Lankan A/L Physics past-paper PDFs, one PNG per question, with a local review page for fixing the crop boxes by hand.

## Quick start (Windows)

Double-click **`run.bat`**. It starts the local server on a free port and opens the review page in your browser; on a fresh copy it creates `.venv` and installs the dependencies first.

- Choose a PDF in the page, or drag a PDF (or a folder of PDFs) onto `run.bat` to open it straight away.
- Keep the window open while you work. Closing it stops the server.

## Setup without the .bat

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
source .venv/bin/activate     # macOS/Linux
pip install -r requirements.txt
```

Python 3.11+. Scanned PDFs also need [Tesseract](https://github.com/tesseract-ocr/tesseract) on your `PATH`; `run.bat` adds `C:\Program Files\Tesseract-OCR` when it finds it there.

## Command line

```bash
python -m cropper review            # review page, upload PDFs in the browser
python -m cropper review paper.pdf  # review page with one paper already loaded
python -m cropper auto paper.pdf    # detect and crop without reviewing
python -m cropper auto papers/      # every PDF in a folder
```

Papers are assumed to have 50 questions; use `-n 40` for a different count.

## Review page

Red boxes need a look and are listed under **Needs review**; good boxes are blue and **Next flagged** steps through the red ones. Drag a box to move it, drag a handle to resize, **+ Add box** fills in a missing question number, **Delete** removes the selected box. **Snap to text** moves a dragged edge onto the nearest blank row.

**Recrop from my boxes** (or **Save PNGs**) rewrites every PNG and the manifest from the boxes as they are on screen, and **Download all PNGs** fetches them in one zip. A processing screen covers the page while that runs, since a long paper takes a minute or two.

## Output

`output/<pdf name>/` gets `2019_q01.png`, `2019_q02.png`, … plus `manifest.json` with the boxes and review warnings. Boxes sharing a question number are joined, so a question spanning two pages becomes one PNG.

Photos of a paper are squared up first: the page's thick black border is found and the page is straightened before anything is detected, so a tilted or skewed photo still crops along the page. A page shot on a dark desk is found from the paper itself, and a lamp's falloff or a shadow is evened out before the text is read. Detection is heuristic: check each new paper, especially unusual layouts, poor scans, missing question numbers and figures that cross columns.

## Tests

```bash
python -m unittest discover -v
```
