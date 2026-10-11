"""Render every sample letter (samples/*.json) to HTML and PDF.

Usage: python samples/build.py   (needs Chrome/Chromium; set CHROME=/path/to/chrome if not found)
"""

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from letters.pdf import html_to_pdf  # noqa: E402
from letters.pipeline import Letter  # noqa: E402
from letters.render import render_html  # noqa: E402

# Samples show a UK A4 letter as it arrives: the address sits in the envelope window.
EXAMPLE_ADDRESS = ["The {child} Family", "1 Example Street", "Bristol", "BS1 4ST"]


for source in sorted((ROOT / "samples").glob("*.json")):
    data = json.loads(source.read_text())
    html_path = source.with_suffix(".html")
    address = [line.format(child=data["child"]) for line in EXAMPLE_ADDRESS]
    html = render_html(Letter(**data["letter"]), data["figure"], data["child"], address_lines=address)
    html_path.write_text(html)
    pages = html_to_pdf(html, source.with_suffix(".pdf"))
    print("wrote", html_path.relative_to(ROOT), "+ PDF,", pages, "pages")
