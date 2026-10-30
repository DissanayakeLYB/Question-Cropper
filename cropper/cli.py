"""Command line interface: auto and review."""

from __future__ import annotations

import socket
import sys
import threading
import webbrowser
from pathlib import Path

from .crop import save_questions
from .detect import format_number_ranges
from .page import CropperError
from .pipeline import analyze_pdf


def collect_pdfs(target: Path) -> list[Path]:
    if target.is_dir():
        pdfs = sorted(
            (entry for entry in target.iterdir() if entry.is_file() and entry.suffix.lower() == ".pdf"),
            key=lambda entry: entry.name.lower(),
        )
        if not pdfs:
            raise CropperError(f"No PDF files found in {target}.")
        return pdfs
    if target.is_file():
        if target.suffix.lower() != ".pdf":
            raise CropperError(f"{target} is not a PDF file.")
        return [target]
    raise CropperError(f"{target} does not exist.")


def run_auto(pdfs: list[Path], expected_count: int = 50) -> int:
    failures = 0
    for pdf in pdfs:
        print(f"{pdf.name}: reading...")
        try:
            analysis = analyze_pdf(pdf, expected_count=expected_count)
        except CropperError as exc:
            print(f"  error: {exc}", file=sys.stderr)
            failures += 1
            continue

        if not analysis.boxes:
            print("  no question starts detected; run 'review' to place boxes by hand.")
            failures += 1
            continue

        try:
            result = save_questions(pdf, analysis.boxes)
        except (CropperError, ValueError, OSError) as exc:
            print(f"  error: {exc}", file=sys.stderr)
            failures += 1
            continue

        question_count = len({box.question for box in analysis.boxes})
        print(f"  {question_count} question image(s) written to {result.output_dir}")
        if analysis.gaps:
            missing = format_number_ranges(analysis.gaps)
            print(
                f"  warning: no question start found for {missing}; "
                "run 'review' to add those boxes.",
                file=sys.stderr,
            )
    return 1 if failures else 0


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def run_review(pdfs: list[Path], expected_count: int = 50) -> int:
    import uvicorn

    from .review import ReviewSession, create_app

    session = ReviewSession(pdfs, expected_count=expected_count)
    app = create_app(session)
    port = _free_port()
    url = f"http://127.0.0.1:{port}/"

    print(f"Question Cropper review: {url}")
    print(f"Loaded {len(pdfs)} PDF(s). Press Ctrl+C to stop.")
    threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        uvicorn.run(app, host="127.0.0.1", port=port, log_level="info")
    finally:
        session.cleanup()
    return 0


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m cropper",
        description="Cut each question out of Sri Lankan A/L Physics past-paper PDFs.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    auto_parser = subparsers.add_parser("auto", help="detect and crop every question automatically")
    auto_parser.add_argument("input", type=Path, help="a PDF file or a folder of PDFs")
    auto_parser.add_argument(
        "-n",
        "--expected",
        type=int,
        default=50,
        metavar="COUNT",
        help="how many questions the paper has (default 50)",
    )

    review_parser = subparsers.add_parser("review", help="open the review page to upload PDFs and crop questions")
    review_parser.add_argument("input", type=Path, nargs="?", help="optional PDF file or folder of PDFs")
    review_parser.add_argument(
        "-n",
        "--expected",
        type=int,
        default=50,
        metavar="COUNT",
        help="how many questions the paper has (default 50)",
    )

    args = parser.parse_args(argv)
    if args.command == "review" and args.input is None:
        pdfs = []
    else:
        try:
            pdfs = collect_pdfs(args.input)
        except CropperError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2

    if args.command == "auto":
        return run_auto(pdfs, expected_count=args.expected)
    try:
        return run_review(pdfs, expected_count=args.expected)
    except CropperError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
