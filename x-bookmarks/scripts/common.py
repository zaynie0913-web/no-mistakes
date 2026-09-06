"""Shared helpers for the X bookmarks sync tool.

Everything that talks to X or GitHub lives here so auth.py, sync.py and
render.py stay small.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests

# X API endpoints.
TOKEN_URL = "https://api.x.com/2/oauth2/token"
AUTHORIZE_URL = "https://x.com/i/oauth2/authorize"
API_BASE = "https://api.x.com/2"

# Scopes required by GET /2/users/:id/bookmarks.
#   bookmark.read  - the bookmarks themselves
#   tweet.read     - the post payloads inside them
#   users.read     - author expansion + /2/users/me
#   offline.access - issues the refresh token this whole design depends on
SCOPES = ["tweet.read", "users.read", "bookmark.read", "offline.access"]

REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = REPO_ROOT / "data"
STORE_PATH = DATA_DIR / "bookmarks.json"
META_PATH = DATA_DIR / "meta.json"
MARKDOWN_DIR = DATA_DIR / "bookmarks"


def log(msg: str) -> None:
    print(msg, flush=True)


def die(msg: str, code: int = 1) -> None:
    print(f"ERROR: {msg}", file=sys.stderr, flush=True)
    sys.exit(code)


def env(name: str, required: bool = True) -> str:
    value = os.environ.get(name, "").strip()
    if required and not value:
        die(f"missing required environment variable {name}")
    return value


def load_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"{path} is not valid JSON ({exc}); refusing to overwrite it")


def save_json(path: Path, payload) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # sort_keys keeps the committed diff stable run over run.
    text = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    path.write_text(text + "\n", encoding="utf-8")


def refresh_access_token(client_id: str, refresh_token: str) -> dict:
    """Trade a refresh token for an access token.

    X rotates refresh tokens: the response carries a NEW refresh_token and the
    one passed in is dead the moment this returns 200. Every caller must
    persist the new one before doing anything else that can fail.
    """
    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
            "client_id": client_id,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        die(
            "token refresh failed "
            f"({resp.status_code}): {resp.text[:500]}\n"
            "If this says invalid_grant the refresh token is spent or expired - "
            "re-run scripts/auth.py locally and update the X_REFRESH_TOKEN secret."
        )
    payload = resp.json()
    if "refresh_token" not in payload:
        die("token response had no refresh_token; was offline.access requested?")
    return payload


def api_get(path: str, access_token: str, params: dict | None = None) -> dict:
    """GET an X API v2 endpoint with bounded retries on 429/5xx."""
    url = f"{API_BASE}{path}"
    headers = {"Authorization": f"Bearer {access_token}"}
    attempt = 0
    while True:
        attempt += 1
        resp = requests.get(url, headers=headers, params=params, timeout=60)

        if resp.status_code == 200:
            return resp.json()

        if resp.status_code == 429:
            # The bookmarks endpoint is tightly rate limited per user.
            # Prefer the server's own hint, fall back to the 15-minute window.
            reset = resp.headers.get("x-rate-limit-reset")
            retry_after = resp.headers.get("retry-after")
            if retry_after and retry_after.isdigit():
                wait = int(retry_after)
            elif reset and reset.isdigit():
                wait = max(1, int(reset) - int(time.time()))
            else:
                wait = 900
            wait = min(wait, 960)
            if attempt > 3:
                die(f"still rate limited after {attempt} attempts on {path}")
            log(f"  rate limited on {path}; sleeping {wait}s")
            time.sleep(wait + 1)
            continue

        if resp.status_code in (500, 502, 503, 504) and attempt <= 3:
            backoff = 2**attempt
            log(f"  {resp.status_code} on {path}; retrying in {backoff}s")
            time.sleep(backoff)
            continue

        if resp.status_code == 403:
            die(
                f"403 Forbidden on {path}: {resp.text[:500]}\n"
                "Known causes, in order of likelihood:\n"
                "  1. The app is not attached to a project with active billing "
                "(pay-per-use credits must be purchased in the developer console).\n"
                "  2. The authorization was granted without the bookmark.read scope - "
                "re-run scripts/auth.py.\n"
                "  3. X-side bug on newly migrated pay-per-use apps; several 2026 "
                "developer-forum reports describe exactly this. Contact X support if "
                "1 and 2 check out."
            )

        die(f"{resp.status_code} on {path}: {resp.text[:500]}")


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")
