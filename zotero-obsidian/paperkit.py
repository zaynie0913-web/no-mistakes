#!/usr/bin/env python3
"""paperkit - Zotero + Obsidian 论文流水线.

从几篇种子论文出发, 用 OpenAlex 找出强关联文献, 按相关度分级, 下载开放获取 PDF,
生成可直接导入 Zotero 的 RIS, 并铺好 Obsidian 笔记库结构与模板.

纯标准库, 零依赖. Python 3.9+.

用法概览:
    python3 paperkit.py install                         # 一键安装
    python3 paperkit.py setup   --vault ~/Obsidian/Research
    python3 paperkit.py discover --seeds seeds.txt --out ./papers --mailto you@example.com
    python3 paperkit.py doctor  --vault ~/Obsidian/Research
"""

from __future__ import annotations

import argparse
import html
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zlib
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator, Sequence

OPENALEX = "https://api.openalex.org"
UA = "paperkit/1.0 (https://github.com/zaynie0913-web/no-mistakes)"

# OpenAlex 批量过滤器每次最多 50 个值; per-page 最多 200.
BATCH = 50
PER_PAGE = 200

# 分级: 目录名直接就是 Zotero 导入后的分类名, 所以起名要能当收藏夹用.
TIERS = [
    ("S", "S-核心必读"),
    ("A", "A-强相关"),
    ("B", "B-背景扩展"),
]
TIER_DIRS = dict(TIERS)


def log(msg: str) -> None:
    print(msg, file=sys.stderr, flush=True)


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------


class Client:
    """OpenAlex 客户端: 限速 + 重试 + 礼貌池.

    匿名 1 req/s, 带 mailto 进 polite pool 后 10 req/s. 默认按匿名节流,
    给了 mailto 就放开, 免得没填邮箱的人一上来就被限流.
    """

    def __init__(self, mailto: str | None = None, timeout: int = 30) -> None:
        self.mailto = mailto
        self.timeout = timeout
        self.min_interval = 0.12 if mailto else 1.05
        self._last = 0.0
        self.calls = 0

    def _throttle(self) -> None:
        wait = self.min_interval - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()

    def get(self, path: str, **params: Any) -> dict:
        if self.mailto:
            params["mailto"] = self.mailto
        qs = urllib.parse.urlencode(
            {k: v for k, v in params.items() if v is not None}, safe="|:,-<>"
        )
        url = f"{OPENALEX}{path}" + (f"?{qs}" if qs else "")
        last_err: Exception | None = None
        for attempt in range(5):
            self._throttle()
            try:
                req = urllib.request.Request(url, headers={"User-Agent": UA})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    self.calls += 1
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                # 404 是"这篇找不到", 不是故障, 直接抛给调用方处理.
                if exc.code == 404:
                    raise
                last_err = exc
                if exc.code in (429, 500, 502, 503, 504):
                    time.sleep(2**attempt)
                    continue
                raise
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_err = exc
                time.sleep(2**attempt)
        raise RuntimeError(f"OpenAlex 请求失败 {url}: {last_err}")

    def paged(self, path: str, limit: int, **params: Any) -> Iterator[dict]:
        """按 cursor 翻页, 最多取 limit 条."""
        cursor = "*"
        got = 0
        while cursor and got < limit:
            page = self.get(
                path, cursor=cursor, **{"per-page": min(PER_PAGE, limit - got)}, **params
            )
            results = page.get("results") or []
            if not results:
                return
            for item in results:
                yield item
                got += 1
                if got >= limit:
                    return
            cursor = (page.get("meta") or {}).get("next_cursor")


# --------------------------------------------------------------------------
# 论文模型
# --------------------------------------------------------------------------

WORK_FIELDS = ",".join(
    [
        "id",
        "doi",
        "title",
        "display_name",
        "publication_year",
        "publication_date",
        "type",
        "cited_by_count",
        "referenced_works",
        "related_works",
        "primary_location",
        "best_oa_location",
        "authorships",
        "abstract_inverted_index",
    ]
)


def short_id(openalex_id: str | None) -> str:
    """https://openalex.org/W123 -> W123"""
    return (openalex_id or "").rstrip("/").rsplit("/", 1)[-1]


def undo_inverted_abstract(index: dict | None) -> str:
    """OpenAlex 存的是 词 -> [位置...] 的倒排表, 还原成正常段落."""
    if not index:
        return ""
    slots: list[tuple[int, str]] = []
    for word, positions in index.items():
        for pos in positions:
            slots.append((pos, word))
    slots.sort()
    return " ".join(word for _, word in slots)


@dataclass
class Paper:
    oid: str
    title: str
    year: int | None
    doi: str | None
    venue: str | None
    authors: list[str]
    abstract: str
    cited_by: int
    refs: set[str]
    related: list[str]
    pdf_url: str | None
    is_oa: bool
    type: str | None = None

    # 打分过程中填充
    score: float = 0.0
    tier: str = "B"
    reasons: list[str] = field(default_factory=list)
    seed_links: set[str] = field(default_factory=set)
    prov: dict[str, int] = field(default_factory=dict)
    is_seed: bool = False
    pdf_path: str | None = None

    @classmethod
    def from_json(cls, w: dict) -> "Paper":
        loc = w.get("best_oa_location") or w.get("primary_location") or {}
        source = (loc or {}).get("source") or {}
        prim = (w.get("primary_location") or {}).get("source") or {}
        authors = []
        for a in w.get("authorships") or []:
            name = ((a.get("author") or {}).get("display_name") or "").strip()
            if name:
                authors.append(name)
        doi = w.get("doi") or ""
        return cls(
            oid=short_id(w.get("id")),
            title=(w.get("title") or w.get("display_name") or "(无标题)").strip(),
            year=w.get("publication_year"),
            doi=doi.replace("https://doi.org/", "") or None,
            venue=(source.get("display_name") or prim.get("display_name") or None),
            authors=authors,
            abstract=undo_inverted_abstract(w.get("abstract_inverted_index")),
            cited_by=w.get("cited_by_count") or 0,
            refs={short_id(r) for r in (w.get("referenced_works") or [])},
            related=[short_id(r) for r in (w.get("related_works") or [])],
            pdf_url=(loc or {}).get("pdf_url"),
            is_oa=bool((loc or {}).get("is_oa")),
            type=w.get("type"),
        )

    @property
    def first_author(self) -> str:
        if not self.authors:
            return "Unknown"
        return self.authors[0].split()[-1]

    @property
    def citekey(self) -> str:
        base = re.sub(r"[^A-Za-z]", "", self.first_author) or "unknown"
        word = ""
        for tok in re.findall(r"[A-Za-z]{4,}", self.title):
            if tok.lower() not in STOPWORDS:
                word = tok.lower()
                break
        return f"{base.lower()}{self.year or ''}{word}"

    def slug(self, maxlen: int = 60) -> str:
        """文件名: 去掉所有会让 Windows/macOS 炸掉的字符."""
        s = re.sub(r'[<>:"/\\|?*\x00-\x1f]', " ", self.title)
        s = re.sub(r"\s+", " ", s).strip().rstrip(". ")
        if len(s) > maxlen:
            s = s[:maxlen].rsplit(" ", 1)[0]
        return f"{self.first_author}{self.year or ''} - {s}".strip()


STOPWORDS = {
    "with", "from", "that", "this", "these", "those", "into", "using", "based",
    "toward", "towards", "über", "their", "there", "when", "what", "which",
    "learning", "networks", "network", "deep", "novel", "study", "analysis",
}


def fetch_works(client: Client, ids: Iterable[str]) -> dict[str, Paper]:
    """按 OpenAlex ID 批量取论文. 自动分批到 50 个一组."""
    out: dict[str, Paper] = {}
    ids = [i for i in dict.fromkeys(ids) if i.startswith("W")]
    for i in range(0, len(ids), BATCH):
        chunk = ids[i : i + BATCH]
        page = client.get(
            "/works",
            filter=f"openalex_id:{'|'.join(chunk)}",
            select=WORK_FIELDS,
            **{"per-page": BATCH},
        )
        for w in page.get("results") or []:
            p = Paper.from_json(w)
            out[p.oid] = p
    return out


# --------------------------------------------------------------------------
# 种子解析
# --------------------------------------------------------------------------

ARXIV_RE = re.compile(r"(?:arxiv[:/ ]*)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.I)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"'<>]+)", re.I)


def resolve_seed(client: Client, raw: str) -> Paper | None:
    """把一行种子输入解析成 Paper. 支持 DOI / arXiv 号 / OpenAlex ID / 标题."""
    s = raw.strip()
    if not s or s.startswith("#"):
        return None

    def one(path: str) -> Paper | None:
        try:
            return Paper.from_json(client.get(path, select=WORK_FIELDS))
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise

    if re.fullmatch(r"W\d+", s):
        return one(f"/works/{s}")

    doi = DOI_RE.search(s)
    if doi:
        return one(f"/works/doi:{doi.group(1).rstrip('.')}")

    arx = ARXIV_RE.search(s)
    if arx:
        # arXiv 论文在 OpenAlex 里几乎都挂着 DataCite 分配的 10.48550 DOI.
        hit = one(f"/works/doi:10.48550/arXiv.{arx.group(1)}")
        if hit:
            return hit

    # 剩下的按标题搜, 取标题最接近的一条, 避免搜索排序把综述顶上来.
    page = client.get("/works", search=s, select=WORK_FIELDS, **{"per-page": 5})
    results = page.get("results") or []
    if not results:
        return None
    want = norm_title(s)
    best = max(results, key=lambda w: title_overlap(want, norm_title(w.get("title") or "")))
    # 以前永远取最接近的一条, 哪怕毫不相干, 随便一篇论文就成了种子.
    if title_overlap(want, norm_title(best.get("title") or "")) < TITLE_MATCH_MIN:
        log(f"  · 搜到最接近的是《{(best.get('title') or '')[:60]}》, 不像同一篇, 不用")
        return None
    return Paper.from_json(best)


TITLE_MATCH_MIN = 0.5


def norm_title(t: str) -> set[str]:
    """英文按词, 中文按字. 只按英文词切的话, 中文标题的集合是空的, 相似度永远为 0."""
    t = (t or "").lower()
    return set(re.findall(r"[a-z0-9]+", t)) | set(re.findall(r"[\u4e00-\u9fff]", t))


def title_overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


# --------------------------------------------------------------------------
# 关联扩展
# --------------------------------------------------------------------------


def expand(client: Client, seeds: list[Paper], per_seed_citers: int) -> dict[str, Paper]:
    """围绕种子采集候选集, 三个方向各走一遍.

    向后 = 种子引用的文献 (领域基石), 向前 = 引用种子的文献 (最新进展),
    related = OpenAlex 自己算的近邻. 三路合并后再统一打分.
    """
    candidates: dict[str, Paper] = {}
    seed_ids = {s.oid for s in seeds}
    provenance: dict[str, dict[str, set[str]]] = {}

    def note(cid: str, kind: str, seed: str) -> None:
        provenance.setdefault(cid, {}).setdefault(kind, set()).add(seed)

    # 1) 向后: 种子的参考文献
    backward: set[str] = set()
    for s in seeds:
        for r in s.refs:
            if r not in seed_ids:
                backward.add(r)
                note(r, "back", s.oid)

    # 2) related_works
    related: set[str] = set()
    for s in seeds:
        for r in s.related:
            if r not in seed_ids:
                related.add(r)
                note(r, "rel", s.oid)

    # 3) 向前: 引用了种子的文献. 按被引量排序, 只要头部, 否则热门种子会拉回几千条噪声.
    forward: dict[str, Paper] = {}
    for s in seeds:
        log(f"  · 抓取引用 {s.oid} 的文献 …")
        for w in client.paged(
            "/works",
            limit=per_seed_citers,
            filter=f"cites:{s.oid}",
            sort="cited_by_count:desc",
            select=WORK_FIELDS,
        ):
            p = Paper.from_json(w)
            if p.oid in seed_ids:
                continue
            forward[p.oid] = p
            note(p.oid, "fwd", s.oid)

    candidates.update(forward)

    need = [i for i in (backward | related) if i not in candidates]
    log(f"  · 补齐 {len(need)} 篇上游/近邻文献元数据 …")
    candidates.update(fetch_works(client, need))

    for cid, kinds in provenance.items():
        p = candidates.get(cid)
        if not p:
            continue
        for kind, seed_set in kinds.items():
            p.seed_links |= seed_set
            p.prov[kind] = len(seed_set)
    return candidates


# --------------------------------------------------------------------------
# 打分与分级
# --------------------------------------------------------------------------

W_BACK = 3.0     # 被种子引用: 领域基石, 权重最高
W_FWD = 2.5      # 引用了种子: 直接的后续工作
W_REL = 1.5      # OpenAlex 近邻: 信号弱一些
W_COUPLE = 2.0   # 文献耦合: 和种子共享参考文献 = 同一个问题域
W_BREADTH = 2.5  # 同时挂到多篇种子上, 这是最强的"这就是你要找的"信号
W_IMPACT = 0.8   # 影响力, 年均被引取对数, 别让老论文单靠年头碾压


def score_all(candidates: dict[str, Paper], seeds: list[Paper], this_year: int) -> None:
    seed_refs: set[str] = set()
    for s in seeds:
        seed_refs |= s.refs

    for p in candidates.values():
        prov = p.prov
        s = 0.0
        why: list[str] = []

        if prov.get("back"):
            s += W_BACK * prov["back"]
            why.append(f"被 {prov['back']} 篇种子引用")
        if prov.get("fwd"):
            s += W_FWD * prov["fwd"]
            why.append(f"引用了 {prov['fwd']} 篇种子")
        if prov.get("rel"):
            s += W_REL * prov["rel"]
            why.append("OpenAlex 判定为近邻")

        # 文献耦合: 和种子重叠的参考文献数, 用自身参考文献量开方归一,
        # 否则 300 条参考文献的综述会靠体量霸榜.
        shared = len(p.refs & seed_refs)
        if shared:
            couple = shared / math.sqrt(max(len(p.refs), 1))
            s += W_COUPLE * couple
            why.append(f"与种子共享 {shared} 条参考文献")

        breadth = len(p.seed_links)
        if breadth > 1:
            s += W_BREADTH * (breadth - 1)
            why.append(f"同时关联 {breadth} 篇种子")

        age = max(1, this_year - (p.year or this_year) + 1)
        per_year = p.cited_by / age
        s += W_IMPACT * math.log1p(per_year)

        # 近三年的新工作给一点补偿, 它们还没来得及攒引用.
        if p.year and this_year - p.year <= 3:
            s += 0.6

        # 综述/社论这类不是"论文"的条目往下压, 但不排除.
        if (p.type or "") not in ("article", "preprint", "book-chapter", "book"):
            s *= 0.75

        p.score = round(s, 3)
        p.reasons = why


def assign_tiers(ranked: list[Paper], n_s: int, n_a: int) -> None:
    for i, p in enumerate(ranked):
        if i < n_s:
            p.tier = "S"
        elif i < n_s + n_a:
            p.tier = "A"
        else:
            p.tier = "B"


# --------------------------------------------------------------------------
# 产物: PDF / RIS / Obsidian 笔记
# --------------------------------------------------------------------------


def download_pdf(url: str, dest: Path, timeout: int = 60) -> bool:
    """下载开放获取 PDF. 只接受真的是 PDF 的响应, 免得存下一堆登录页 HTML."""
    try:
        req = urllib.request.Request(url, headers={"User-Agent": UA})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            head = resp.read(5)
            if head[:4] != b"%PDF":
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(".part")
            with open(tmp, "wb") as fh:
                fh.write(head)
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
            tmp.replace(dest)
            return True
    except Exception:
        return False


def ris_escape(v: str) -> str:
    return re.sub(r"\s*\r?\n\s*", " ", (v or "").strip())


RIS_TYPES = {
    "article": "JOUR",
    "preprint": "JOUR",
    "book": "BOOK",
    "book-chapter": "CHAP",
    "dissertation": "THES",
    "proceedings-article": "CPAPER",
}


def to_ris(p: Paper) -> str:
    """Zotero 的 RIS 导入比 BibTeX 更稳: 摘要/DOI/附件路径都有明确字段."""
    lines = [f"TY  - {RIS_TYPES.get(p.type or '', 'JOUR')}"]
    lines.append(f"TI  - {ris_escape(p.title)}")
    for a in p.authors[:50]:
        lines.append(f"AU  - {ris_escape(a)}")
    if p.year:
        lines.append(f"PY  - {p.year}")
    if p.venue:
        lines.append(f"JO  - {ris_escape(p.venue)}")
    if p.abstract:
        lines.append(f"AB  - {ris_escape(p.abstract)}")
    if p.doi:
        lines.append(f"DO  - {p.doi}")
        lines.append(f"UR  - https://doi.org/{p.doi}")
    else:
        lines.append(f"UR  - https://openalex.org/{p.oid}")
    # 分级和理由写进 Zotero 的标签/备注, 在 Zotero 里也能一眼看出为什么收它.
    lines.append(f"KW  - paperkit/{TIER_DIRS[p.tier]}")
    if p.reasons:
        lines.append(f"N1  - paperkit 关联理由: {'; '.join(p.reasons)} (score {p.score})")
    if p.pdf_path:
        lines.append(f"L1  - {p.pdf_path}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n\n"


NOTE_TEMPLATE = """---
citekey: {citekey}
title: "{title}"
authors: [{authors}]
year: {year}
venue: "{venue}"
doi: {doi}
openalex: {oid}
tier: {tier}
score: {score}
status: 未读
rating:
tags: [论文, {tier_tag}]
---

# {title}

> [!info] 为什么这篇会进来
> {reasons}
>
> 关联种子: {seed_links}
> 被引: {cited_by} · 年份: {year} · 分级: **{tier_name}**

## 摘要

{abstract}

## 我的笔记

> 读的时候在 Zotero 里用颜色标注, 回头一键同步到下面的分区.
> 🟡 关键结论 · 🔴 存疑/反对 · 🟢 方法可复用 · 🔵 待深挖

### 🟡 关键结论

### 🔴 存疑与反对

### 🟢 可复用的方法

### 🔵 待深挖

## 一句话总结

<!-- 读完再写. 写不出来说明没读懂. -->

## 关联

- 链接:
"""


def render_note(p: Paper, seed_titles: dict[str, str]) -> str:
    links = ", ".join(seed_titles.get(s, s) for s in sorted(p.seed_links)) or "—"
    return NOTE_TEMPLATE.format(
        citekey=p.citekey,
        title=p.title.replace('"', "'"),
        authors=", ".join('"{}"'.format(a.replace('"', "'")) for a in p.authors[:8]),
        year=p.year or "",
        venue=(p.venue or "").replace('"', "'"),
        doi=p.doi or "",
        oid=p.oid,
        tier=p.tier,
        tier_tag=TIER_DIRS[p.tier].replace("-", "/"),
        tier_name=TIER_DIRS[p.tier],
        score=p.score,
        reasons="; ".join(p.reasons) or "种子论文",
        seed_links=links,
        cited_by=p.cited_by,
        abstract=p.abstract or "*(OpenAlex 无摘要, 打开 PDF 补)*",
    )


# --------------------------------------------------------------------------
# Obsidian 库结构
# --------------------------------------------------------------------------

VAULT_DIRS = ["00-面板", "10-文献笔记", "20-永久笔记", "30-论文地图", "90-模板"]

# Zotero Integration 插件用的 nunjucks 模板. 按标注颜色分流到不同小节,
# 这样"边看边写"才成立: 在 Zotero 里划完线, 回 Obsidian 一键就归好位了.
#
# 变量和过滤器都对照过插件源码, 别凭印象改:
# - filterBy 只认 startswith/endswith/contains 和日期比较, 没有 eq;
#   写 eq 不报错, 只是每个小节永远为空.
# - colorCategory 是插件按色相把十六进制色归的类, Zotero 阅读器默认的
#   黄/红/绿/蓝 分别落在 Yellow/Red/Green/Blue.
# - 条目的 Zotero 链接叫 desktopURI; 没有 pdfZoteroLink 这个变量.
# - date 在条目没日期时是 null, 必须先判断再 format.
ZI_TEMPLATE = """---
citekey: {{citekey}}
title: "{{title}}"
year: {% if date %}{{date | format("YYYY")}}{% endif %}
authors: [{% for a in creators %}"{{a.firstName}} {{a.lastName}}"{% if not loop.last %}, {% endif %}{% endfor %}]
doi: {{DOI}}
zotero: "{{desktopURI}}"
status: 在读
tags: [论文]
---

# {{title}}

[在 Zotero 中打开]({{desktopURI}}){% if DOI %} · [DOI](https://doi.org/{{DOI}}){% endif %}

## 🟡 关键结论
{% for annot in annotations | filterby("colorCategory", "startswith", "yellow") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 🔴 存疑与反对
{% for annot in annotations | filterby("colorCategory", "startswith", "red") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 🟢 可复用的方法
{% for annot in annotations | filterby("colorCategory", "startswith", "green") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 🔵 待深挖
{% for annot in annotations | filterby("colorCategory", "startswith", "blue") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 一句话总结

## 关联
- 链接:
"""

DASHBOARD = """# 阅读面板

## 还没开始读的核心论文

```dataview
TABLE year AS 年份, score AS 关联分, join(authors, ", ") AS 作者
FROM "10-文献笔记"
WHERE tier = "S" AND status = "未读"
SORT score DESC
```

## 在读

```dataview
TABLE tier AS 分级, year AS 年份, rating AS 评分
FROM "10-文献笔记"
WHERE status = "在读"
SORT tier ASC
```

## 读完但还没写一句话总结

```dataview
TABLE tier AS 分级, year AS 年份
FROM "10-文献笔记"
WHERE status = "已读" AND !rating
SORT file.mtime DESC
```

## 全部按分级

```dataview
TABLE length(rows) AS 篇数
FROM "10-文献笔记"
GROUP BY tier
```
"""

PERMANENT_TEMPLATE = """---
tags: [永久笔记]
created: {{date}}
---

# 

## 这个想法是什么

## 为什么重要

## 出处
- 来自:

## 反面 / 边界
"""


def cmd_setup(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser()
    if not vault.exists():
        log(f"× 库目录不存在: {vault}")
        log("  先在 Obsidian 里创建/打开这个库, 再跑一次.")
        return 1

    for d in VAULT_DIRS:
        (vault / d).mkdir(parents=True, exist_ok=True)
    for tier, name in TIERS:
        (vault / "10-文献笔记" / name).mkdir(parents=True, exist_ok=True)

    written = []
    for rel, content in [
        ("90-模板/literature-note.md", ZI_TEMPLATE),
        ("90-模板/permanent-note.md", PERMANENT_TEMPLATE),
        ("00-面板/阅读面板.md", DASHBOARD),
    ]:
        target = vault / rel
        if target.exists() and not args.force:
            log(f"· 跳过已存在的 {rel} (要覆盖加 --force)")
            continue
        target.write_text(content, encoding="utf-8")
        written.append(rel)

    log(f"✓ 库结构就绪: {vault}")
    for w in written:
        log(f"  + {w}")
    if getattr(args, "quiet", False):
        return 0
    log("")
    log("接下来在 Obsidian 里装这三个社区插件:")
    log("  1. Zotero Integration  — 从 Zotero 拉标注 (需要 Zotero 装 Better BibTeX)")
    log("  2. Dataview            — 阅读面板要靠它渲染")
    log("  3. Templater (可选)    — 新建永久笔记时自动套模板")
    log("")
    log("Zotero Integration 设置里, Import Formats 新建一条:")
    log("  Output Path : 10-文献笔记/{{citekey}}.md")
    log("  Template    : 90-模板/literature-note.md")
    return 0


# --------------------------------------------------------------------------
# install: 一键安装
# --------------------------------------------------------------------------

# Obsidian 官方插件注册表. 插件仓库会搬家 (Zotero Integration 已经搬过两次),
# 所以每次都从这里查 id -> repo, 不写死地址.
PLUGIN_REGISTRY = (
    "https://raw.githubusercontent.com/obsidianmd/obsidian-releases/"
    "master/community-plugins.json"
)
ZI_ID = "obsidian-zotero-desktop-connector"
PLUGINS = ("dataview", ZI_ID)
PLUGIN_ASSETS = [("main.js", True), ("manifest.json", True), ("styles.css", False)]

# 插件会为每个导入格式注册一条同名命令, 所以这个名字就是命令面板里搜的词.
ZI_FORMAT_NAME = "导入文献笔记"

BBT_ID = "better-bibtex@iris-advies.com"
BBT_RELEASE_API = (
    "https://api.github.com/repos/retorquere/zotero-better-bibtex/releases/latest"
)
# Zotero 开着且 Better BibTeX 加载完时返回 ready (BBT 源码 content/cayw.ts).
BBT_PROBE = "http://127.0.0.1:23119/better-bibtex/cayw?probe=true"

PY = "py" if sys.platform == "win32" else "python3"

SEEDS_TEMPLATE = """# 种子论文清单 —— 一行一篇, 井号开头是注释
#
# 支持四种写法, 混着写也行:
#   DOI        10.1038/nature14539
#   arXiv 号   1706.03762
#   OpenAlex   W2741809807
#   标题       Attention Is All You Need
#
# 建议放 3-6 篇: 太少关联信号弱, 太多主题发散.
# 把你要读的论文写在下面, 去掉行首的井号:

# 1706.03762
"""


def http_get(url: str, timeout: int = 30) -> bytes | None:
    """GET 返回内容; 404 返回 None; 其它错误抛出, 由调用方决定怎么报."""
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return None
        raise


# Windows 已知文件夹 (KNOWNFOLDERID). 用户在"属性 → 位置"里把文件夹挪到 D 盘后,
# 以系统 API 返回的为准; %USERPROFILE% 下的同名目录可能根本不存在.
FOLDERID_DOWNLOADS = "{374DE290-123F-4565-9164-39C4925E467B}"
FOLDERID_ROAMING_APPDATA = "{3EB685DB-65F9-4CF6-A03A-E3EF65729F3D}"


def _known_folder(guid: str) -> Path | None:
    """SHGetKnownFolderPath. 非 Windows 或调用失败返回 None, 调用方退回环境变量."""
    if sys.platform != "win32":
        return None
    try:
        import ctypes
        import uuid
        from ctypes import wintypes

        class GUID(ctypes.Structure):
            _fields_ = [
                ("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8),
            ]

        u = uuid.UUID(guid)
        g = GUID(u.time_low, u.time_mid, u.time_hi_version,
                 (ctypes.c_ubyte * 8)(*u.bytes[8:]))
        out = ctypes.c_wchar_p()
        hr = ctypes.windll.shell32.SHGetKnownFolderPath(
            ctypes.byref(g), 0, None, ctypes.byref(out)
        )
        try:
            return Path(out.value) if hr == 0 and out.value else None
        finally:
            ctypes.windll.ole32.CoTaskMemFree(out)
    except Exception:
        return None


def _appdata_dirs() -> list[Path]:
    """Roaming AppData 的候选, 系统 API 的结果排第一, 去重."""
    cands = [
        _known_folder(FOLDERID_ROAMING_APPDATA),
        os.environ.get("APPDATA"),
        Path.home() / "AppData" / "Roaming",
    ]
    out: list[Path] = []
    seen: set[str] = set()
    for c in cands:
        if not c:
            continue
        key = os.path.normcase(str(c))
        if key not in seen:
            seen.add(key)
            out.append(Path(c))
    return out


def obsidian_config_dir() -> Path:
    if sys.platform == "win32":
        dirs = _appdata_dirs()
        for d in dirs:
            if (d / "obsidian" / "obsidian.json").exists():
                return d / "obsidian"
        return dirs[0] / "obsidian"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "obsidian"
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "obsidian"


def zotero_profile_dirs() -> list[Path]:
    if sys.platform == "win32":
        found: list[Path] = []
        for d in _appdata_dirs():
            base = d / "Zotero" / "Zotero" / "Profiles"
            if base.is_dir():
                found += [p for p in base.iterdir() if p.is_dir() and p not in found]
        return found
    if sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support" / "Zotero" / "Profiles"
    else:
        base = Path.home() / ".zotero" / "zotero"
    if not base.is_dir():
        return []
    return [p for p in base.iterdir() if p.is_dir()]


def downloads_dir() -> Path:
    known = _known_folder(FOLDERID_DOWNLOADS)
    if known and known.is_dir():
        return known
    d = Path.home() / "Downloads"
    return d if d.is_dir() else Path.cwd()


def obsidian_running() -> bool:
    """尽力判断 Obsidian 是否开着. 判断不了就当没开, 不挡路."""
    import subprocess

    try:
        if sys.platform == "win32":
            # 中文 Windows 的 tasklist 输出 GBK; 开了 UTF-8 模式的 Python 严格解码会抛错,
            # 一抛错就会误判成"没开". 只需要匹配 ASCII 的进程名, 解不了的字节替换掉即可.
            out = subprocess.run(
                ["tasklist", "/FI", "IMAGENAME eq Obsidian.exe", "/NH"],
                capture_output=True, text=True, errors="replace", timeout=10,
            ).stdout
            return "obsidian.exe" in (out or "").lower()
        name = "Obsidian" if sys.platform == "darwin" else "obsidian"
        return subprocess.run(
            ["pgrep", "-x", name], capture_output=True, timeout=10
        ).returncode == 0
    except Exception:
        return False


def bbt_live() -> bool:
    """Zotero 开着且 Better BibTeX 已加载. 绕开系统代理, 否则本机地址可能被代理吃掉."""
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(BBT_PROBE, timeout=3) as resp:
            return resp.read().strip() == b"ready"
    except Exception:
        return False


def find_vaults(cfg_dir: Path) -> list[Path]:
    """从 Obsidian 自己的仓库列表里读出所有还存在的仓库, 最近用过的排前面.

    找不到时说清楚是哪一环断了, 不然用户和我都只能猜.
    """
    f = cfg_dir / "obsidian.json"
    if not f.exists():
        log(f"· Obsidian 的仓库列表不存在: {f}")
        return []
    try:
        data = json.loads(f.read_text(encoding="utf-8-sig"))
        entries = list((data.get("vaults") or {}).values())
    except (OSError, ValueError, AttributeError) as exc:
        log(f"· 读不懂 Obsidian 的仓库列表 {f}: {exc}")
        return []
    entries = [e for e in entries if isinstance(e, dict) and e.get("path")]
    if not entries:
        log(f"· Obsidian 的仓库列表是空的: {f}")
        return []
    entries.sort(key=lambda e: -(e.get("ts") or 0))
    out: list[Path] = []
    gone: list[str] = []
    for e in entries:
        p = Path(e["path"])
        if p.is_dir():
            if p not in out:
                out.append(p)
        else:
            gone.append(str(p))
    if not out:
        log(f"· 列表里的仓库都已经不在了: {'; '.join(gone)}")
    return out


# 扫盘时跳过的目录: 系统目录、依赖目录, 以及大到扫不完又不可能放笔记的地方.
SKIP_DIRS = {
    "appdata", "node_modules", "windows", "program files", "program files (x86)",
    "programdata", "library", "system volume information", "__pycache__",
    "site-packages", "anaconda3", "miniconda3", "go", "sdk",
}


def _windows_drives() -> list[Path]:
    lister = getattr(os, "listdrives", None)   # Python 3.12+
    if lister:
        try:
            return [Path(d) for d in lister()]
        except OSError:
            pass
    # 跳过 A: B: (软驱位, 查询空驱动器可能弹窗)
    return [Path(f"{c}:/") for c in "CDEFGHIJKLMNOPQRSTUVWXYZ" if os.path.isdir(f"{c}:/")]


def vault_search_roots() -> list[Path]:
    """扫盘的起点: 用户目录, 加上 Windows 上除系统盘外的其它盘 (很多人把资料放 D 盘)."""
    roots = [Path.home()]
    if sys.platform == "win32":
        system = (os.environ.get("SystemDrive") or "C:")[:1].upper()
        for d in _windows_drives():
            if str(d)[:1].upper() != system:
                roots.append(d)
    return roots


def scan_for_vaults(roots: Iterable[Path], depth: int = 4, limit: int = 40000) -> list[Path]:
    """找带 .obsidian 子目录的文件夹, 这就是 Obsidian 仓库的标志.

    不进仓库内部、不进隐藏和系统目录、限深限量, 一个大硬盘也不会扫到天荒地老.
    最近用过的 (.obsidian 最近被改过的) 排前面.
    """
    found: list[Path] = []
    seen: set[str] = set()
    visited = 0
    for root in roots:
        stack = [(Path(root), 0)]
        while stack:
            d, lvl = stack.pop()
            try:
                key = os.path.normcase(str(d.resolve()))
            except (OSError, RuntimeError):
                continue
            if key in seen:
                continue
            seen.add(key)
            visited += 1
            if visited > limit:
                return _by_recency(found)
            try:
                with os.scandir(d) as it:
                    entries = list(it)
            except OSError:
                continue
            if any(e.name == ".obsidian" and _is_dir(e) for e in entries):
                found.append(d)
                continue
            if lvl >= depth:
                continue
            for e in entries:
                name = e.name
                if name.startswith((".", "$")) or name.lower() in SKIP_DIRS:
                    continue
                if _is_dir(e, follow=False):
                    stack.append((Path(e.path), lvl + 1))
    return _by_recency(found)


def _is_dir(entry: os.DirEntry, follow: bool = True) -> bool:
    try:
        return entry.is_dir(follow_symlinks=follow)
    except OSError:
        return False


def _by_recency(vaults: list[Path]) -> list[Path]:
    def mtime(v: Path) -> float:
        try:
            return (v / ".obsidian").stat().st_mtime
        except OSError:
            return 0.0
    return sorted(vaults, key=mtime, reverse=True)


def choose_vault(vaults: list[Path], ask=None) -> Path | None:
    ask = ask or input
    if len(vaults) == 1:
        log(f"✓ 找到 Obsidian 库: {vaults[0]}")
        return vaults[0]
    if vaults:
        log("找到多个 Obsidian 库:")
        for i, v in enumerate(vaults, 1):
            log(f"  {i}. {v}")
        while True:
            ans = ask("装到哪一个? 输入编号 (直接回车放弃): ").strip()
            if not ans:
                return None
            if ans.isdigit() and 1 <= int(ans) <= len(vaults):
                return vaults[int(ans) - 1]
            log(f"  请输入 1 到 {len(vaults)} 之间的数字")
    log("")
    log("没找到任何 Obsidian 仓库.")
    log("  · 从没建过仓库: 直接回车退出, 打开 Obsidian 新建一个仓库, 关掉 Obsidian 再跑一次")
    log("  · 建过: Obsidian 左下角点仓库名 → 管理仓库 (Manage vaults), 复制路径粘贴到这里")
    while True:
        ans = ask("粘贴库的路径 (直接回车放弃): ").strip().strip('"').strip("'")
        if not ans:
            return None
        p = Path(ans).expanduser()
        if p.is_dir():
            return p
        log(f"  × 这个目录不存在: {p}")


def load_plugin_registry(get=None) -> dict[str, str]:
    get = get or http_get
    body = get(PLUGIN_REGISTRY)
    if not body:
        raise RuntimeError("拿不到 Obsidian 插件注册表")
    return {p["id"]: p["repo"] for p in json.loads(body.decode("utf-8"))}


def _plugin_version(repo: str, plugin_id: str, get) -> str | None:
    """读仓库默认分支的 manifest.json 拿版本号, 这也是 Obsidian 自己装插件的方式.

    "最新发布" 不一定是它: 预发布版或者漏标 latest 的发布都会让 latest 链接指错.
    读不到就返回 None, 由调用方退回 latest.
    """
    try:
        body = get(f"https://raw.githubusercontent.com/{repo}/HEAD/manifest.json")
    except Exception:
        return None
    if not body:
        return None
    try:
        manifest = json.loads(body.decode("utf-8"))
    except ValueError:
        return None
    if manifest.get("id") != plugin_id:
        raise RuntimeError(
            f"{repo} 的 manifest 写的是 {manifest.get('id')!r}, 不是 {plugin_id}, 拒绝安装"
        )
    return manifest.get("version") or None


def install_plugin(vault: Path, plugin_id: str, registry: dict[str, str], get=None) -> None:
    get = get or http_get
    repo = registry.get(plugin_id)
    if not repo:
        raise RuntimeError(f"Obsidian 插件注册表里没有 {plugin_id}")

    version = _plugin_version(repo, plugin_id, get)
    bases = [f"https://github.com/{repo}/releases/latest/download"]
    if version:
        bases.insert(0, f"https://github.com/{repo}/releases/download/{version}")

    files: dict[str, bytes] = {}
    for base in bases:
        files = {}
        complete = True
        for name, required in PLUGIN_ASSETS:
            body = get(f"{base}/{name}")
            if body is None:
                if required:
                    complete = False
                    break
                continue
            files[name] = body
        if complete:
            break
    else:
        raise RuntimeError(f"{plugin_id} 的发布里缺 main.js 或 manifest.json")
    # 全部下完再落盘: 下到一半断网, 不会留下一个装了一半、Obsidian 加载时报错的插件.
    folder = vault / ".obsidian" / "plugins" / plugin_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (folder / name).write_bytes(body)


def plugin_installed(vault: Path, plugin_id: str) -> bool:
    return (vault / ".obsidian" / "plugins" / plugin_id / "manifest.json").exists()


def enabled_plugins(vault: Path) -> list[str]:
    f = vault / ".obsidian" / "community-plugins.json"
    try:
        loaded = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    return [x for x in loaded if isinstance(x, str)] if isinstance(loaded, list) else []


def enable_plugins(vault: Path, ids: Iterable[str]) -> None:
    """合并进启用列表: 保留你原有的插件和顺序, 不重复添加."""
    current = enabled_plugins(vault)
    for i in ids:
        if i not in current:
            current.append(i)
    f = vault / ".obsidian" / "community-plugins.json"
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(current, indent=2), encoding="utf-8")


def zi_export_format() -> dict:
    # 字段名来自插件 src/types.ts 的 ExportFormat.
    return {
        "name": ZI_FORMAT_NAME,
        "outputPathTemplate": "10-文献笔记/{{citekey}}.md",
        "imageOutputPathTemplate": "10-文献笔记/附件/{{citekey}}/",
        "imageBaseNameTemplate": "image",
        "templatePath": "90-模板/literature-note.md",
    }


def configure_zotero_integration(vault: Path) -> bool:
    """往插件配置里加我们的导入格式. 插件加载时会把 data.json 和默认值合并,
    所以只写 exportFormats 是安全的; 你原有的设置和格式一律保留."""
    f = vault / ".obsidian" / "plugins" / ZI_ID / "data.json"
    cfg: dict = {}
    if f.exists():
        try:
            cfg = json.loads(f.read_text(encoding="utf-8"))
        except ValueError:
            log(f"! {f} 不是合法 JSON, 没动它. 请在插件设置里手动加导入格式.")
            return False
        if not isinstance(cfg, dict):
            return False
    formats = cfg.get("exportFormats")
    if not isinstance(formats, list):
        formats = []
    if not any(isinstance(x, dict) and x.get("name") == ZI_FORMAT_NAME for x in formats):
        formats.append(zi_export_format())
    cfg["exportFormats"] = formats
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
    return True


def bbt_installed(profiles: Iterable[Path]) -> bool:
    for p in profiles:
        ext = p / "extensions"
        if (ext / f"{BBT_ID}.xpi").exists() or (ext / BBT_ID).is_dir():
            return True
    return False


def download_bbt(dest_dir: Path, get=None) -> Path:
    get = get or http_get
    body = get(BBT_RELEASE_API)
    if not body:
        raise RuntimeError("拿不到 Better BibTeX 的发布信息")
    assets = json.loads(body.decode("utf-8")).get("assets") or []
    # 发布里同时挂着 .xpi 和 .xpi.sha256, 要的是前者.
    xpi = next((a for a in assets if str(a.get("name", "")).endswith(".xpi")), None)
    if not xpi:
        raise RuntimeError("Better BibTeX 最新发布里没有 .xpi")
    data = get(xpi["browser_download_url"], timeout=120)
    if not data:
        raise RuntimeError("Better BibTeX 下载失败")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / xpi["name"]
    dest.write_bytes(data)
    return dest


def cmd_install(args: argparse.Namespace) -> int:
    failures: list[str] = []
    todo: list[str] = []
    log("paperkit 一键安装")
    log("")

    if args.vault:
        vault = Path(args.vault).expanduser()
        if not vault.is_dir():
            log(f"× 库目录不存在: {vault}")
            return 1
    else:
        vaults = find_vaults(obsidian_config_dir())
        if not vaults:
            log("→ 直接在硬盘上找带 .obsidian 文件夹的目录 …")
            vaults = scan_for_vaults(vault_search_roots())
        vault = choose_vault(vaults)
        if vault is None:
            log("× 没有可用的库. 先在 Obsidian 里新建一个库, 再跑一次.")
            return 1

    # 目录和模板是纯本地操作, 放在最前面, 后面联网的步骤失败也不影响它.
    cmd_setup(argparse.Namespace(vault=str(vault), force=False, quiet=True))

    if not args.skip_plugins:
        # Obsidian 开着时改插件列表没用: 它退出时会拿内存里的旧列表覆盖回去.
        if obsidian_running():
            input("! Obsidian 正开着. 请先彻底关掉它, 然后回到这里按回车 …")
        if obsidian_running():
            failures.append("Obsidian 还开着, 跳过了插件安装. 关掉 Obsidian 后再跑一次本命令.")
        else:
            log("→ 从 GitHub 下载 Obsidian 插件 …")
            try:
                registry = load_plugin_registry()
            except Exception as exc:
                registry = None
                failures.append(f"下载 Obsidian 插件列表失败: {exc}")
            if registry is not None:
                ready = []
                for pid in PLUGINS:
                    if plugin_installed(vault, pid):
                        log(f"✓ 插件已存在, 不重装: {pid}")
                        ready.append(pid)
                        continue
                    try:
                        install_plugin(vault, pid, registry)
                        log(f"✓ 装好插件: {pid}")
                        ready.append(pid)
                    except Exception as exc:
                        failures.append(f"插件 {pid} 安装失败: {exc}")
                if ready:
                    enable_plugins(vault, ready)
                    log("✓ 已加入启用列表")
            if plugin_installed(vault, ZI_ID) and configure_zotero_integration(vault):
                log(f"✓ Zotero Integration 已配好导入格式「{ZI_FORMAT_NAME}」")
            todo.append(
                "打开 Obsidian → 设置 → 第三方插件. 如果看到「安全模式」或「开启社区插件」, "
                "点开启; 本来就开着就不用管"
            )

    if not args.skip_zotero:
        profiles = zotero_profile_dirs()
        if bbt_live() or bbt_installed(profiles):
            log("✓ Better BibTeX 已安装")
        else:
            if not profiles:
                todo.append(
                    "没找到 Zotero 的配置目录. 没装的话先装最新版 Zotero "
                    "(Better BibTeX 要求 8.0.1 以上): https://www.zotero.org/download/ ; "
                    "已经装了就忽略这条"
                )
            log("→ 正在从 GitHub 下载 Better BibTeX 安装包, 网速慢时要一两分钟 …")
            log("  (等太久可以 Ctrl+C, 改用浏览器下载, 装好后重跑本命令会自动跳过这步)")
            try:
                xpi = download_bbt(downloads_dir())
                log(f"✓ 已下载 Better BibTeX: {xpi}")
                todo.append(
                    "Zotero → 工具 → 插件 → 右上角齿轮 → 从文件安装插件 → "
                    f"选 {xpi} → 重启 Zotero"
                )
            except Exception as exc:
                failures.append(f"下载 Better BibTeX 失败: {exc}")
                todo.append(
                    "手动下载 Better BibTeX: https://github.com/retorquere/"
                    "zotero-better-bibtex/releases/latest (选 .xpi), "
                    "再在 Zotero → 工具 → 插件 → 齿轮 → 从文件安装插件"
                )

    seeds = Path.cwd() / "seeds.txt"
    if not seeds.exists():
        seeds.write_text(SEEDS_TEMPLATE, encoding="utf-8")
        log(f"✓ 建好种子清单: {seeds}")

    log("")
    if failures:
        log("没做成的:")
        for f in failures:
            log(f"  × {f}")
        log("")
    if todo:
        log("还需要你手动做的:")
        for i, t in enumerate(todo, 1):
            log(f"  {i}. {t}")
        log("")
    log("都做完后, 开着 Zotero 跑体检:")
    log(f'  {PY} paperkit.py doctor --vault "{vault}"')
    log("")
    log("然后编辑 seeds.txt 写上你要读的论文, 再跑:")
    log(f'  {PY} paperkit.py discover --seeds seeds.txt --out papers --vault "{vault}"')
    return 1 if failures else 0


# --------------------------------------------------------------------------
# seeds: 从已经下载的 PDF 生成种子清单
# --------------------------------------------------------------------------

PDF_DOI_RE = re.compile(rb"10\.\d{4,9}/[^\s\"'<>\[\]{}]+")
META_DOI_RE = re.compile(
    rb"(?:prism:doi|pdfx:doi|crossmark:doi|dc:identifier|/doi)"
    rb"(?:\s*=\s*[\"']"        # 属性写法: crossmark:DOI="..."
    rb"|(?:\s+[^<>]*)?>"          # 元素写法, 标签里可能还带 xmlns 等属性
    rb"|\s*\()"                  # PDF Info 字典: /doi (...)
    rb"\s*(?:doi:\s*|https?://(?:dx\.)?doi\.org/)?"
    rb"(10\.\d{4,9}/[^\s\"'<>\[\]{}]+)",
    re.I,
)
# arXiv 页边水印: "arXiv:1706.03762v5 [cs.CL] 6 Dec 2017". 要求带版本号和分类,
# 这样参考文献里引用的 "arXiv:xxxx.xxxxx" 不会被当成本篇.
ARXIV_MARK_RE = re.compile(rb"arXiv:(\d{4}\.\d{4,5})v\d+\s*\[[A-Za-z\-]+(?:\.[A-Za-z\-]+)?\]")
ARXIV_NAME_RE = re.compile(r"^(\d{4}\.\d{4,5})(?:v\d+)?\b")
STREAM_RE = re.compile(rb"stream\r?\n")


def _clean_doi(raw: bytes) -> str | None:
    s = raw.decode("latin-1").replace("\\", "")
    # 在第一个不配对的右括号处截断: "(https://doi.org/10.1/x)Tj" 这类
    depth = 0
    for i, ch in enumerate(s):
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth < 0:
                s = s[:i]
                break
    s = s.rstrip(".,;:")
    for suffix in ("/abstract", "/full", "/epdf", "/pdf", ".pdf"):
        if s.lower().endswith(suffix):
            s = s[: -len(suffix)]
    m = re.fullmatch(r"10\.\d{4,9}/(\S+)", s)
    # 排版常把 DOI 拆成几段, 留下 "10.1016/j" "10.3389/fpsyg" 这种残片;
    # 参考文献里好几条在同一处断开, 残片还会"反复出现". 真实 DOI 后缀几乎都带数字.
    if not m or len(m.group(1)) < 4 or not re.search(r"\d", m.group(1)):
        return None
    return s.lower()


def _pdf_blobs(data: bytes, cap: int = 40_000_000) -> Iterator[bytes]:
    """原始字节, 以及每个能 Flate 解压的流. 限量, 大扫描件也不会吃光内存."""
    yield data
    total, pos = 0, 0
    while True:
        m = STREAM_RE.search(data, pos)
        if not m:
            return
        start = m.end()
        end = data.find(b"endstream", start)
        if end < 0:
            return
        pos = end + len(b"endstream")
        try:
            out = zlib.decompressobj().decompress(data[start:end], 5_000_000)
        except zlib.error:
            continue
        if out:
            total += len(out)
            yield out
            if total > cap:
                return


TJ_RE = re.compile(rb"\[((?:[^\[\]\\]|\\.)*)\]\s*TJ", re.S)
LIT_RE = re.compile(rb"\(((?:[^\\()]|\\.)*)\)", re.S)


def _join_tj(blob: bytes) -> bytes:
    """把 TJ 数组里被字距调整拆开的字符串拼回来: [(10.3389/fpsyg.)-20(2024.1)]TJ.
    只对标准编码字体有效; CID 字体的字形编号本来就读不出, 那就靠元数据和链接."""
    if b"TJ" not in blob:
        return b""
    pieces = []
    for m in TJ_RE.finditer(blob):
        joined = b"".join(LIT_RE.findall(m.group(1)))
        pieces.append(re.sub(rb"\\([()\\])", rb"\1", joined))
    return b"\n".join(pieces)


def _decode_pdf_bytes(b: bytes) -> str:
    if b.startswith(b"\xfe\xff"):
        return b[2:].decode("utf-16-be", errors="replace")
    if b.startswith(b"\xef\xbb\xbf"):
        b = b[3:]
    try:
        return b.decode("utf-8")
    except UnicodeDecodeError:
        return b.decode("latin-1")


def _read_pdf_string(data: bytes, i: int) -> str | None:
    """从 data[i] 读一个 PDF 字符串: (字面量, 支持嵌套括号和转义) 或 <十六进制>."""
    while i < len(data) and data[i:i + 1].isspace():
        i += 1
    if data[i:i + 1] == b"<":
        end = data.find(b">", i)
        if end < 0:
            return None
        hexs = re.sub(rb"\s", b"", data[i + 1:end])
        if len(hexs) % 2:
            hexs += b"0"
        try:
            return _decode_pdf_bytes(bytes.fromhex(hexs.decode("ascii")))
        except ValueError:
            return None
    if data[i:i + 1] != b"(":
        return None
    out = bytearray()
    depth, j = 1, i + 1
    escapes = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
    while j < len(data) and depth:
        c = data[j]
        if c == 0x5C:  # 反斜杠
            j += 1
            if j >= len(data):
                break
            e = data[j]
            if e in escapes:
                out.append(escapes[e])
            elif 0x30 <= e <= 0x37:
                k = j
                while k < min(j + 3, len(data)) and 0x30 <= data[k] <= 0x37:
                    k += 1
                out.append(int(data[j:k], 8) & 0xFF)
                j = k - 1
            elif e in (10, 13):
                pass  # 续行
            else:
                out.append(e)
        elif c == 0x28:
            depth += 1
            out.append(c)
        elif c == 0x29:
            depth -= 1
            if depth:
                out.append(c)
        else:
            out.append(c)
        j += 1
    return _decode_pdf_bytes(bytes(out))


INFO_KEYS = (b"/Producer", b"/Creator", b"/CreationDate", b"/ModDate", b"/Author")
OUTLINE_KEYS = (b"/Parent", b"/Dest", b"/First", b"/Last", b"/Count", b"/Prev", b"/Next")


def _in_info_dict(blob: bytes, k: int) -> bool:
    """书签 (outline) 条目也用 /Title 键, 存的是"（一）服务质量"这种小节名.
    Info 字典带 /Producer 之类的键, 书签带 /Parent /Dest 之类的键, 据此区分."""
    start = blob.rfind(b"<<", 0, k)
    end = blob.find(b">>", k)
    if start < 0 or end < 0:
        return False
    region = blob[start:end]
    if any(x in region for x in OUTLINE_KEYS):
        return False
    return any(x in region for x in INFO_KEYS)


def _useful_title(t: str | None) -> bool:
    if not t:
        return False
    t = t.strip()
    has_cjk = re.search(r"[一-鿿]{4,}", t)
    if len(t) < 8 and not has_cjk:
        return False
    low = t.lower()
    if any(j in low for j in ("microsoft word", "untitled", ".doc", ".pdf", ".tex",
                              "powerpoint", "slide 1")):
        return False
    if re.match(r"1-s2\.0-", t) or re.fullmatch(r"[\d\W_]+", t):
        return False
    if re.fullmatch(r"[\w\-]+\.\w{2,4}", t) and not has_cjk:
        return False
    # 英文标题不会只有一个词: main / manuscript / xmp-paper 这类是文件名不是标题.
    if not has_cjk and " " not in t:
        return False
    # 每个词都是文件名惯用词 ("paper final", "manuscript v2") 的也不是标题.
    # 不能简单要求至少三个词: Nature 那篇著名综述就叫 "Deep learning".
    words = re.findall(r"[a-z]+|\d+", low)
    if words and all(w in FILENAME_WORDS or w.isdigit() for w in words):
        return False
    return True


FILENAME_WORDS = {
    "paper", "final", "draft", "manuscript", "main", "full", "text", "article",
    "preprint", "revised", "revision", "submission", "submitted", "camera", "ready",
    "accepted", "version", "v", "copy", "new", "old", "fulltext", "download", "file",
    "document", "doc", "pdf", "the", "of", "and",
}


# 个人命名习惯: 作者(等)_年份_自己写的概括_期刊, 作者年份可能套在花括号里.
PERSONAL_NAME_RE = re.compile(r"^\{?([A-Za-z][A-Za-z\-']*)(等)?_((?:19|20)\d{2})\}?_(.+)$")


def filename_hint(path: Path) -> str | None:
    """认不出的文件, 从个人命名里读出 作者/年份/期刊, 方便手动去查 DOI."""
    m = PERSONAL_NAME_RE.match(path.stem)
    if not m:
        return None
    author, etal, year, rest = m.groups()
    journal = rest.rsplit("_", 1)[1] if "_" in rest else None
    who = f"{author} 等" if etal else author
    return ", ".join(x for x in (who, year, journal) if x)


def _title_from_filename(path: Path) -> str | None:
    s = path.stem
    if PERSONAL_NAME_RE.match(s):
        return None  # 中间那段是自己写的概括, 拿去搜只会搜到不相干的论文
    s = re.sub(r"\s*\(\d+\)$", "", s)            # 重复下载: xxx (1)
    s = re.sub(r"\s*-\s*副本(\s*\(\d+\))?$", "", s)
    if re.search(r"[一-鿿]", s) and "_" in s:
        head, tail = s.rsplit("_", 1)
        if len(tail) <= 6:                        # 知网命名: 标题_第一作者
            s = head
    s = s.replace("_", " ")
    if " " not in s and s.count("-") >= 2:        # slug 写法: graph-attention-networks
        s = s.replace("-", " ")
    s = re.sub(r"\s+", " ", s).strip()
    return s if _useful_title(s) else None


def pdf_identifiers(path: Path) -> dict:
    """认出一篇 PDF 自己是谁. 顺序:
    元数据里的 DOI > 正文反复出现的 DOI (期刊页眉页脚) > arXiv 编号 >
    全文唯一的 DOI > PDF 标题 > 文件名.

    参考文献里有几十个别人的 DOI, 每个只出现一次; 所以"很多个、各出现一次"
    时宁可退回标题, 也不挑一个可能是引用文献的 DOI.
    """
    res = {"doi": None, "arxiv": None, "title": None, "how": "认不出"}
    try:
        with open(path, "rb") as fh:
            data = fh.read(80_000_000)
    except OSError:
        data = b""

    meta_doi = None
    counts: Counter = Counter()
    arxiv = None
    title = None
    for i, blob in enumerate(_pdf_blobs(data)):
        if not meta_doi:
            m = META_DOI_RE.search(blob)
            if m:
                meta_doi = _clean_doi(m.group(1))
        here: Counter = Counter()
        for raw in PDF_DOI_RE.findall(blob):
            d = _clean_doi(raw)
            if d:
                here[d] += 1
        if i:  # 整个文件那一块多是压缩乱码, 只对解压出来的流拼 TJ
            joined: Counter = Counter()
            for raw in PDF_DOI_RE.findall(_join_tj(blob)):
                d = _clean_doi(raw)
                if d:
                    joined[d] += 1
            # 同一处 DOI 在原始流和拼接文本里各出现一次, 取较大值而不是相加,
            # 不然一条参考文献会被算成"反复出现".
            for d, n in joined.items():
                here[d] = max(here[d], n)
        counts.update(here)
        if not arxiv:
            m = ARXIV_MARK_RE.search(blob)
            if m:
                arxiv = m.group(1).decode()
        if title is None:
            k = blob.find(b"/Title")
            while k >= 0 and title is None:
                if _in_info_dict(blob, k):
                    t = _read_pdf_string(blob, k + len(b"/Title"))
                    if _useful_title(t):
                        title = re.sub(r"\s+", " ", t).strip()
                k = blob.find(b"/Title", k + 1)
        if title is None:
            m = re.search(rb"<dc:title>\s*<rdf:Alt>\s*<rdf:li[^>]*>(.*?)</rdf:li>", blob, re.S)
            if m:
                t = html.unescape(m.group(1).decode("utf-8", errors="replace"))
                if _useful_title(t):
                    title = re.sub(r"\s+", " ", t).strip()

    name_arxiv = ARXIV_NAME_RE.match(path.stem)
    arxiv = arxiv or (name_arxiv.group(1) if name_arxiv else None)
    top = counts.most_common(2)

    if meta_doi:
        res.update(doi=meta_doi, how="元数据 DOI")
    elif top and top[0][1] >= 2 and (len(top) == 1 or top[0][1] > top[1][1]):
        res.update(doi=top[0][0], how="正文反复出现的 DOI")
    elif arxiv:
        res.update(arxiv=arxiv, how="arXiv 编号")
    elif len(counts) == 1:
        res.update(doi=top[0][0], how="全文唯一的 DOI")
    elif title:
        res.update(title=title, how="PDF 标题")
    else:
        t = _title_from_filename(path)
        if t:
            res.update(title=t, how="文件名")
    return res


def seed_line(ids: dict, path: Path) -> str | None:
    key = ids.get("doi") or (f"arXiv:{ids['arxiv']}" if ids.get("arxiv") else None) or ids.get("title")
    if not key:
        return None
    return f"{key}  # {path.name} ({ids['how']})"


def cmd_seeds(args: argparse.Namespace) -> int:
    src = Path(args.from_pdfs).expanduser()
    if not src.is_dir():
        log(f"× 文件夹不存在: {src}")
        return 1
    pdfs = sorted(p for p in src.rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")
    if not pdfs:
        log(f"× {src} 里没有 PDF")
        return 1

    out = Path(args.out).expanduser()
    have = {x.lower() for x in (read_seeds(out) if out.exists() else [])}
    lines: list[str] = []
    how_count: Counter = Counter()
    unknown: list[Path] = []
    log(f"→ 扫描 {len(pdfs)} 篇 PDF …")
    for p in pdfs:
        rel = p.relative_to(src)
        ids = pdf_identifiers(p)
        line = seed_line(ids, p)
        if not line:
            unknown.append(rel)
            hint = filename_hint(p)
            if hint:
                log(f"  ? {rel}: 认不出 — 按文件名是 {hint}, 打开 PDF 首页找到 DOI 手动补进清单")
            else:
                log(f"  ? {rel}: 认不出, 跳过")
            continue
        key = line.split("  # ", 1)[0]
        if key.lower() in have:
            log(f"  = {rel}: 已在清单里")
            continue
        have.add(key.lower())
        lines.append(line)
        how_count[ids["how"]] += 1
        log(f"  ✓ {rel}: {ids['how']} → {key[:70]}")

    if lines:
        head = "" if out.exists() else "# 种子论文清单 —— 一行一篇, 井号后面是注释\n"
        body = out.read_text(encoding="utf-8-sig") if out.exists() else ""
        if body and not body.endswith("\n"):
            body += "\n"
        stamp = time.strftime("%Y-%m-%d")
        out.write_text(
            head + body + f"\n# 来自 {src} ({stamp})\n" + "\n".join(lines) + "\n",
            encoding="utf-8",
        )

    log("")
    log(f"新增 {len(lines)} 条种子 → {out}")
    for how, n in how_count.most_common():
        log(f"  {how}: {n}")
    if unknown:
        log(f"  认不出: {len(unknown)} 篇 (扫描版或文件名是一串编号, 可以手动把标题写进清单)")
    if how_count.get("PDF 标题") or how_count.get("文件名"):
        log("按标题认的不一定准, discover 时看一眼每条种子匹配到的论文对不对.")
    return 0


# --------------------------------------------------------------------------
# config: 记住邮箱等设置 (存在 paperkit.py 旁边, 只在你电脑上)
# --------------------------------------------------------------------------

CONFIG_PATH = Path(__file__).resolve().parent / "paperkit.json"
EMAIL_RE = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


def load_config() -> dict:
    try:
        cfg = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        return cfg if isinstance(cfg, dict) else {}
    except (OSError, ValueError):
        return {}


def resolve_mailto(flag: str | None) -> str | None:
    return flag or os.environ.get("PAPERKIT_MAILTO") or load_config().get("mailto")


def citers_budget(explicit: int | None, n_seeds: int) -> int:
    """每篇种子回溯多少引用它的文献. 种子一多, 总量要控制住, 不然候选集爆炸."""
    if explicit is not None:
        return explicit
    return 150 if n_seeds <= 10 else max(30, 1500 // n_seeds)


def cmd_config(args: argparse.Namespace) -> int:
    cfg = load_config()
    if args.mailto:
        m = args.mailto.strip()
        if not EMAIL_RE.fullmatch(m):
            log(f"× 这不像邮箱: {m}")
            return 1
        user, domain = m.rsplit("@", 1)
        cfg["mailto"] = f"{user}@{domain.lower()}"
        CONFIG_PATH.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"✓ 记住了邮箱 {cfg['mailto']}")
        log(f"  存在 {CONFIG_PATH}, 只在你电脑上, 以后 discover 自动用它")
        return 0
    log(json.dumps(cfg, ensure_ascii=False, indent=2) if cfg else "(还没有任何设置)")
    return 0


# --------------------------------------------------------------------------
# discover: 主流程
# --------------------------------------------------------------------------


def read_seeds(path: Path) -> list[str]:
    # utf-8-sig: 老版 Windows 记事本存的 UTF-8 带 BOM, 不剥掉第一行就解析失败.
    raw = path.read_text(encoding="utf-8-sig").splitlines()
    out = []
    for line in raw:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        # 行内注释要求 # 前后都有空白, 这样 "C# in Depth" 这种标题不受影响.
        line = re.sub(r"\s+#\s.*$", "", line).strip()
        if line:
            out.append(line)
    return out


def cmd_discover(args: argparse.Namespace) -> int:
    seeds_path = Path(args.seeds).expanduser()
    if not seeds_path.exists():
        log(f"× 找不到种子文件: {seeds_path}")
        return 1

    out = Path(args.out).expanduser()
    vault = Path(args.vault).expanduser() if args.vault else None
    if vault and not vault.exists():
        log(f"× 库目录不存在: {vault}")
        return 1

    mailto = resolve_mailto(args.mailto)
    client = Client(mailto=mailto)
    if not mailto:
        log("! 没设邮箱, 按 1 请求/秒 跑. 跑一次 paperkit.py config --mailto 你的邮箱, 以后都快 10 倍.")

    lines = read_seeds(seeds_path)
    if not lines:
        log(f"× {seeds_path.name} 里还没写论文. 用记事本打开它, 一行写一篇, 去掉行首的井号.")
        return 1
    log(f"→ 解析 {len(lines)} 条种子 …")
    seeds: list[Paper] = []
    for ln in lines:
        p = resolve_seed(client, ln)
        if not p:
            log(f"  × 没找到: {ln}")
            continue
        p.is_seed = True
        p.tier = "S"
        seeds.append(p)
        log(f"  ✓ {p.year} {p.title[:70]}")
    if not seeds:
        log("× 一条种子都没解析出来, 检查 DOI/标题拼写.")
        return 1

    log(f"→ 围绕 {len(seeds)} 篇种子展开关联检索 …")
    candidates = expand(client, seeds, citers_budget(args.citers_per_seed, len(seeds)))
    log(f"  得到 {len(candidates)} 篇候选")

    this_year = time.gmtime().tm_year
    score_all(candidates, seeds, this_year)

    ranked = sorted(candidates.values(), key=lambda p: -p.score)
    if args.min_year:
        ranked = [p for p in ranked if (p.year or 0) >= args.min_year]
    ranked = ranked[: args.max]
    assign_tiers(ranked, args.top_s, args.top_a)

    # 种子本身永远是 S 级, 排在最前面.
    final = seeds + ranked

    out.mkdir(parents=True, exist_ok=True)
    for _, name in TIERS:
        (out / name).mkdir(exist_ok=True)

    if not args.no_pdf:
        log("→ 下载开放获取 PDF …")
        ok = 0
        for p in final:
            if not p.pdf_url or (p.is_seed and args.have_seeds):
                continue
            dest = out / TIER_DIRS[p.tier] / f"{p.slug()}.pdf"
            if dest.exists():
                p.pdf_path = str(dest.resolve())
                ok += 1
                continue
            if download_pdf(p.pdf_url, dest):
                p.pdf_path = str(dest.resolve())
                ok += 1
                log(f"  ↓ [{p.tier}] {p.slug()[:64]}")
        closed = sum(1 for p in final if not p.pdf_path)
        log(f"  拿到 {ok} 篇 PDF, {closed} 篇没有开放获取版本 (Zotero 里可以再试抓取)")

    # 每级一个 RIS: Zotero 里 "导入到新分类" 会拿文件名当分类名, 分级就自动建好了.
    for tier, name in TIERS:
        group = [p for p in final if p.tier == tier and not (p.is_seed and args.have_seeds)]
        if not group:
            continue
        (out / f"{name}.ris").write_text(
            "".join(to_ris(p) for p in group), encoding="utf-8"
        )
        log(f"  ✓ {name}.ris ({len(group)} 篇)")

    seed_titles = {s.oid: f"[[{s.slug()}]]" for s in seeds}
    if vault:
        notes_root = vault / "10-文献笔记"
        n = 0
        for p in final:
            folder = notes_root / TIER_DIRS[p.tier]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{p.slug()}.md"
            if target.exists() and not args.force:
                continue
            target.write_text(render_note(p, seed_titles), encoding="utf-8")
            n += 1
        log(f"  ✓ 写入 {n} 篇 Obsidian 文献笔记 → {notes_root}")

        (vault / "30-论文地图" / "主题地图.md").write_text(
            render_map(seeds, final), encoding="utf-8"
        )
        log("  ✓ 主题地图.md")

    (out / "paperkit-result.json").write_text(
        json.dumps(
            [
                {
                    "oid": p.oid, "title": p.title, "year": p.year, "doi": p.doi,
                    "tier": p.tier, "score": p.score, "reasons": p.reasons,
                    "cited_by": p.cited_by, "pdf": p.pdf_path, "seed": p.is_seed,
                }
                for p in final
            ],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    log("")
    log(f"完成. OpenAlex 请求 {client.calls} 次. 产物在 {out}")
    log("下一步: Zotero → 文件 → 导入 → 选中 .ris → 勾选「放入新分类」")
    return 0


def render_map(seeds: list[Paper], final: list[Paper]) -> str:
    out = ["# 主题地图", "", "## 种子论文", ""]
    for s in seeds:
        out.append(f"- [[{s.slug()}]] ({s.year})")
    for tier, name in TIERS:
        group = [p for p in final if p.tier == tier and not p.is_seed]
        if not group:
            continue
        out += ["", f"## {name}", ""]
        for p in group:
            why = "; ".join(p.reasons[:2]) or "—"
            out.append(f"- [[{p.slug()}]] · {p.year} · 被引 {p.cited_by} · _{why}_")
    return "\n".join(out) + "\n"


def cmd_doctor(args: argparse.Namespace) -> int:
    """装完之后跑一下, 确认每个环节都真的接上了."""
    vault = Path(args.vault).expanduser()
    problems = 0

    def check(ok: bool, good: str, bad: str) -> None:
        nonlocal problems
        log(("✓ " + good) if ok else ("× " + bad))
        if not ok:
            problems += 1

    check(vault.exists(), f"库存在: {vault}", f"库不存在: {vault}")
    if not vault.exists():
        return 1

    for d in VAULT_DIRS:
        check((vault / d).is_dir(), f"目录 {d}", f"缺目录 {d} — 跑 paperkit.py setup")
    check(
        (vault / "90-模板" / "literature-note.md").exists(),
        "Zotero Integration 模板已就位",
        "缺 90-模板/literature-note.md",
    )

    enabled = set(enabled_plugins(vault))
    for pid, label in [(ZI_ID, "Zotero Integration"), ("dataview", "Dataview")]:
        installed = plugin_installed(vault, pid)
        check(
            installed,
            f"插件已安装: {label}",
            f"插件没装: {label} — 关掉 Obsidian 后跑 {PY} paperkit.py install",
        )
        if installed:
            check(
                pid in enabled,
                f"插件已启用: {label}",
                f"插件装了但没启用: {label} — Obsidian → 设置 → 第三方插件 里打开",
            )

    # 不查 .bib: Zotero Integration 直接调 Better BibTeX 的本地接口, 从来不读 .bib.
    check(
        bbt_live(),
        "Zotero 在运行, Better BibTeX 已连上",
        "连不上 Better BibTeX — 先打开 Zotero; 开着还不行就是 Better BibTeX 没装",
    )

    log("")
    log("全部通过, 可以开读了." if not problems else f"{problems} 项待处理.")
    return 0 if not problems else 1


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="paperkit", description="Zotero + Obsidian 论文流水线"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("setup", help="铺好 Obsidian 库结构和模板")
    s.add_argument("--vault", required=True, help="Obsidian 库根目录")
    s.add_argument("--force", action="store_true", help="覆盖已存在的模板")
    s.set_defaults(func=cmd_setup)

    d = sub.add_parser("discover", help="从种子论文出发找关联文献并分级")
    d.add_argument("--seeds", required=True, help="种子清单, 一行一个 DOI/arXiv号/标题")
    d.add_argument("--out", required=True, help="PDF 和 RIS 的输出目录")
    d.add_argument("--vault", help="Obsidian 库根目录, 给了就一并生成文献笔记")
    d.add_argument("--mailto", help="你的邮箱, 进 OpenAlex 礼貌池, 速度快 10 倍")
    d.add_argument("--max", type=int, default=60, help="最多保留多少篇 (默认 60)")
    d.add_argument("--top-s", type=int, default=12, help="S 级篇数 (默认 12)")
    d.add_argument("--top-a", type=int, default=20, help="A 级篇数 (默认 20)")
    d.add_argument("--citers-per-seed", type=int, default=None,
                   help="每篇种子最多回溯多少引用它的文献 (默认 150, 种子多时自动减少)")
    d.add_argument("--min-year", type=int, help="只要这一年之后的")
    d.add_argument("--no-pdf", action="store_true", help="只出元数据, 不下载 PDF")
    d.add_argument("--have-seeds", action="store_true",
                   help="种子论文我已经有了: 不下载它们的 PDF, 也不写进 RIS, 免得 Zotero 里重复")
    d.add_argument("--force", action="store_true", help="覆盖已存在的笔记")
    d.set_defaults(func=cmd_discover)

    i = sub.add_parser("install", help="一键安装: 自动找库, 铺结构, 装插件, 配 Zotero")
    i.add_argument("--vault", help="不自动找, 直接指定 Obsidian 库路径")
    i.add_argument("--skip-plugins", action="store_true", help="不装 Obsidian 插件")
    i.add_argument("--skip-zotero", action="store_true", help="不管 Zotero 那边")
    i.set_defaults(func=cmd_install)

    sd = sub.add_parser("seeds", help="从已经下载的 PDF 生成种子清单")
    sd.add_argument("--from-pdfs", required=True, help="PDF 所在文件夹 (含子文件夹)")
    sd.add_argument("--out", default="seeds.txt", help="写到哪个清单, 已存在就追加 (默认 seeds.txt)")
    sd.set_defaults(func=cmd_seeds)

    c = sub.add_parser("config", help="记住邮箱等设置")
    c.add_argument("--mailto", help="你的邮箱, 用于 OpenAlex 礼貌池")
    c.set_defaults(func=cmd_config)

    k = sub.add_parser("doctor", help="体检: 检查插件/模板/Zotero 连接是否到位")
    k.add_argument("--vault", required=True)
    k.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    # 中文 Windows 上输出被重定向时编码是 GBK, 装不下 ✓ 这类符号.
    # 宁可显示成问号也不能让整个流程崩在一行日志上.
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(errors="replace")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("\n已中断.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
