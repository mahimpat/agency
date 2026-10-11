"""Send a letter to a print-and-post service.

- Stannp (UK, default): https://www.stannp.com/uk/direct-mail-api/letters
  Takes an A4 PDF, prints it, folds it into a window envelope, and posts it with
  Royal Mail. It prints the recipient's address into the window zone on page 1.
- Lob (US): https://docs.lob.com. Takes HTML and adds a blank address page.

Both run in TEST mode unless `live=True`: you get a PDF proof, nothing is mailed
or charged. Always check a proof before going live.

Address JSON (one format for both):
  {"name": "...", "line1": "...", "line2": "...", "town": "...", "postcode": "...",
   "country": "GB", "state": "CA"}   # state only for US
"""

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# Check this against the API URL shown in your Stannp dashboard (API settings).
STANNP_API_URL = os.environ.get("STANNP_API_URL", "https://dash.stannp.com/api/v1")
LOB_LETTERS_URL = "https://api.lob.com/v1/letters"


def _post(url: str, key: str, body: bytes, content_type: str) -> dict:
    auth = base64.b64encode(f"{key}:".encode()).decode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Authorization": f"Basic {auth}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"{url} returned {e.code}: {e.read().decode(errors='replace')}") from e


def stannp_fields(pdf: bytes, to: dict, live: bool, first_class: bool) -> dict:
    first, _, last = to["name"].partition(" ")
    fields = {
        "test": "false" if live else "true",
        "size": "A4",
        "duplex": "true",          # letter on the front, Real history card on the back
        "clearzone": "true",       # white-out the address zone and print the address there
        "post_unverified": "false",  # don't post to addresses that fail validation
        "file": base64.b64encode(pdf).decode(),
        "recipient[firstname]": first,
        "recipient[lastname]": last,
        "recipient[address1]": to["line1"],
        "recipient[address2]": to.get("line2", ""),
        "recipient[town]": to["town"],
        "recipient[postcode]": to["postcode"],
        "recipient[country]": to.get("country", "GB"),
    }
    if first_class:
        fields["addons"] = "FIRST_CLASS"
    return fields


def send_stannp(pdf_path: Path, to: dict, live: bool = False, first_class: bool = False) -> dict:
    key = os.environ.get("STANNP_API_KEY")
    if not key:
        raise RuntimeError("Set STANNP_API_KEY (from your Stannp dashboard).")
    fields = stannp_fields(pdf_path.read_bytes(), to, live, first_class)
    result = _post(f"{STANNP_API_URL}/letters/create", key,
                   urllib.parse.urlencode(fields).encode(), "application/x-www-form-urlencoded")
    if not result.get("success", True):
        raise RuntimeError(f"Stannp error: {result.get('error') or result}")
    data = result.get("data", result)
    return {"id": data.get("id"), "proof": data.get("pdf"), "cost": data.get("cost"), "status": data.get("status")}


def _lob_address(a: dict) -> dict:
    return {
        "name": a["name"], "address_line1": a["line1"], "address_line2": a.get("line2", ""),
        "address_city": a["town"], "address_state": a.get("state", ""),
        "address_zip": a["postcode"], "address_country": a.get("country", "US"),
    }


def send_lob(html: str, to: dict, sender: dict, description: str, live: bool = False) -> dict:
    key = os.environ.get("LOB_API_KEY")
    if not key:
        raise RuntimeError("Set LOB_API_KEY.")
    if live != key.startswith("live_"):
        raise RuntimeError("Use a test_ Lob key for proofs and a live_ key only with --live.")
    payload = {
        "description": description[:255], "to": _lob_address(to), "from": _lob_address(sender),
        "file": html, "color": True, "double_sided": True,
        "address_placement": "insert_blank_page", "use_type": "operational",
    }
    result = _post(LOB_LETTERS_URL, key, json.dumps(payload).encode(), "application/json")
    return {"id": result.get("id"), "proof": result.get("url"),
            "cost": None, "status": result.get("expected_delivery_date")}
