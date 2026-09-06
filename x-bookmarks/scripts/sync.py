#!/usr/bin/env python3
"""Daily sync: pull X bookmarks into data/bookmarks.json and Markdown.

Runs in GitHub Actions. Reads X_CLIENT_ID / X_REFRESH_TOKEN from the
environment, rotates the refresh token back into GitHub Secrets, then fetches
bookmarks and writes the store. The workflow commits whatever changed.
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    META_PATH,
    STORE_PATH,
    api_get,
    die,
    env,
    load_json,
    log,
    refresh_access_token,
    save_json,
)
from render import render_all  # noqa: E402
from secretstore import preflight, rotate_refresh_token_secret  # noqa: E402

# One page is 100 bookmarks. Owned reads bill per resource ($0.001 each at the
# 2026 pay-per-use rate), so this cap is a spend guard as much as a loop guard:
# 30 pages is at most 3000 resources, about $3, per run.
DEFAULT_MAX_PAGES = 30

TWEET_FIELDS = "created_at,note_tweet,entities,lang,public_metrics,referenced_tweets"
USER_FIELDS = "username,name"
MEDIA_FIELDS = "url,preview_image_url,type,alt_text"
EXPANSIONS = "author_id,attachments.media_keys"


def now_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def index_includes(payload: dict) -> tuple[dict, dict]:
    includes = payload.get("includes") or {}
    users = {u["id"]: u for u in includes.get("users", [])}
    media = {m["media_key"]: m for m in includes.get("media", [])}
    return users, media


def normalize(tweet: dict, users: dict, media: dict, first_seen: str) -> dict:
    author = users.get(tweet.get("author_id"), {})
    username = author.get("username", "unknown")

    # A long-form post truncates `text`; note_tweet carries the full body.
    note = (tweet.get("note_tweet") or {}).get("text")
    text = note or tweet.get("text", "")

    image_urls = []
    keys = (tweet.get("attachments") or {}).get("media_keys") or []
    for key in keys:
        item = media.get(key)
        if not item:
            continue
        # Photos expose `url`; video and GIF only expose a preview frame.
        url = item.get("url") or item.get("preview_image_url")
        if url:
            image_urls.append({"type": item.get("type", "unknown"), "url": url})

    # entities.urls carries the expanded target behind every t.co shortlink.
    links = []
    for entry in (tweet.get("entities") or {}).get("urls", []):
        expanded = entry.get("expanded_url")
        if expanded and "/status/" not in expanded:
            links.append(expanded)

    return {
        "id": tweet["id"],
        "url": f"https://x.com/{username}/status/{tweet['id']}",
        "author": {"username": username, "name": author.get("name", "")},
        "created_at": tweet.get("created_at", ""),
        "text": text,
        "lang": tweet.get("lang", ""),
        "images": image_urls,
        "links": sorted(set(links)),
        "metrics": tweet.get("public_metrics", {}),
        "is_reply_or_quote": bool(tweet.get("referenced_tweets")),
        "first_seen": first_seen,
    }


def fetch_page(user_id: str, token: str, cursor: str | None) -> dict:
    params = {
        "max_results": 100,
        "tweet.fields": TWEET_FIELDS,
        "user.fields": USER_FIELDS,
        "media.fields": MEDIA_FIELDS,
        "expansions": EXPANSIONS,
    }
    if cursor:
        params["pagination_token"] = cursor
    return api_get(f"/users/{user_id}/bookmarks", token, params)


def collect(
    user_id: str,
    token: str,
    store: dict,
    *,
    start_cursor: str | None,
    stop_on_known: bool,
    max_pages: int,
) -> tuple[dict, str | None, int]:
    """Walk bookmark pages, returning (new_entries, next_cursor, pages_used).

    next_cursor is None when the walk reached the end of the timeline.
    """
    seen_at = now_iso()
    new_entries: dict[str, dict] = {}
    cursor = start_cursor
    pages = 0

    while pages < max_pages:
        payload = fetch_page(user_id, token, cursor)
        pages += 1
        tweets = payload.get("data") or []
        users, media = index_includes(payload)

        hit_known = False
        for tweet in tweets:
            if tweet["id"] in store:
                hit_known = True
                continue
            new_entries[tweet["id"]] = normalize(tweet, users, media, seen_at)

        log(f"  page {pages}: {len(tweets)} posts, {len(new_entries)} new so far")

        cursor = (payload.get("meta") or {}).get("next_token")
        if not cursor:
            return new_entries, None, pages
        # Bookmarks come back newest-bookmarked-first, so once a page contains
        # something already stored, everything older is stored too.
        if stop_on_known and hit_known:
            log("  reached already-synced bookmarks; stopping")
            return new_entries, None, pages

    log(f"  hit the {max_pages}-page cap; the rest continues on the next run")
    return new_entries, cursor, pages


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--full",
        action="store_true",
        help="walk the whole bookmark timeline instead of stopping at the "
        "first already-synced post",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=int(os.environ.get("MAX_PAGES", DEFAULT_MAX_PAGES)),
        help="hard cap on pages fetched this run (100 bookmarks per page)",
    )
    args = parser.parse_args()

    client_id = env("X_CLIENT_ID")
    refresh_token = env("X_REFRESH_TOKEN")
    preflight()

    log("refreshing access token...")
    tokens = refresh_access_token(client_id, refresh_token)
    access_token = tokens["access_token"]
    new_refresh = tokens["refresh_token"]

    # Persist the rotated token FIRST. The old one is already dead; if the run
    # died here without saving the new one, the next run would 400 and the whole
    # thing would need re-authorizing by hand.
    rotate_refresh_token_secret(new_refresh)

    meta = load_json(META_PATH, {})
    store = load_json(STORE_PATH, {})

    user_id = meta.get("user_id")
    if not user_id:
        # A user read bills separately, so resolve this once and cache it.
        log("resolving user id (once; cached in data/meta.json)...")
        user = api_get("/users/me", access_token)["data"]
        user_id = user["id"]
        meta["user_id"] = user_id
        meta["username"] = user["username"]
        log(f"  @{user['username']} ({user_id})")

    backfill_cursor = meta.get("backfill_cursor")
    backfill_done = bool(meta.get("backfill_complete"))
    total_new: dict[str, dict] = {}
    pages_left = args.max_pages

    # Pass 1: the top of the timeline, for whatever was bookmarked since the
    # last run. On a --full run this walks everything.
    log("fetching new bookmarks...")
    fresh, _, used = collect(
        user_id,
        access_token,
        store,
        start_cursor=None,
        stop_on_known=not args.full,
        max_pages=pages_left,
    )
    total_new.update(fresh)
    pages_left -= used

    # Pass 2: resume an unfinished first-run backfill from where it stopped.
    # Without this, pass 1 would stop at the newest known post every day and the
    # older tail would never be fetched.
    if not backfill_done and pages_left > 0:
        if backfill_cursor:
            log("resuming backfill of older bookmarks...")
            older, cursor, _ = collect(
                user_id,
                access_token,
                store,
                start_cursor=backfill_cursor,
                stop_on_known=False,
                max_pages=pages_left,
            )
            total_new.update(older)
            meta["backfill_cursor"] = cursor
            meta["backfill_complete"] = cursor is None
        else:
            # Pass 1 walked from the top and ran to the end of the timeline,
            # so there is nothing older left.
            meta["backfill_complete"] = True
            meta.pop("backfill_cursor", None)
        if meta.get("backfill_complete"):
            log("  backfill complete")

    store.update(total_new)
    meta["last_sync"] = now_iso()
    meta["total"] = len(store)

    save_json(STORE_PATH, store)
    save_json(META_PATH, meta)
    written = render_all(store, meta)

    log(
        f"\ndone: +{len(total_new)} new, {len(store)} total, "
        f"{written} markdown files"
    )

    # Surface the count for the workflow's commit message / job summary.
    summary = os.environ.get("GITHUB_OUTPUT")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"new_count={len(total_new)}\n")
            handle.write(f"total_count={len(store)}\n")


if __name__ == "__main__":
    main()
