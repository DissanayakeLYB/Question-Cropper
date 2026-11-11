# Question Cropper

Extracts individual questions from Sri Lankan A/L Physics past-paper PDFs and saves them as PNG files. Use automatic detection for a quick result or the local review page to correct crop boundaries.

## Requirements

- Python 3.11+
- Tesseract OCR for scanned PDFs without a text layer

## Setup

```bash
python -m venv .venv
```

Activate the environment (`.venv\Scripts\activate` on Windows or `source .venv/bin/activate` on macOS/Linux), then install the dependencies:

```bash
pip install -r requirements.txt
```

For scanned PDFs, install [Tesseract](https://github.com/tesseract-ocr/tesseract) and make sure `tesseract` is on your `PATH`.

## Usage

Crop one PDF or all PDFs in a folder automatically:

```bash
python -m cropper auto paper.pdf
python -m cropper auto papers/
```

The default expected question count is 50. Change it with `-n`:

```bash
python -m cropper auto paper.pdf -n 40
```

Open the review page:

```bash
python -m cropper review
python -m cropper review paper.pdf
```

The review page lets you move, resize, add, or remove boxes before saving PNGs. Boxes that need a look are red and listed under **Needs review**, good boxes are blue, and **Next flagged** jumps to the next one. **Snap to text** moves a dragged edge to the nearest blank row, and **+ Add box** fills in the next missing question number.

**Save PNGs** (or **Recrop from my boxes**) rewrites every PNG and the manifest from the boxes as they are on screen, not from the boxes detection first chose; the page says when the crops are stale, and **Download all PNGs** fetches them in one zip. Use `←` and `→` to change pages and `Delete` to remove the selected box. Boxes with the same question number are combined, so questions spanning multiple pages can be joined.

To run an upload-only server:

```bash
uvicorn cropper.review:create_default_app --factory --host 0.0.0.0 --port 8000
```

Then open `http://localhost:8000`. The server has no authentication; keep it local or put it behind authentication and HTTPS.

## Output

Results are saved under `output/<pdf name>/`:

- One PNG per question, such as `2019_q01.png`
- `manifest.json` containing crop metadata and review warnings

Automatic detection is heuristic, so review each new paper. Unusual layouts, poor scans, missing question numbers, and figures crossing columns may require manual corrections. Papers default to 50 questions unless another count is specified.

## Tests

```bash
python -m unittest discover -v
```
