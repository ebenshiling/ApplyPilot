from __future__ import annotations

import argparse
from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    parser = argparse.ArgumentParser(description="Render an HTML file to PDF using Playwright Chromium.")
    parser.add_argument("input_html", help="Input HTML file path")
    parser.add_argument("output_pdf", help="Output PDF file path")
    args = parser.parse_args()

    input_html = Path(args.input_html).resolve()
    output_pdf = Path(args.output_pdf).resolve()

    if not input_html.exists():
        raise FileNotFoundError(f"Input HTML not found: {input_html}")
    output_pdf.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(input_html.as_uri(), wait_until="networkidle")
        page.pdf(
            path=str(output_pdf),
            format="A4",
            print_background=True,
            margin={"top": "14mm", "right": "10mm", "bottom": "14mm", "left": "10mm"},
        )
        browser.close()

    print(f"Wrote PDF: {output_pdf}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
