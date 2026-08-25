#!/usr/bin/env python3
"""paperkit - Zotero + Obsidian 论文流水线.

从几篇种子论文出发, 用 OpenAlex 找出强关联文献, 按相关度分级, 下载开放获取 PDF,
生成可直接导入 Zotero 的 RIS, 并铺好 Obsidian 笔记库结构与模板.

纯标准库, 零依赖. Python 3.9+.

用法概览:
    python3 paperkit.py setup   --vault ~/Obsidian/Research
    python3 paperkit.py discover --seeds seeds.txt --out ./papers --mailto you@example.com
    python3 paperkit.py doctor  --vault ~/Obsidian/Research
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
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
    return Paper.from_json(best)


def norm_title(t: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", (t or "").lower()))


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
ZI_TEMPLATE = """---
citekey: {{citekey}}
title: "{{title}}"
year: {{date | format("YYYY")}}
authors: [{% for a in creators %}"{{a.firstName}} {{a.lastName}}"{% if not loop.last %}, {% endif %}{% endfor %}]
doi: {{DOI}}
zotero: "{{pdfZoteroLink}}"
status: 在读
tags: [论文]
---

# {{title}}

[在 Zotero 中打开]({{pdfZoteroLink}}){% if DOI %} · [DOI](https://doi.org/{{DOI}}){% endif %}

## 🟡 关键结论
{% for annot in annotations | filterby("color", "eq", "#ffd400") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 🔴 存疑与反对
{% for annot in annotations | filterby("color", "eq", "#ff6666") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 🟢 可复用的方法
{% for annot in annotations | filterby("color", "eq", "#5fb236") %}
- {{annot.annotatedText}} `p.{{annot.page}}`{% if annot.comment %}
  - 💭 {{annot.comment}}{% endif %}
{% endfor %}

## 🔵 待深挖
{% for annot in annotations | filterby("color", "eq", "#2ea8e5") %}
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
# discover: 主流程
# --------------------------------------------------------------------------


def read_seeds(path: Path) -> list[str]:
    raw = path.read_text(encoding="utf-8").splitlines()
    return [ln for ln in (l.strip() for l in raw) if ln and not ln.startswith("#")]


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

    client = Client(mailto=args.mailto)
    if not args.mailto:
        log("! 没给 --mailto, 按 1 请求/秒 跑. 填个邮箱能进 OpenAlex 礼貌池, 快 10 倍.")

    lines = read_seeds(seeds_path)
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
    candidates = expand(client, seeds, args.citers_per_seed)
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
            if not p.pdf_url:
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
        group = [p for p in final if p.tier == tier]
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

    plugins = vault / ".obsidian" / "plugins"
    for pid, label in [
        ("obsidian-zotero-desktop-connector", "Zotero Integration"),
        ("dataview", "Dataview"),
    ]:
        check(
            (plugins / pid).is_dir(),
            f"插件已安装: {label}",
            f"插件没装: {label} — Obsidian → 设置 → 社区插件 里搜",
        )

    bibs = list(vault.rglob("*.bib"))
    check(
        bool(bibs),
        f"找到 Better BibTeX 导出: {bibs[0].name}" if bibs else "",
        "库里没有 .bib — Zotero 里设置 Better BibTeX 自动导出到库目录",
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
    d.add_argument("--citers-per-seed", type=int, default=150,
                   help="每篇种子最多回溯多少引用它的文献 (默认 150)")
    d.add_argument("--min-year", type=int, help="只要这一年之后的")
    d.add_argument("--no-pdf", action="store_true", help="只出元数据, 不下载 PDF")
    d.add_argument("--force", action="store_true", help="覆盖已存在的笔记")
    d.set_defaults(func=cmd_discover)

    k = sub.add_parser("doctor", help="体检: 检查插件/模板/自动导出是否到位")
    k.add_argument("--vault", required=True)
    k.set_defaults(func=cmd_doctor)

    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("\n已中断.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
