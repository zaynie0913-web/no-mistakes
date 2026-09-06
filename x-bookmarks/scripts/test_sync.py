#!/usr/bin/env python3
"""Offline tests for the parts that are easy to get quietly wrong.

No network, no credentials: fake API pages are fed straight into the
pagination and normalization code.

    python3 scripts/test_sync.py
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import render  # noqa: E402
import sync  # noqa: E402

FAILURES: list[str] = []


def check(label: str, actual, expected) -> None:
    if actual == expected:
        print(f"  ok   {label}")
    else:
        print(f"  FAIL {label}\n       expected {expected!r}\n       got      {actual!r}")
        FAILURES.append(label)


def tweet(tid: str, text: str, *, created: str, media_key: str | None = None) -> dict:
    payload = {
        "id": tid,
        "author_id": "u1",
        "text": text,
        "created_at": created,
        "lang": "zh",
        "public_metrics": {"like_count": 1},
    }
    if media_key:
        payload["attachments"] = {"media_keys": [media_key]}
    return payload


def page(tweets: list[dict], next_token: str | None) -> dict:
    payload = {
        "data": tweets,
        "includes": {
            "users": [{"id": "u1", "username": "someone", "name": "Some One"}],
            "media": [
                {"media_key": "m1", "type": "photo", "url": "https://pbs.example/a.jpg"},
                {
                    "media_key": "m2",
                    "type": "video",
                    "preview_image_url": "https://pbs.example/v.jpg",
                },
            ],
        },
        "meta": {"result_count": len(tweets)},
    }
    if next_token:
        payload["meta"]["next_token"] = next_token
    return payload


def test_normalize() -> None:
    print("normalize")
    users = {"u1": {"id": "u1", "username": "someone", "name": "Some One"}}
    media = {
        "m1": {"media_key": "m1", "type": "photo", "url": "https://pbs.example/a.jpg"},
        "m2": {
            "media_key": "m2",
            "type": "video",
            "preview_image_url": "https://pbs.example/v.jpg",
        },
    }

    raw = tweet("1", "short", created="2026-09-01T10:00:00.000Z", media_key="m1")
    raw["note_tweet"] = {"text": "the full long-form body"}
    raw["entities"] = {
        "urls": [
            {"expanded_url": "https://example.com/article"},
            {"expanded_url": "https://x.com/someone/status/1"},
        ]
    }
    entry = sync.normalize(raw, users, media, "2026-09-06T00:00:00+00:00")

    # note_tweet must win: `text` is truncated for long-form posts.
    check("uses note_tweet body", entry["text"], "the full long-form body")
    check("builds the post url", entry["url"], "https://x.com/someone/status/1")
    check("keeps photo url", entry["images"], [{"type": "photo", "url": "https://pbs.example/a.jpg"}])
    # The self-referential t.co link back to the post itself is noise.
    check("drops the self status link", entry["links"], ["https://example.com/article"])

    video = sync.normalize(
        tweet("2", "v", created="2026-09-01T10:00:00.000Z", media_key="m2"),
        users,
        media,
        "now",
    )
    check(
        "falls back to video preview frame",
        video["images"],
        [{"type": "video", "url": "https://pbs.example/v.jpg"}],
    )


def test_incremental_stop() -> None:
    print("incremental stop")
    pages = [
        page([tweet("30", "new", created="2026-09-05T00:00:00.000Z")], "cursor1"),
        page(
            [
                tweet("20", "also new", created="2026-09-04T00:00:00.000Z"),
                tweet("10", "already stored", created="2026-09-03T00:00:00.000Z"),
            ],
            "cursor2",
        ),
        page([tweet("5", "older still", created="2026-09-02T00:00:00.000Z")], None),
    ]
    calls: list[str | None] = []

    def fake_get(path, token, params=None):
        calls.append((params or {}).get("pagination_token"))
        return pages[len(calls) - 1]

    original = sync.api_get
    sync.api_get = fake_get
    try:
        store = {"10": {"id": "10"}}
        new, cursor, used = sync.collect(
            "u1", "tok", store, start_cursor=None, stop_on_known=True, max_pages=10
        )
    finally:
        sync.api_get = original

    check("stops at the first known post", sorted(new), ["20", "30"])
    check("reports no continuation", cursor, None)
    check("does not read the third page", used, 2)


def test_backfill_cap_resumes() -> None:
    print("page cap")
    pages = [
        page([tweet(str(100 - i), "t", created="2026-08-01T00:00:00.000Z")], f"c{i}")
        for i in range(5)
    ]
    calls: list[int] = []

    def fake_get(path, token, params=None):
        calls.append(1)
        return pages[len(calls) - 1]

    original = sync.api_get
    sync.api_get = fake_get
    try:
        new, cursor, used = sync.collect(
            "u1", "tok", {}, start_cursor=None, stop_on_known=False, max_pages=2
        )
    finally:
        sync.api_get = original

    check("honours the page cap", used, 2)
    check("hands back a resume cursor", cursor, "c1")
    check("kept both pages of results", len(new), 2)


def test_render() -> None:
    print("render")
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        render.MARKDOWN_DIR = root / "bookmarks"
        store = {
            "1": {
                "id": "1",
                "url": "https://x.com/someone/status/1",
                "author": {"username": "someone", "name": "Some One"},
                "created_at": "2026-09-01T10:00:00.000Z",
                "text": "line one\nline two",
                "images": [{"type": "photo", "url": "https://pbs.example/a.jpg"}],
                "links": ["https://example.com"],
            },
            "2": {
                "id": "2",
                "url": "https://x.com/someone/status/2",
                "author": {"username": "someone", "name": ""},
                "created_at": "2026-08-15T10:00:00.000Z",
                "text": "older",
                "images": [],
                "links": [],
            },
        }
        written = render.render_all(store, {"username": "wai", "total": 2})
        check("one file per month", written, 2)

        sept = (root / "bookmarks" / "2026-09.md").read_text(encoding="utf-8")
        check("multi-line text stays quoted", "> line one\n> line two" in sept, True)
        check("image url is kept, not downloaded", "https://pbs.example/a.jpg" in sept, True)

        index = (root / "index.md").read_text(encoding="utf-8")
        check("index links newest month first", index.index("2026-09") < index.index("2026-08"), True)

        # A month that loses all its entries must not leave a stale file behind.
        render.render_all({"1": store["1"]}, {"username": "wai", "total": 1})
        check(
            "stale month file is removed",
            (root / "bookmarks" / "2026-08.md").exists(),
            False,
        )


def main() -> None:
    test_normalize()
    test_incremental_stop()
    test_backfill_cap_resumes()
    test_render()
    print()
    if FAILURES:
        print(f"{len(FAILURES)} failure(s): {', '.join(FAILURES)}")
        sys.exit(1)
    print("all tests passed")


if __name__ == "__main__":
    main()
