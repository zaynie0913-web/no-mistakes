"""Write the rotated X refresh token back into GitHub Actions Secrets.

X issues a new refresh token on every refresh and kills the old one, so the
stored secret has to be updated on every run or the next day's run fails with
invalid_grant. GitHub's API takes secrets encrypted to the repository's public
key with a libsodium sealed box.

Needs a fine-grained personal access token with `Secrets: read and write` on
this repository, exposed to the workflow as GH_PAT.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import requests
from nacl import encoding, public

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import die, log  # noqa: E402

API = "https://api.github.com"
SECRET_NAME = "X_REFRESH_TOKEN"


def _in_actions() -> bool:
    return os.environ.get("GITHUB_ACTIONS", "").lower() == "true"


def preflight() -> None:
    """Fail before the token is rotated if we could not store the new one.

    Order matters: refreshing first and discovering the missing PAT afterwards
    would burn the stored refresh token and force a manual re-authorization.
    """
    if not _in_actions():
        return
    for name in ("GH_PAT", "GITHUB_REPOSITORY"):
        if not os.environ.get(name, "").strip():
            die(
                f"{name} is not set. Refusing to refresh the X token, because "
                "the rotated replacement could not be saved and the current "
                "secret would be left dead. See README > Secrets."
            )


def _encrypt(public_key_b64: str, secret_value: str) -> str:
    key = public.PublicKey(public_key_b64.encode("utf-8"), encoding.Base64Encoder())
    sealed = public.SealedBox(key).encrypt(secret_value.encode("utf-8"))
    return encoding.Base64Encoder().encode(sealed).decode("utf-8")


def rotate_refresh_token_secret(new_token: str) -> None:
    if not _in_actions():
        print("\n" + "=" * 68)
        print("Not running in GitHub Actions, so the secret was not updated.")
        print(f"New {SECRET_NAME} (the previous one is now dead):\n")
        print(new_token)
        print("=" * 68 + "\n")
        return

    pat = os.environ["GH_PAT"].strip()
    repo = os.environ["GITHUB_REPOSITORY"].strip()
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    key_resp = requests.get(
        f"{API}/repos/{repo}/actions/secrets/public-key", headers=headers, timeout=30
    )
    if key_resp.status_code != 200:
        die(
            f"could not read the repo public key ({key_resp.status_code}): "
            f"{key_resp.text[:300]}\nDoes GH_PAT have Secrets: read and write "
            f"on {repo}?"
        )
    key = key_resp.json()

    put_resp = requests.put(
        f"{API}/repos/{repo}/actions/secrets/{SECRET_NAME}",
        headers=headers,
        json={
            "encrypted_value": _encrypt(key["key"], new_token),
            "key_id": key["key_id"],
        },
        timeout=30,
    )
    if put_resp.status_code not in (201, 204):
        die(
            f"could not update {SECRET_NAME} ({put_resp.status_code}): "
            f"{put_resp.text[:300]}"
        )
    log(f"  rotated {SECRET_NAME} secret")
