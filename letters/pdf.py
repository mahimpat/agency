"""Turn a rendered letter (HTML) into a print-ready PDF using Chrome or Chromium."""

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

CANDIDATES = [
    "chromium", "chromium-browser", "google-chrome", "google-chrome-stable", "chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
]


def find_chrome() -> str | None:
    if os.environ.get("CHROME"):
        return os.environ["CHROME"]
    for candidate in CANDIDATES:
        found = shutil.which(candidate) or (candidate if Path(candidate).is_file() else None)
        if found:
            return found
    return None


def html_to_pdf(html: str, pdf_path: Path) -> int:
    """Write the PDF and return its page count."""
    chrome = find_chrome()
    if not chrome:
        raise RuntimeError("Chrome or Chromium is needed to make PDFs. Install it or set CHROME=/path/to/chrome.")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "letter.html"
        source.write_text(html, encoding="utf-8")
        subprocess.run(
            [chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
             # Gives the fit-to-page script time to run before printing.
             "--virtual-time-budget=5000", f"--print-to-pdf={pdf_path}", source.as_uri()],
            check=True, capture_output=True, timeout=120,
        )
    return len(re.findall(rb"/Type\s*/Page[^s]", pdf_path.read_bytes()))
