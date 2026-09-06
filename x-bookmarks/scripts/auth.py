#!/usr/bin/env python3
"""One-time local authorization: get a refresh token for the sync workflow.

Run this on YOUR OWN machine (the one that can reach x.com). It opens a browser,
you click Authorize, and it prints the refresh token to paste into GitHub
Secrets. It then smoke-tests /2/users/me and the bookmarks endpoint so you find
out immediately whether the API actually works for your app, before wiring up
any automation.

    python3 scripts/auth.py --client-id <YOUR_CLIENT_ID>
"""

from __future__ import annotations

import argparse
import hashlib
import http.server
import os
import secrets
import socketserver
import sys
import threading
import urllib.parse
import webbrowser
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    API_BASE,
    AUTHORIZE_URL,
    SCOPES,
    TOKEN_URL,
    b64url,
    die,
    log,
)

REDIRECT_HOST = "127.0.0.1"
REDIRECT_PORT = 8723
REDIRECT_URI = f"http://{REDIRECT_HOST}:{REDIRECT_PORT}/callback"

_result: dict[str, str] = {}
_done = threading.Event()

PAGE = """<!doctype html><meta charset="utf-8">
<title>X bookmarks sync</title>
<body style="font-family:system-ui;padding:3rem;max-width:32rem;margin:auto">
<h2>{title}</h2><p>{body}</p></body>"""


class Handler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802 - stdlib signature
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return

        params = urllib.parse.parse_qs(parsed.query)
        _result["state"] = (params.get("state") or [""])[0]
        _result["code"] = (params.get("code") or [""])[0]
        _result["error"] = (params.get("error") or [""])[0]

        ok = bool(_result["code"]) and not _result["error"]
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(
            PAGE.format(
                title="授权成功 ✓" if ok else "授权失败",
                body=(
                    "可以关掉这个页面，回到终端。"
                    if ok
                    else f"X 返回：{_result['error'] or 'no code'}"
                ),
            ).encode("utf-8")
        )
        _done.set()

    def log_message(self, *args):  # silence the default stderr access log
        pass


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--client-id",
        default=os.environ.get("X_CLIENT_ID", ""),
        help="OAuth 2.0 Client ID from the X developer console "
        "(or set X_CLIENT_ID)",
    )
    args = parser.parse_args()
    client_id = args.client_id.strip()
    if not client_id:
        die("pass --client-id or set X_CLIENT_ID")

    # PKCE: a public client proves it started the flow without holding a secret.
    verifier = b64url(secrets.token_bytes(64))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = b64url(secrets.token_bytes(24))

    query = urllib.parse.urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "scope": " ".join(SCOPES),
            "state": state,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
    )
    url = f"{AUTHORIZE_URL}?{query}"

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer((REDIRECT_HOST, REDIRECT_PORT), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()

        log("正在打开浏览器完成 X 授权...")
        log("如果浏览器没自动打开，手动访问这个地址：\n")
        log(url + "\n")
        try:
            webbrowser.open(url)
        except Exception:
            pass

        log(f"等待回调 {REDIRECT_URI} ...（5 分钟超时）")
        if not _done.wait(timeout=300):
            die("timed out waiting for the authorization callback")
        httpd.shutdown()

    if _result.get("error"):
        die(f"X returned an error: {_result['error']}")
    if _result.get("state") != state:
        die("state mismatch - aborting, the callback did not come from your request")
    code = _result.get("code")
    if not code:
        die("no authorization code in the callback")

    resp = requests.post(
        TOKEN_URL,
        data={
            "grant_type": "authorization_code",
            "code": code,
            "client_id": client_id,
            "redirect_uri": REDIRECT_URI,
            "code_verifier": verifier,
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=30,
    )
    if resp.status_code != 200:
        die(f"code exchange failed ({resp.status_code}): {resp.text[:500]}")
    tokens = resp.json()

    access_token = tokens.get("access_token", "")
    refresh_token = tokens.get("refresh_token", "")
    if not refresh_token:
        die(
            "no refresh_token in the response - the app must request "
            "offline.access and be a public OAuth 2.0 client"
        )

    granted = tokens.get("scope", "")
    log(f"\n拿到 token。授予的 scope: {granted}")
    if "bookmark.read" not in granted:
        die("bookmark.read was NOT granted - the sync cannot work without it")

    # Smoke test. This is the whole point of running auth.py before building
    # anything else: several 2026 reports describe pay-per-use apps getting 403
    # here, and you want to know that now rather than after wiring up CI.
    log("\n冒烟测试 /2/users/me ...")
    me = requests.get(
        f"{API_BASE}/users/me",
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=30,
    )
    if me.status_code != 200:
        die(f"/2/users/me failed ({me.status_code}): {me.text[:500]}")
    user = me.json()["data"]
    log(f"  ✓ @{user['username']} (id {user['id']})")

    log("冒烟测试 bookmarks 端点 ...")
    bm = requests.get(
        f"{API_BASE}/users/{user['id']}/bookmarks",
        headers={"Authorization": f"Bearer {access_token}"},
        params={"max_results": 1},
        timeout=60,
    )
    if bm.status_code != 200:
        die(
            f"bookmarks endpoint failed ({bm.status_code}): {bm.text[:500]}\n"
            "This is the failure mode worth knowing about up front - see README "
            "for what to check."
        )
    count = bm.json().get("meta", {}).get("result_count", 0)
    log(f"  ✓ bookmarks 端点可用（本次返回 {count} 条）")

    print("\n" + "=" * 68)
    print("把下面两个值填进 GitHub 仓库的 Settings -> Secrets and variables")
    print("-> Actions:")
    print("=" * 68)
    print(f"\nX_CLIENT_ID\n{client_id}\n")
    print(f"X_REFRESH_TOKEN\n{refresh_token}\n")
    print("=" * 68)
    print("注意：这个 refresh token 一次性有效，workflow 每天用完会自动换新的。")
    print("不要在别处再用它，也不要提交进仓库。")


if __name__ == "__main__":
    main()
