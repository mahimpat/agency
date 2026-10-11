"""Render every sample letter (samples/*.json) to HTML and PDF.

Usage: python samples/build.py   (PDFs need Chrome/Chromium; set CHROME=/path/to/chrome)
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from letters.pipeline import Letter  # noqa: E402
from letters.render import render_html  # noqa: E402

chrome = os.environ.get("CHROME") or shutil.which("chromium") or shutil.which("google-chrome")

for source in sorted((ROOT / "samples").glob("*.json")):
    data = json.loads(source.read_text())
    html_path = source.with_suffix(".html")
    html_path.write_text(render_html(Letter(**data["letter"]), data["figure"], data["child"]))
    print("wrote", html_path.relative_to(ROOT))
    if chrome:
        pdf = source.with_suffix(".pdf")
        subprocess.run([chrome, "--headless", "--no-sandbox", "--disable-gpu", "--no-pdf-header-footer",
                        f"--print-to-pdf={pdf}", str(html_path)], check=True, capture_output=True)
        print("wrote", pdf.relative_to(ROOT))
