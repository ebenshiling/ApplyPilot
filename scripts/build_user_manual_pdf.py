from __future__ import annotations

from pathlib import Path

from playwright.sync_api import sync_playwright


def main() -> int:
    repo = Path(__file__).resolve().parents[1]
    html_path = repo / "docs" / "APPLYPILOT_USER_MANUAL.html"
    pdf_path = repo / "docs" / "APPLYPILOT_USER_MANUAL.pdf"

    if not html_path.exists():
        raise FileNotFoundError(f"Manual HTML not found: {html_path}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        page = browser.new_page()
        page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
        page.pdf(
            path=str(pdf_path),
            format="A4",
            margin={"top": "14mm", "right": "10mm", "bottom": "14mm", "left": "10mm"},
            print_background=True,
        )
        browser.close()

    print(f"Wrote PDF: {pdf_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
