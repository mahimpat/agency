"""Send a rendered letter through Lob's print-and-mail API (https://docs.lob.com).

Use a `test_` API key until you're ready: test letters are rendered (you get a PDF
preview link) but never printed or mailed.
"""

import base64
import json
import os
import urllib.error
import urllib.request

LOB_LETTERS_URL = "https://api.lob.com/v1/letters"


def send_letter(html: str, to: dict, sender: dict, description: str) -> dict:
    """`to` and `sender` are Lob address dicts: name, address_line1, address_city,
    address_state, address_zip (and address_country for non-US senders)."""
    key = os.environ.get("LOB_API_KEY")
    if not key:
        raise RuntimeError("Set LOB_API_KEY (use a test_ key first).")

    payload = {
        "description": description[:255],
        "to": to,
        "from": sender,
        "file": html,
        "color": True,
        "double_sided": True,
        # Lob adds a blank first page for the address, so the letter design is untouched.
        "address_placement": "insert_blank_page",
        "use_type": "operational",
    }
    auth = base64.b64encode(f"{key}:".encode()).decode()
    request = urllib.request.Request(
        LOB_LETTERS_URL,
        data=json.dumps(payload).encode(),
        headers={"Authorization": f"Basic {auth}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            return json.loads(response.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"Lob error {e.code}: {e.read().decode(errors='replace')}") from e
