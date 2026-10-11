"""Sign in with Google (OpenID Connect, authorisation-code flow).

We ask only for "openid email profile": a stable Google account ID, the email
address (and whether Google has verified it), and the person's name.
"""

import json
import secrets
import urllib.error
import urllib.parse
import urllib.request

from .config import Settings

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"


def enabled(settings: Settings) -> bool:
    return bool(settings.google_client_id and settings.google_client_secret)


def redirect_uri(settings: Settings) -> str:
    return f"{settings.base_url}/auth/google/callback"


def authorization_url(settings: Settings, session: dict) -> str:
    state = secrets.token_urlsafe(24)
    session["google_state"] = state
    params = {
        "client_id": settings.google_client_id, "redirect_uri": redirect_uri(settings),
        "response_type": "code", "scope": "openid email profile", "state": state,
        "prompt": "select_account",
    }
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


def _post_form(url: str, data: dict) -> dict:
    request = urllib.request.Request(url, data=urllib.parse.urlencode(data).encode(), method="POST",
                                     headers={"Content-Type": "application/x-www-form-urlencoded"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def _get_json(url: str, token: str) -> dict:
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(request, timeout=20) as response:
        return json.loads(response.read())


def fetch_profile(settings: Settings, code: str) -> dict:
    """Swap the one-time code for tokens (server to server, with our secret), then read the profile.
    Returns {"sub", "email", "email_verified", "name"}."""
    try:
        tokens = _post_form(TOKEN_URL, {
            "code": code, "client_id": settings.google_client_id, "client_secret": settings.google_client_secret,
            "redirect_uri": redirect_uri(settings), "grant_type": "authorization_code"})
        info = _get_json(USERINFO_URL, tokens["access_token"])
    except (urllib.error.URLError, KeyError, ValueError) as e:
        raise RuntimeError(f"Google sign-in failed: {e}") from e
    return {"sub": str(info["sub"]), "email": str(info.get("email", "")).lower(),
            "email_verified": bool(info.get("email_verified")), "name": info.get("given_name") or info.get("name") or ""}
