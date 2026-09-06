#!/usr/bin/env python3
"""Render the JSON store into Markdown files Claude can read cheaply.

One file per month of the post's own creation date, newest post first inside
each file, plus an index. Run it standalone to re-render without touching the
API:

    python3 scripts/render.py
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (  # noqa: E402
    MARKDOWN_DIR,
    META_PATH,
    STORE_PATH,
    load_json,
    log,
)

# Long posts are kept whole; the Markdown is the reading surface, and a
# truncated post is worse than a long one.
BLOCKQUOTE_PREFIX = "> "


def month_key(entry: dict) -> str:
    created = entry.get("created_at") or ""
    if len(created) >= 7:
        return created[:7]
    return "unknown"


def as_blockquote(text: str) -> str:
    lines = text.replace("\r\n", "\n").split("\n")
    return "\n".join(BLOCKQUOTE_PREFIX + line if line else ">" for line in lines)


def render_entry(entry: dict) -> str:
    author = entry.get("author", {})
    handle = author.get("username", "unknown")
    name = author.get("name", "")
    header = f"### @{handle}" + (f" ({name})" if name else "")
    date = (entry.get("created_at") or "")[:10]

    parts = [header, "", f"- 时间: {date}", f"- 原推: {entry.get('url', '')}"]

    links = entry.get("links") or []
    if links:
        parts.append("- 链接: " + " · ".join(links))

    images = entry.get("images") or []
    if images:
        rendered = " · ".join(
            f"{img.get('type', 'media')}: {img.get('url', '')}" for img in images
        )
        parts.append("- 图片: " + rendered)

    parts.extend(["", as_blockquote(entry.get("text", "")), ""])
    return "\n".join(parts)


def render_all(store: dict, meta: dict) -> int:
    MARKDOWN_DIR.mkdir(parents=True, exist_ok=True)

    buckets: dict[str, list[dict]] = {}
    for entry in store.values():
        buckets.setdefault(month_key(entry), []).append(entry)

    # Drop files for months that no longer have entries, so an edited store
    # cannot leave orphans behind.
    wanted = {f"{key}.md" for key in buckets}
    for stale in MARKDOWN_DIR.glob("*.md"):
        if stale.name not in wanted:
            stale.unlink()

    written = 0
    for key, entries in buckets.items():
        entries.sort(key=lambda e: (e.get("created_at") or "", e.get("id", "")), reverse=True)
        body = [f"# X 书签 · {key}", "", f"共 {len(entries)} 条。", ""]
        for entry in entries:
            body.append(render_entry(entry))
        (MARKDOWN_DIR / f"{key}.md").write_text("\n".join(body).rstrip() + "\n", encoding="utf-8")
        written += 1

    index = [
        "# X 书签索引",
        "",
        f"- 账号: @{meta.get('username', '?')}",
        f"- 总数: {meta.get('total', len(store))}",
        f"- 最近同步: {meta.get('last_sync', '?')}",
    ]
    if not meta.get("backfill_complete"):
        index.append("- 存量书签仍在分批回填中（每天一批）。")
    index.extend(["", "## 按月份", ""])
    for key in sorted(buckets, reverse=True):
        index.append(f"- [{key}](bookmarks/{key}.md) — {len(buckets[key])} 条")
    index.extend(
        [
            "",
            "完整结构化数据见 [`bookmarks.json`](bookmarks.json)。",
        ]
    )
    (MARKDOWN_DIR.parent / "index.md").write_text("\n".join(index) + "\n", encoding="utf-8")
    return written


def main() -> None:
    store = load_json(STORE_PATH, {})
    meta = load_json(META_PATH, {})
    if not store:
        log("store is empty; nothing to render")
        return
    written = render_all(store, meta)
    log(f"rendered {written} markdown files from {len(store)} bookmarks")


if __name__ == "__main__":
    main()
