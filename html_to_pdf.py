"""
Turn your reviewed HTML files (CV + cover letter) into print-ready PDFs.

This is the last step of the CV workflow: run it after you've reviewed the
HTML preview in your browser. By default it writes one PDF per HTML file
(e.g. Acme_Smith_Resume.pdf and Acme_Smith_CoverLetter.pdf). Use
--combine to merge each company's resume + cover letter into one PDF.

Requirements (install once):
    pip install playwright pypdf
    playwright install chromium

Usage:
    python html_to_pdf.py
    python html_to_pdf.py --combine
    python html_to_pdf.py --input output --name "Custom_Application"
"""

from __future__ import annotations

import argparse
import tempfile
from pathlib import Path

from playwright.sync_api import sync_playwright
from pypdf import PdfReader, PdfWriter

BASE = Path(__file__).resolve().parent


def order_key(path: Path) -> tuple:
    """Put the resume first, then the cover letter, then any other files."""
    name = path.name.lower()
    if "resume" in name or "cv" in name:
        return (0, name)
    if "cover" in name:
        return (1, name)
    return (2, name)


def collect_html(input_dir: Path) -> list[Path]:
    return sorted(input_dir.glob("*.html"), key=order_key)


def company_key(path: Path) -> str:
    """Return the shared prefix (company + last name) of a document file.

    Example: Acme_Smith_Resume.html -> Acme_Smith
    """
    stem = path.stem
    for suffix in ("_Resume", "_CoverLetter", "_Cover_Letter", "_CV"):
        if stem.endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    return stem


def render_one(browser, html_path: Path, pdf_path: Path) -> None:
    page = browser.new_page()
    page.goto(html_path.as_uri(), wait_until="networkidle")
    page.pdf(path=str(pdf_path), format="A4", print_background=True)
    page.close()
    pages = len(PdfReader(str(pdf_path)).pages)
    if pages != 1:
        print(f"  WARNING: {html_path.name} is {pages} pages, must be exactly 1. Tighten the HTML and re-run.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert reviewed HTML files (CV + cover letter) into PDFs."
    )
    parser.add_argument(
        "--input",
        default=str(BASE / "output"),
        help="Folder with the HTML files to convert (default: ./output)",
    )
    parser.add_argument(
        "--outdir",
        default=str(BASE / "output" / "PDF"),
        help="Folder where PDFs are written (default: ./output/PDF)",
    )
    parser.add_argument(
        "--name",
        default=None,
        help="Name for the combined PDF (only used with --combine and one "
        "company), without extension. Default: [Company]_[LastName]_Application",
    )
    parser.add_argument(
        "--combine",
        action="store_true",
        help="Merge each company's resume + cover letter into one PDF per "
        "company instead of one PDF per file",
    )
    args = parser.parse_args()

    input_dir = Path(args.input)
    out_dir = Path(args.outdir)
    html_files = collect_html(input_dir)

    if not html_files:
        print(f"No .html files found in {input_dir}")
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"Found {len(html_files)} HTML file(s) in {input_dir}:")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        if not args.combine:
            # Default: one PDF per HTML file (resume and cover letter separate).
            for html in html_files:
                pdf = out_dir / f"{html.stem}.pdf"
                print(f"  Rendering {html.name} -> {pdf}")
                render_one(browser, html, pdf)
        else:
            # Group HTML files by company so each application gets its own PDF.
            groups: dict[str, list[Path]] = {}
            for html in html_files:
                groups.setdefault(company_key(html), []).append(html)

            for company, files in groups.items():
                combined = PdfWriter()
                with tempfile.TemporaryDirectory() as tmp:
                    for html in files:
                        tmp_pdf = Path(tmp) / f"{html.stem}.pdf"
                        print(f"  Rendering {html.name}")
                        render_one(browser, html, tmp_pdf)
                        for page in PdfReader(str(tmp_pdf)).pages:
                            combined.add_page(page)
                final_name = (
                    args.name if args.name and len(groups) == 1 else f"{company}_Application"
                )
                final_path = out_dir / f"{final_name}.pdf"
                with final_path.open("wb") as fh:
                    combined.write(fh)
                print(f"  Combined -> {final_path}")

        browser.close()

    print("Done.")


if __name__ == "__main__":
    main()
