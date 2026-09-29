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
import csv
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
    # 结构方程、PLS 这类统计方法文献: 写方法论那章要引, 但不是主题论文,
    # 不能拿名气去挤主题论文的名额.
    ("M", "M-研究方法"),
]
TIER_DIRS = dict(TIERS)


def script_id() -> str:
    """版本指纹: 文件内容的 sha256 前 8 位. 不用手动维护版本号,
    对一下就知道用户跑的是不是最新下载的那份."""
    import hashlib

    try:
        return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()[:8]
    except OSError:
        return "unknown"


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
        "locations",
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
    # 所有开放获取副本的 PDF 地址, 最推荐的排第一. 出版社那份常被拦, 还有 PMC、机构仓库可试.
    pdf_urls: list[str] = field(default_factory=list)
    date: str | None = None

    # 打分过程中填充
    score: float = 0.0
    tier: str = "B"
    reasons: list[str] = field(default_factory=list)
    seed_links: set[str] = field(default_factory=set)
    prov: dict[str, int] = field(default_factory=dict)
    # 只算引用关系 (被种子引用 / 引用了种子). OpenAlex 的"近邻"不算,
    # 不然被引 0 次的会议论文也能吃到"同时关联多篇种子"的加分.
    cite_links: set[str] = field(default_factory=set)
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
        urls: list[str] = []
        for cand in [w.get("best_oa_location") or {}] + [
            l for l in (w.get("locations") or []) if isinstance(l, dict) and l.get("is_oa")
        ]:
            u = cand.get("pdf_url")
            if u and u not in urls:
                urls.append(u)
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
            pdf_urls=urls,
            date=w.get("publication_date"),
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
            if kind in ("back", "fwd"):
                p.cite_links |= seed_set
    return candidates


# --------------------------------------------------------------------------
# 打分与分级
# --------------------------------------------------------------------------

W_BACK = 3.0     # 被种子引用: 领域基石, 权重最高
W_FWD = 2.5      # 引用了种子: 直接的后续工作
W_REL = 1.0      # OpenAlex 近邻: 按主题相似度算的, 只当排序的补充
REL_MAX_SEEDS = 2
W_COUPLE = 2.0   # 文献耦合: 和种子共享参考文献 = 同一个问题域
W_BREADTH = 2.5  # 同时挂到多篇种子上, 这是最强的"这就是你要找的"信号
W_IMPACT = 0.8   # 影响力, 年均被引取对数, 别让老论文单靠年头碾压
IMPACT_CAP = 2.0  # 封顶: 被引两万次的方法论经典不能靠名气压过主题相关的论文


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
            s += W_REL * min(prov["rel"], REL_MAX_SEEDS)
            why.append("OpenAlex 判定为近邻")

        # 文献耦合: 和种子重叠的参考文献数, 用自身参考文献量开方归一,
        # 否则 300 条参考文献的综述会靠体量霸榜.
        shared = len(p.refs & seed_refs)
        if shared:
            couple = shared / math.sqrt(max(len(p.refs), 1))
            s += W_COUPLE * couple
            why.append(f"与种子共享 {shared} 条参考文献")

        breadth = len(p.cite_links)
        if breadth > 1:
            s += W_BREADTH * (breadth - 1)
            why.append(f"同时关联 {breadth} 篇种子")

        age = max(1, this_year - (p.year or this_year) + 1)
        per_year = p.cited_by / age
        s += min(W_IMPACT * math.log1p(per_year), IMPACT_CAP)

        # 近三年的新工作给一点补偿, 它们还没来得及攒引用.
        if p.year and this_year - p.year <= 3:
            s += 0.6

        # 综述/社论这类不是"论文"的条目往下压, 但不排除.
        if (p.type or "") not in ("article", "preprint", "book-chapter", "book"):
            s *= 0.75

        p.score = round(s, 3)
        p.reasons = why


METHOD_PATTERNS = [
    r"structural equation", r"\bpls\b", r"partial least squares",
    r"multivariate data analysis", r"\bfit ind(?:ex|exes|ices)\b", r"goodness[- ]of[- ]fit",
    r"measurement (?:model|error|invariance)", r"common method (?:bias|variance)",
    r"factor analysis", r"\bfactorial\b", r"cronbach", r"discriminant validity",
    r"unobserved heterogeneity", r"marketing research", r"\bresearch methods?\b",
    r"survey research", r"sample size", r"\bbootstrap", r"mediation analysis",
    r"regression analysis", r"psychometric", r"robustness checks?",
    r"结构方程", r"偏最小二乘", r"因子分析", r"信度", r"效度", r"研究方法",
]

# 种子标题里的泛用词: 不能因为共享 "data" "study" 就把方法文献当成主题论文.
GENERIC_WORDS = {
    "with", "from", "into", "toward", "towards", "study", "studie", "research", "based",
    "base", "exploring", "explore", "relationship", "between", "factor", "influencing",
    "influence", "impact", "effect", "role", "analysi", "analyse", "model", "modeling",
    "modelling", "theory", "theorie", "context", "driver", "focu", "keep", "coming", "come",
    "what", "which", "their", "using", "approach", "evidence", "case", "perspective", "data",
    "review", "development", "determinant", "examining", "assessing", "assessment",
    "empirical", "among", "within", "through", "toward", "more", "than", "this", "that",
}
CJK_GENERIC = {"研究", "影响", "关系", "分析", "基于", "模型", "方法", "理论", "因素", "作用", "视角", "对策"}


def _stem(w: str) -> str:
    return w[:-1] if len(w) > 4 and w.endswith("s") and not w.endswith("ss") else w


def _topic_words(title: str) -> set[str]:
    words = {_stem(w) for w in re.findall(r"[a-z]{4,}", (title or "").lower())} - GENERIC_WORDS
    for run in re.findall(r"[\u4e00-\u9fff]+", title or ""):
        for i in range(len(run) - 1):
            bg = run[i:i + 2]
            if not any(c in bg for c in "的与和及之") and bg not in CJK_GENERIC:
                words.add(bg)
    return words


def seed_vocabulary(titles: Iterable[str]) -> set[str]:
    """种子标题里的主题词. 方法文献的判断要看它和这些词有没有交集."""
    vocab: set[str] = set()
    for t in titles:
        vocab |= _topic_words(t)
    return vocab


def is_method_paper(title: str, vocab: set[str]) -> bool:
    """标题像统计方法文献, 并且和种子的主题词毫无交集.
    "主题公园与游客满意度的结构方程模型" 这种仍然是主题论文."""
    low = (title or "").lower()
    if not any(re.search(p, low) for p in METHOD_PATTERNS):
        return False
    return not (_topic_words(title) & vocab)


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


# MDPI、Frontiers 等出版社会拦截非浏览器请求, 返回网页而不是 PDF.
# 下的都是开放获取的文章, 用浏览器的请求头即可.
BROWSER_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
)


def download_pdf(url: str, dest: Path, timeout: int = 60) -> bool:
    """下载开放获取 PDF. 只接受真的是 PDF 的响应, 免得存下一堆登录页 HTML."""
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": BROWSER_UA, "Accept": "application/pdf,*/*;q=0.8"}
        )
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
    # 双语引用样式 (中文作者多于 3 个写"等", 英文写 et al.) 靠 Zotero 的语言字段区分,
    # 必须是 zh-CN / en-US 这种代码, 写 "中文" "English" 不认.
    lines.append("LA  - zh-CN" if re.search(r"[一-鿿]", p.title) else "LA  - en-US")
    lines.append(f"KW  - paperkit/{TIER_DIRS[p.tier]}")
    if p.reasons:
        lines.append(f"N1  - paperkit 关联理由: {'; '.join(p.reasons)} (score {p.score})")
    if p.pdf_path:
        lines.append(f"L1  - {p.pdf_path}")
    lines.append("ER  - ")
    return "\n".join(lines) + "\n\n"


# --------------------------------------------------------------------------
# 理论 / 变量 / 方法识别: 按标题和摘要里的关键词预填进笔记, 用户读的时候核对修改
# --------------------------------------------------------------------------

# 关键词写法: 英文按词开头匹配 (satisf 命中 satisfaction); 以 = 开头的按整词匹配
# (=sem 不会命中 semantic, =wom 不会命中 women); 中文按字面匹配.
THEORIES = [
    ("期望确认理论", ["expectation confirmation", "expectation-confirmation",
                "expectation disconfirmation", "expectancy disconfirmation", "期望确认", "期望不一致"]),
    ("S-O-R 模型", ["stimulus-organism-response", "stimulus organism response", "s-o-r",
                  "=sor model", "=sor framework", "=sor theory", "=sor paradigm", "刺激-机体-反应"]),
    ("计划行为理论", ["planned behavio", "计划行为"]),
    ("技术接受模型", ["technology acceptance", "技术接受"]),
    ("推拉理论", ["push and pull", "push-pull", "push–pull", "push or pull", "推拉"]),
    ("体验经济理论", ["experience economy", "体验经济"]),
    ("心流理论", ["flow theory", "flow experience", "心流"]),
    ("心理账户理论", ["mental accounting", "心理账户"]),
    ("SERVQUAL 模型", ["servqual", "差距模型"]),
    ("PAD 情绪模型", ["pleasure-arousal", "pleasure, arousal", "pleasure and arousal",
                   "pleasure arousal", "=pad"]),
    ("手段-目的链理论", ["means-end", "means end chain", "手段-目的", "手段目的"]),
    ("社会认同理论", ["social identity", "social identification", "社会认同"]),
    ("自我一致性理论", ["self-congruity", "self congruity", "自我一致"]),
    ("地方依恋理论", ["place attachment", "地方依恋"]),
    ("认知评价理论", ["cognitive appraisal", "appraisal theory", "认知评价"]),
    ("社会交换理论", ["social exchange", "社会交换"]),
    ("使用与满足理论", ["uses and gratification", "使用与满足"]),
]

VARIABLES = [
    ("满意度", ["satisf", "满意"]),
    ("忠诚度", ["loyal", "忠诚"]),
    ("重游意愿", ["revisit", "return intention", "intention to return", "repeat visit", "重游", "再游"]),
    ("推荐/口碑", ["word of mouth", "word-of-mouth", "=wom", "=ewom", "recommend", "口碑", "推荐"]),
    ("行为意向", ["behavioral intention", "behavioural intention", "行为意向"]),
    ("感知价值", ["perceived value", "感知价值"]),
    ("享乐/功利价值", ["hedonic value", "utilitarian", "享乐价值", "功利价值"]),
    ("服务质量", ["service quality", "服务质量"]),
    ("体验质量", ["experience quality", "experiential quality", "quality of experience", "体验质量"]),
    ("目的地/品牌形象", ["destination image", "park image", "brand image", "image", "目的地形象", "形象"]),
    ("难忘旅游体验", ["memorable", "难忘"]),
    ("情绪/愉悦", ["emotion", "pleasure", "arousal", "delight", "positive affect", "情绪", "愉悦", "惊喜"]),
    ("享乐主义", ["hedonism", "hedonic motivation", "享乐主义"]),
    ("旅游动机", ["motivation", "=motives", "动机"]),
    ("期望", ["expectation", "期望"]),
    ("信任", ["=trust", "信任"]),
    ("品牌资产", ["brand equity", "品牌资产"]),
    ("地方依恋", ["place attachment", "地方依恋"]),
    ("涉入度", ["involvement", "涉入"]),
    ("感知风险", ["perceived risk", "感知风险"]),
    ("服务场景", ["servicescape", "physical environment", "服务场景"]),
    ("拥挤感", ["crowding", "拥挤"]),
    ("真实性", ["authenticity", "真实性"]),
    ("新奇感", ["novelty", "新奇"]),
    ("怀旧", ["nostalgi", "怀旧"]),
    ("价格/公平", ["price fairness", "perceived price", "价格"]),
]

METHODS = [
    ("PLS-SEM", ["pls-sem", "partial least squares", "smartpls", "=pls"]),
    ("结构方程模型 (SEM)", ["structural equation", "=sem", "=amos", "=lisrel", "结构方程"]),
    ("问卷调查", ["questionnaire", "survey", "问卷"]),
    ("访谈/质性研究", ["interview", "qualitative", "grounded theory", "访谈", "质性", "扎根"]),
    ("文本挖掘/情感分析", ["text mining", "sentiment", "online review", "user-generated",
                    "文本挖掘", "情感分析", "网络评论"]),
    ("实验法", ["experiment", "实验"]),
    ("回归分析", ["regression", "回归"]),
    ("fsQCA", ["fsqca", "qualitative comparative analysis", "定性比较"]),
    ("层次分析法 (AHP)", ["analytic hierarchy", "=ahp", "层次分析"]),
    ("因子分析", ["factor analysis", "因子分析"]),
    ("大数据/机器学习", ["big data", "machine learning", "deep learning", "neural network",
                   "大数据", "机器学习"]),
]

MATRIX_FIELDS = ["理论", "变量", "方法", "样本", "主要结论"]

SAMPLE_PATTERNS = [
    r"\bn\s*=\s*([\d,]{2,7})",
    r"([\d,]{2,7})\s+(?:valid\s+|usable\s+|completed\s+|effective\s+)?"
    r"(?:respondents|questionnaires|responses|participants|visitors|tourists|guests|"
    r"customers|consumers|samples|climbers|students)",
    r"sample\s+(?:size\s+)?of\s+([\d,]{2,7})",
    r"([\d,]{2,7})\s*份",
]


def _kw_hit(kw: str, low: str) -> bool:
    if re.search(r"[一-鿿]", kw):
        return kw in low
    if kw.startswith("="):
        return re.search(r"\b" + re.escape(kw[1:]) + r"\b", low) is not None
    return re.search(r"\b" + re.escape(kw), low) is not None


def _hits(table: list[tuple[str, list[str]]], low: str) -> list[str]:
    return [name for name, kws in table if any(_kw_hit(k, low) for k in kws)]


def extract_sample(text: str) -> str:
    low = (text or "").lower()
    for pat in SAMPLE_PATTERNS:
        for m in re.finditer(pat, low):
            n = int(m.group(1).replace(",", "") or 0)
            # 年份和个位数不是样本量
            if 30 <= n <= 100000 and not 1950 <= n <= 2035:
                return str(n)
    return ""


def extract_concepts(title: str, abstract: str) -> dict:
    """按关键词猜这篇用了什么理论、研究了哪些变量、用的什么方法、样本多大.
    只是预填, 笔记里写明让用户读的时候核对."""
    low = f"{title or ''}\n{abstract or ''}".lower()
    methods = _hits(METHODS, low)
    if "PLS-SEM" in methods and "结构方程模型 (SEM)" in methods:
        methods.remove("结构方程模型 (SEM)")   # PLS-SEM 已经说明了
    return {
        "理论": _hits(THEORIES, low),
        "变量": _hits(VARIABLES, low),
        "方法": methods,
        "样本": extract_sample(abstract) or extract_sample(title),
    }


def matrix_lines(c: dict) -> list[str]:
    lines = [f"{k}: [{', '.join(c[k])}]" for k in ("理论", "变量", "方法")]
    lines.append(f'样本: "{c["样本"]}"' if c["样本"] else "样本:")
    lines.append("主要结论:")
    return lines


def _fm_list(head: str, key: str) -> list[str]:
    """读 frontmatter 里的列表字段. 兼容 [a, b]、"a，b、c" 和 YAML 块列表三种写法."""
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*)$", head)
    if not m:
        return []
    v = m.group(1).strip()
    if v.startswith("[") and v.endswith("]"):
        items = v[1:-1].split(",")
    elif v:
        items = re.split(r"[,，、;；]", v)
    else:
        items = []
        for line in head[m.end():].splitlines()[1:]:
            b = re.match(r"^\s+-\s*(.*)$", line)
            if not b:
                break
            items.append(b.group(1))
    return [x.strip().strip('"').strip("'").strip() for x in items if x.strip().strip('"\'')]


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
{matrix}
---

# {title}

> [!info] 为什么这篇会进来
> {reasons}
>
> 关联种子: {seed_links}
> 被引: {cited_by} · 年份: {year} · 分级: **{tier_name}**

> [!tip] 文献矩阵
> 顶部属性里的 理论 / 变量 / 方法 / 样本 是按摘要自动识别的, 读的时候核对修改;
> 主要结论读完自己填. 这几项会汇总进 [[文献矩阵]] 和 [[理论与变量]].

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


# 不在最新结果里的旧笔记挪到这里. 不在 TIERS 里, 下次重跑不会再扫它.
RETIRED_DIR = "_不再推荐"
RETIRED_TIER = "不再推荐"


def retire_note(path: Path) -> None:
    """改掉分级标记, 让阅读面板 (按 tier 过滤) 不再把它列成待读; 正文一字不动."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            head, rest = text[:end], text[end:]
            head = re.sub(r"(?m)^tier: .*$", f"tier: {RETIRED_TIER}", head)
            text = head + rest
    text = re.sub(r"分级: \*\*[^*]+\*\*", f"分级: **{RETIRED_TIER}**", text)
    path.write_text(text, encoding="utf-8")


def retier_note(path: Path, p: Paper) -> None:
    """只改分级相关的几行: frontmatter 的 tier/score/tags 和提示框里的"分级"."""
    text = path.read_text(encoding="utf-8")
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end > 0:
            head, rest = text[:end], text[end:]
            head = re.sub(r"(?m)^tier: .*$", f"tier: {p.tier}", head)
            head = re.sub(r"(?m)^score: .*$", f"score: {p.score}", head)
            head = re.sub(r"(?m)^tags: \[论文, .*\]$",
                          f"tags: [论文, {TIER_DIRS[p.tier].replace('-', '/')}]", head)
            text = head + rest
    text = re.sub(r"分级: \*\*[^*]+\*\*", f"分级: **{TIER_DIRS[p.tier]}**", text)
    path.write_text(text, encoding="utf-8")


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
        matrix="\n".join(matrix_lines(extract_concepts(p.title, p.abstract))),
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
        log(f"  认不出: {len(unknown)} 篇 (PDF 里没有能可靠认出的 DOI 或标题, "
            "打开首页找到 DOI 手动补进清单)")
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
# outline: 按主题分节的文献综述大纲
# --------------------------------------------------------------------------

THEMES_PATH = Path(__file__).resolve().parent / "themes.txt"
METHOD_THEME = "研究方法"
UNSORTED_THEME = "未归类"
OUTLINE_NOTE = "文献综述大纲"
DRAFT_NOTE = "文献综述草稿"

THEMES_HEADER = """# 文献综述的主题分节 —— 一行一节, 格式: 节名: 关键词, 关键词, ...
#
# 规则: 从上往下, 论文标题命中哪一节的关键词就归哪一节 (第一个命中的算数);
#       标题一个都没命中, 再用摘要按同样顺序匹配; 还没命中的放进"未归类".
# 所以: 具体的主题放上面, 宽泛的 (比如"满意度") 放下面, 不然会把别的节吞掉.
#
# 关键词不分大小写; 英文按词开头匹配 (brand 能匹配 branding, brands);
# 中文按字面匹配. 改完运行 paperkit.py outline --vault "你的库路径" 即可重排.
"""

# 自动起草时不拿来当主题的泛用词 (几乎每篇旅游/管理论文标题里都有)
AUTO_GENERIC = {
    "experience", "tourist", "tourism", "visitor", "perceived", "value", "case",
    "examination", "preliminary", "conceptualization", "version", "customer", "consumer",
    "element", "scale", "measure",
}


def load_themes(path: Path) -> list[tuple[str, list[str]]]:
    themes = []
    for line in read_user_text(path).splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        m = re.match(r"^(.+?)\s*[:：]\s*(.+)$", line)
        if not m:
            continue
        kws = [k.strip().lower() for k in re.split(r"[,，、]", m.group(2)) if k.strip()]
        if kws:
            themes.append((m.group(1).strip(), kws))
    return themes


def _theme_hit(kw: str, low: str) -> bool:
    # 按词开头匹配: brand 命中 branding, park 不命中 sparkling; =开头按整词
    return _kw_hit(kw, low)


def theme_of(title: str, abstract: str, themes: list[tuple[str, list[str]]]) -> str | None:
    """先看标题, 标题一个都不中再看摘要; 各自按分节的先后顺序, 第一个命中的算数."""
    for text in (title, abstract):
        low = (text or "").lower()
        if not low:
            continue
        for name, kws in themes:
            if any(_theme_hit(k, low) for k in kws):
                return name
    return None


def draft_themes(titles: Iterable[str], max_themes: int = 8) -> list[tuple[str, list[str]]]:
    """没有分节规则时, 从标题里的高频词组起草一份, 用户再改名、调整."""
    bigrams: Counter = Counter()
    unigrams: Counter = Counter()
    skip = GENERIC_WORDS | AUTO_GENERIC
    for t in titles:
        ws = [_stem(w) for w in re.findall(r"[a-z]{3,}", (t or "").lower())]
        bigrams.update({f"{a} {b}" for a, b in zip(ws, ws[1:])
                        if len(a) >= 4 and len(b) >= 4 and a not in GENERIC_WORDS
                        and b not in GENERIC_WORDS})
        unigrams.update({w for w in ws if len(w) >= 4 and w not in skip})
    phrases = [b for b, n in bigrams.most_common() if n >= 3][:max_themes]
    covered = {w for b in phrases for w in b.split()}
    words = [w for w, n in unigrams.most_common() if n >= 4 and w not in covered]
    # 词组比单词具体, 放上面; 单词里出现越多的越宽泛, 放越下面, 免得吞掉别的节
    words = sorted(words[: max(0, max_themes - len(phrases))], key=lambda w: unigrams[w])
    return [(x, [x]) for x in phrases + words]


def _note_meta(path: Path) -> dict | None:
    """读 paperkit 生成的文献笔记的标题、摘要、分级. 别的笔记 (没有 openalex 字段) 不管."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not text.startswith("---"):
        return None
    end = text.find("\n---", 3)
    if end < 0:
        return None
    head = text[3:end]
    if not re.search(r"(?m)^openalex:", head):
        return None

    def field(key: str) -> str:
        m = re.search(rf"(?m)^{key}:[ \t]*(.*)$", head)
        return m.group(1).strip() if m else ""

    m = re.search(r"\n## 摘要\s*\n(.*?)(?:\n## |\Z)", text, re.S)
    abstract = m.group(1).strip() if m else ""
    if abstract.startswith("*(OpenAlex 无摘要"):
        abstract = ""
    return {"title": field("title").strip('"'), "tier": field("tier"), "abstract": abstract,
            "year": field("year"), "venue": field("venue").strip('"'),
            "doi": field("doi"), "status": field("status"), "theme": field("theme").strip('"'),
            "head": head}


def _add_missing_frontmatter(path: Path, lines: list[str]) -> bool:
    """只补缺的键, 已有的键 (哪怕值是空的) 一律不动: 用户清空识别错的字段后,
    下次运行不能再填回去."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    add = [ln for ln in lines
           if not re.search(rf"(?m)^{re.escape(ln.split(':', 1)[0])}:", head)]
    if not add:
        return False
    path.write_text(head + "\n" + "\n".join(add) + rest, encoding="utf-8")
    return True


def _set_frontmatter(path: Path, key: str, value: str) -> None:
    """只改 frontmatter 里的一行; 没有就加一行. 正文一字不动."""
    text = path.read_text(encoding="utf-8")
    end = text.find("\n---", 3)
    head, rest = text[:end], text[end:]
    line = f'{key}: "{value}"'
    if re.search(rf"(?m)^{key}:.*$", head):
        head = re.sub(rf"(?m)^{key}:.*$", lambda _: line, head)
    else:
        head = head + "\n" + line
    new = head + rest
    if new != text:
        path.write_text(new, encoding="utf-8")


def _cn_number(n: int) -> str:
    digits = "零一二三四五六七八九"
    if n < 10:
        return digits[n]
    if n < 20:
        return "十" + (digits[n - 10] if n > 10 else "")
    return digits[n // 10] + "十" + (digits[n % 10] if n % 10 else "")


def build_outline(vault: Path, themes_path: Path) -> int:
    notes_root = vault / "10-文献笔记"
    metas: list[tuple[Path, dict]] = []
    for _, name in TIERS:
        d = notes_root / name
        if d.is_dir():
            for f in sorted(d.glob("*.md")):
                m = _note_meta(f)
                if m:
                    metas.append((f, m))
    if not metas:
        log(f"× {notes_root} 里没有 paperkit 生成的文献笔记, 先跑 discover")
        return 1

    if themes_path.exists():
        themes = load_themes(themes_path)
    else:
        themes = draft_themes(m["title"] for _, m in metas if m["tier"] != "M")
        themes_path.write_text(
            THEMES_HEADER + "\n# 下面是按标题里的高频词自动起草的, 节名可以改成中文, 关键词可以增删.\n\n"
            + "".join(f"{n}: {', '.join(k)}\n" for n, k in themes),
            encoding="utf-8",
        )
        log(f"  · 还没有分节规则, 按标题高频词起草了一份: {themes_path}")
    if not themes:
        log(f"× {themes_path} 里没有有效的分节 (格式: 节名: 关键词, 关键词)")
        return 1

    counts: Counter = Counter()
    for f, m in metas:
        if m["tier"] == "M":
            th = METHOD_THEME
        else:
            th = theme_of(m["title"], m["abstract"], themes) or UNSORTED_THEME
        _set_frontmatter(f, "theme", th)
        if m["tier"] != "M":
            _add_missing_frontmatter(f, matrix_lines(extract_concepts(m["title"], m["abstract"])))
        counts[th] += 1

    sections = [n for n, _ in themes]
    sections += [x for x in (METHOD_THEME, UNSORTED_THEME) if counts[x]]
    rel_notes = notes_root.relative_to(vault).as_posix()
    lines = [
        f"# {OUTLINE_NOTE}", "",
        "> [!note] 自动生成",
        f"> 每次运行 discover 或 outline 都会重新生成这份大纲, 别在这里写东西; 写综述用 [[{DRAFT_NOTE}]].",
        f"> 分节规则在 `{themes_path}`, 改完运行 `{PY} paperkit.py outline --vault \"{vault}\"` 重排.",
        "",
        f"共 {len(metas)} 篇 · {len(themes)} 个主题"
        + (f" · 研究方法 {counts[METHOD_THEME]} 篇" if counts[METHOD_THEME] else "")
        + (f" · 未归类 {counts[UNSORTED_THEME]} 篇" if counts[UNSORTED_THEME] else ""),
    ]
    for name in sections:
        hint = ""
        if name == UNSORTED_THEME:
            hint = " — 给 themes.txt 补关键词, 就能把它们归进某一节"
        elif not counts[name]:
            hint = " — 还没有论文命中这一节的关键词"
        lines += [
            "", f"## {name}", "", f"*{counts[name]} 篇*{hint}", "",
            "```dataview",
            "TABLE WITHOUT ID file.link AS 文献, year AS 年份, tier AS 分级, status AS 状态",
            f'FROM "{rel_notes}"',
            f'WHERE theme = "{name}" AND tier != "{RETIRED_TIER}"',
            "SORT score DESC",
            "```",
        ]
    map_dir = vault / "30-论文地图"
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / f"{OUTLINE_NOTE}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")

    draft = map_dir / f"{DRAFT_NOTE}.md"
    if not draft.exists():
        body = [f"# {DRAFT_NOTE}", "",
                "> 按主题分节的写作底稿. 这个文件只在第一次生成, 之后不会被覆盖, 放心写.",
                f"> 每节有哪些文献、读到哪了, 见 [[{OUTLINE_NOTE}]].", ""]
        for i, name in enumerate([n for n, _ in themes], 1):
            body += [f"## {_cn_number(i)}、{name}", "",
                     f"相关文献: [[{OUTLINE_NOTE}#{name}]]", "",
                     "<!-- 这一主题的主要观点是什么? 研究之间有哪些共识和分歧? 和你的研究有什么关系? -->",
                     ""]
        draft.write_text("\n".join(body), encoding="utf-8")
        log(f"  ✓ 建好综述草稿: {draft}")

    summary = ", ".join(f"{n} {counts[n]}" for n in sections if counts[n])
    log(f"  ✓ 文献综述大纲: {summary}")

    # 字段刚补过, 重新读一遍; 统计以笔记里的字段为准, 用户改正了哪篇, 统计就跟着准
    studies = [(f, m) for f, m in ((f, _note_meta(f)) for f, _ in metas)
               if m and m["tier"] != "M"]
    write_matrix(vault, map_dir, studies)
    write_stats(map_dir, studies)
    write_guide(vault, themes_path)
    return 0


# --------------------------------------------------------------------------
# 文献矩阵 / 理论与变量统计 / 使用说明
# --------------------------------------------------------------------------

MATRIX_NOTE = "文献矩阵"
STATS_NOTE = "理论与变量"
NEWS_NOTE = "新文献"
GUIDE_NOTE = "使用说明"
MATRIX_COLUMNS = ["文献", "标题", "年份", "期刊", "DOI", "分级", "主题",
                  "理论", "变量", "方法", "样本", "主要结论", "状态"]


def _alias(path: Path) -> str:
    return path.stem.split(" - ")[0]


def _cell_link(path: Path) -> str:
    """表格里的链接: 竖线要转义, 不然会被当成表格分隔符."""
    return f"[[{path.stem}\\|{_alias(path)}]]"


def _fm_text(head: str, key: str) -> str:
    m = re.search(rf"(?m)^{re.escape(key)}:[ \t]*(.*)$", head)
    return m.group(1).strip().strip('"') if m else ""


def _write_csv(path: Path, rows: list[dict]) -> None:
    # utf-8-sig: Excel 要 BOM 才会按 UTF-8 打开, 不然中文全是乱码
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        w = csv.DictWriter(fh, fieldnames=MATRIX_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def write_matrix(vault: Path, map_dir: Path, studies: list[tuple[Path, dict]]) -> None:
    rel_notes = (vault / "10-文献笔记").relative_to(vault).as_posix()
    csv_path = map_dir / f"{MATRIX_NOTE}.csv"
    note = [
        f"# {MATRIX_NOTE}", "",
        "> [!note] 自动生成",
        "> 理论 / 变量 / 方法 / 样本 先按摘要自动识别, 读的时候在每篇笔记顶部的属性里核对修改;",
        "> 主要结论读完自己填. 表格实时更新.",
        f"> 同一张表的 Excel 版: `{csv_path}` (每次运行 discover 或 outline 时更新)",
        "",
        "```dataview",
        "TABLE WITHOUT ID file.link AS 文献, year AS 年份, theme AS 主题, 理论, 变量, 方法, 样本, "
        "主要结论, status AS 状态",
        f'FROM "{rel_notes}"',
        f'WHERE tier != "{RETIRED_TIER}" AND tier != "M"',
        "SORT theme ASC, score DESC",
        "```",
    ]
    (map_dir / f"{MATRIX_NOTE}.md").write_text("\n".join(note) + "\n", encoding="utf-8")

    rows = []
    for f, m in studies:
        h = m["head"]
        rows.append({
            "文献": _alias(f), "标题": m["title"], "年份": m["year"], "期刊": m["venue"],
            "DOI": m["doi"], "分级": m["tier"], "主题": m["theme"],
            "理论": "、".join(_fm_list(h, "理论")), "变量": "、".join(_fm_list(h, "变量")),
            "方法": "、".join(_fm_list(h, "方法")), "样本": _fm_text(h, "样本"),
            "主要结论": _fm_text(h, "主要结论"), "状态": m["status"],
        })
    try:
        _write_csv(csv_path, rows)
        log(f"  ✓ 文献矩阵: {len(rows)} 篇 (Excel 版 {csv_path.name})")
    except PermissionError:
        log(f"  · {csv_path.name} 可能正在 Excel 里打开, 这次没更新; 关掉 Excel 再运行 outline")


def _var_order(name: str) -> tuple:
    names = [n for n, _ in VARIABLES]
    return (names.index(name) if name in names else len(names), name)


def write_stats(map_dir: Path, studies: list[tuple[Path, dict]]) -> None:
    def table(field: str, title: str) -> list[str]:
        counter: Counter = Counter()
        papers: dict[str, list[Path]] = {}
        for f, m in studies:
            for x in dict.fromkeys(_fm_list(m["head"], field)):
                counter[x] += 1
                papers.setdefault(x, []).append(f)
        out = ["", f"## {title}", ""]
        if not counter:
            return out + ["还没有识别到. 读的时候在笔记属性里填上, 再运行 outline."]
        out += [f"| {field} | 篇数 | 文献 |", "|---|---|---|"]
        for name, n in sorted(counter.items(), key=lambda kv: (-kv[1], kv[0])):
            links = ", ".join(_cell_link(p) for p in papers[name][:8])
            more = f" 等 {n} 篇" if n > 8 else ""
            out.append(f"| {name} | {n} | {links}{more} |")
        return out

    with_abstract = sum(1 for _, m in studies if m["abstract"])
    lines = [
        f"# {STATS_NOTE}", "",
        "> [!note] 自动生成 · 每次运行 discover 或 outline 更新",
        "> 统计来自每篇文献笔记里的 理论 / 变量 / 方法 字段. 这些字段先按摘要自动识别,",
        "> 你读的时候在笔记里改正, 再运行 outline, 这里就跟着准.",
        f"> 范围: {len(studies)} 篇文献 (不含研究方法类), 其中 {with_abstract} 篇有摘要可供识别.",
    ]
    lines += table("理论", "用得最多的理论")
    lines += table("变量", "研究得最多的变量")
    lines += table("方法", "研究方法分布")

    var_count: Counter = Counter()
    pairs: Counter = Counter()
    pair_papers: dict[tuple, list[Path]] = {}
    for f, m in studies:
        vs = sorted(dict.fromkeys(_fm_list(m["head"], "变量")), key=_var_order)
        var_count.update(vs)
        for i in range(len(vs)):
            for j in range(i + 1, len(vs)):
                key = (vs[i], vs[j])
                pairs[key] += 1
                pair_papers.setdefault(key, []).append(f)

    lines += ["", "## 常一起研究的变量组合", ""]
    common = [(k, n) for k, n in pairs.most_common() if n >= 2][:15]
    if common:
        lines += ["| 组合 | 篇数 | 文献 |", "|---|---|---|"]
        for (a, b), n in common:
            links = ", ".join(_cell_link(p) for p in pair_papers[(a, b)][:6])
            lines.append(f"| {a} × {b} | {n} | {links} |")
    else:
        lines.append("还没有两篇以上共同研究的变量组合.")

    lines += ["", "## 很少一起研究的变量组合", "",
              "> 只在这批文献范围内统计, 不代表整个领域没人做过. 拿来当找研究空白的线索,",
              "> 真要用, 得去知网 / Web of Science 用这两个变量一起检索核实.", ""]
    top = [v for v, n in var_count.most_common(8) if n >= 2]
    rare = []
    for i in range(len(top)):
        for j in range(i + 1, len(top)):
            a, b = sorted((top[i], top[j]), key=_var_order)
            if pairs[(a, b)] <= 1:
                rare.append((pairs[(a, b)], -(var_count[a] + var_count[b]), a, b))
    if rare:
        lines += ["| 组合 | 一起出现的篇数 | 各自出现的篇数 |", "|---|---|---|"]
        for n, _, a, b in sorted(rare)[:12]:
            lines.append(f"| {a} × {b} | {n} | {var_count[a]} / {var_count[b]} |")
    else:
        lines.append("文献还太少, 或者常见变量之间都有研究, 暂时看不出.")
    (map_dir / f"{STATS_NOTE}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    log(f"  ✓ 理论与变量: {sum(1 for _, m in studies if _fm_list(m['head'], '理论'))} 篇识别到理论, "
        f"{len(var_count)} 个变量")


SEEN_FILE = "paperkit-seen.json"


def track_literature(out: Path, final: list[Paper], candidates: dict[str, Paper],
                     this_year: int) -> dict:
    """记住见过哪些论文, 只把新出现的列出来. 第一次只建基准, 不把全部 80 篇都当"新"的刷屏."""
    today = time.strftime("%Y-%m-%d")
    seen_path = out / SEEN_FILE
    state: dict = {"seen": {}, "runs": []}
    if seen_path.exists():
        try:
            state = json.loads(seen_path.read_text(encoding="utf-8"))
        except ValueError:
            pass
    else:
        prev = out / "paperkit-result.json"
        if prev.exists():
            try:
                state["seen"] = {r["oid"]: "更早" for r in json.loads(prev.read_text(encoding="utf-8"))
                                 if isinstance(r, dict) and r.get("oid")}
            except ValueError:
                pass
    seen: dict = state.setdefault("seen", {})
    runs: list = state.setdefault("runs", [])

    current = [p for p in final if not p.is_seed]
    baseline = not seen
    if baseline:
        new: list[Paper] = []
        runs.append({"date": today, "baseline": len(current)})
    else:
        new = [p for p in current if p.oid not in seen]
        runs.append({"date": today, "new": len(new)})
    for p in current:
        seen.setdefault(p.oid, today)
    seen_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

    since = this_year - 2
    recent = sorted(
        (c for c in candidates.values() if c.prov.get("fwd") and (c.year or 0) >= since),
        key=lambda c: c.date or str(c.year or ""), reverse=True,
    )[:15]
    if baseline:
        log(f"  ✓ 新文献追踪: 第一次运行, 已把当前 {len(current)} 篇记为基准")
    else:
        log(f"  ✓ 新文献追踪: 本次新增 {len(new)} 篇")
    return {"new": new, "recent": recent, "baseline": baseline, "count": len(current),
            "runs": runs, "final_ids": {p.oid for p in final}, "today": today}


def write_news(map_dir: Path, news: dict, this_year: int) -> None:
    lines = [
        f"# {NEWS_NOTE}", "",
        "> [!note] 每次运行 discover 更新. 写论文期间建议每月跑一次, 不漏最新研究.",
        "",
        f"## 本次新进入推荐的 ({news['today']})", "",
    ]
    if news["baseline"]:
        lines.append(f"第一次追踪: 已把当前 {news['count']} 篇记为基准, 下次运行起这里会列出新出现的论文.")
    elif news["new"]:
        for p in news["new"]:
            lines.append(f"- [[{p.slug()}|{p.slug().split(' - ')[0]}]] · {TIER_DIRS[p.tier]} · "
                         f"{p.year or ''} · {p.title}")
    else:
        lines.append("这次没有新论文进入推荐.")

    lines += ["", f"## 最近引用了你种子论文的 ({this_year - 2} 年以来)", ""]
    if news["recent"]:
        for c in news["recent"]:
            link = f"[{c.title}](https://doi.org/{c.doi})" if c.doi else c.title
            where = "已在推荐里" if c.oid in news["final_ids"] else "没进推荐"
            lines.append(f"- {c.date or c.year} · {link} · 引用了 {c.prov['fwd']} 篇种子 · {where}")
    else:
        lines.append("暂时没有.")

    lines += ["", "## 追踪记录", ""]
    for r in reversed(news["runs"][-24:]):
        if "baseline" in r:
            lines.append(f"- {r['date']}: 建立基准 ({r['baseline']} 篇)")
        else:
            lines.append(f"- {r['date']}: 新增 {r['new']} 篇")
    map_dir.mkdir(parents=True, exist_ok=True)
    (map_dir / f"{NEWS_NOTE}.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_guide(vault: Path, themes_path: Path) -> None:
    """放进仓库的使用说明, 每次重新生成. 写在 Obsidian 里, 用的时候随手能找到."""
    v = f'"{vault}"'
    text = f"""# {GUIDE_NOTE}

> [!note] 自动生成 · 每次运行都会更新这份说明, 别在这里写东西

## 这些笔记是干什么的

| 笔记 | 用途 |
|---|---|
| [[阅读面板]] | 还没读的核心论文、在读的、读完没总结的 |
| [[主题地图]] | 按 S / A / B / M 分级列出全部推荐 |
| [[{OUTLINE_NOTE}]] | 按主题分节, 每节一张表, 看每节读到哪了 |
| [[{DRAFT_NOTE}]] | 按同样的分节写综述, 只生成一次, 放心写 |
| [[{MATRIX_NOTE}]] | 每篇的理论、变量、方法、样本、结论, 一张大表; 另有 Excel 版 |
| [[{STATS_NOTE}]] | 哪些理论、变量用得最多, 哪些常一起研究, 哪些很少一起研究 |
| [[{NEWS_NOTE}]] | 每次运行新出现的论文, 以及最近引用了你种子论文的研究 |

分级: S 核心必读 · A 强相关 · B 背景扩展 · M 研究方法 (写方法论那章时引).

## 读一篇论文

1. 在 Zotero 里打开 PDF, 按颜色划线: 黄 = 关键结论, 红 = 存疑, 绿 = 可借鉴的方法, 蓝 = 待深挖
2. 读完回 Obsidian, `Ctrl+P` → `Zotero Integration: {ZI_FORMAT_NAME}`, 选中这篇, 划线按颜色归位
3. 打开这篇的文献笔记, 核对顶部属性里自动识别的 理论 / 变量 / 方法 / 样本, 写上 主要结论
4. 把 `status` 从 未读 改成 已读

## 写论文时插引用

1. **Word 插件**: Word 顶部有 `Zotero` 选项卡就说明装好了. 没有的话: Zotero → 编辑 → 设置 → 引用
   → 文字处理软件 → 安装 Microsoft Word 加载项
2. **引用样式**: Zotero → 编辑 → 设置 → 引用 → 样式 → 获取更多样式, 搜 `GB/T 7714`, 装
   `China National Standard GB/T 7714-2015 (numeric, 中文)` (顺序编码制, 正文里是 [1] [2])
   或 `China National Standard GB/T 7714-2015 (author-date, 中文)` (著者-出版年制).
   学校有自己的格式要求的话以学校模板为准; Zotero 中文社区 (zotero-chinese.com/styles)
   有不少学校的学位论文样式
3. **在 Word 里**: `Zotero` 选项卡 → Add/Edit Citation 插入引用; 写完点 Add/Edit Bibliography
   自动生成参考文献表. 调整段落顺序后序号会自动重排
4. **中英文混排**: 要让中文文献写"等"、英文写"et al.", 每条文献的"语言"字段得是 `zh-CN` 或
   `en-US` (不能写"中文""English"). paperkit 导出的 .ris 已经按标题自动填好了;
   你自己拖进 Zotero 的 PDF 要在右侧信息栏里手动填一下

## 常用命令

在 PowerShell 里先 `cd $HOME\\paperkit`, 然后:

| 想做什么 | 命令 |
|---|---|
| 重新找关联论文 (建议每月一次) | `{PY} paperkit.py discover --seeds seeds.txt --out papers --vault {v} --have-seeds --prune` |
| 改了分节规则或笔记属性后, 刷新大纲、矩阵、统计 (不联网) | `{PY} paperkit.py outline --vault {v}` |
| 从一个文件夹的 PDF 生成种子清单 | `{PY} paperkit.py seeds --from-pdfs "PDF 所在文件夹"` |
| 检查环境 | `{PY} paperkit.py doctor --vault {v}` |

分节规则: `{themes_path}`
"""
    guide_dir = vault / "00-面板"
    guide_dir.mkdir(parents=True, exist_ok=True)
    (guide_dir / f"{GUIDE_NOTE}.md").write_text(text, encoding="utf-8")


def cmd_outline(args: argparse.Namespace) -> int:
    vault = Path(args.vault).expanduser()
    if not vault.is_dir():
        log(f"× 库目录不存在: {vault}")
        return 1
    return build_outline(vault, Path(args.themes).expanduser() if args.themes else THEMES_PATH)


# --------------------------------------------------------------------------
# discover: 主流程
# --------------------------------------------------------------------------


def read_user_text(path: Path) -> str:
    """读用户手改的文本文件. utf-8-sig 去掉老版记事本加的 BOM;
    再老的记事本存成 GBK, 用 UTF-8 解不开时退回 GBK, 不能一读就崩."""
    data = path.read_bytes()
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        return data.decode("gbk", errors="replace")


def read_seeds(path: Path) -> list[str]:
    raw = read_user_text(path).splitlines()
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
    vocab = seed_vocabulary(s.title for s in seeds)
    methods = [p for p in ranked if is_method_paper(p.title, vocab)]
    method_ids = {p.oid for p in methods}
    topical = [p for p in ranked if p.oid not in method_ids][: args.max]
    assign_tiers(topical, args.top_s, args.top_a)
    methods = methods[: args.top_m]
    for p in methods:
        p.tier = "M"
    if methods:
        log(f"  其中 {len(methods)} 篇是统计/研究方法文献, 单独放进 {TIER_DIRS['M']}, 不占主题论文的名额")

    # 种子本身永远是 S 级, 排在最前面.
    final = seeds + topical + methods

    out.mkdir(parents=True, exist_ok=True)
    for _, name in TIERS:
        (out / name).mkdir(exist_ok=True)

    if not args.no_pdf:
        log("→ 下载开放获取 PDF …")
        ok = failed = closed = skipped = 0
        for p in final:
            if p.is_seed and args.have_seeds:
                skipped += 1
                continue
            dest = out / TIER_DIRS[p.tier] / f"{p.slug()}.pdf"
            # 上次下在别的分级文件夹里了 (分级变了): 挪过来, 不重新下载.
            if not dest.exists():
                for _, name in TIERS:
                    old = out / name / dest.name
                    if old != dest and old.exists():
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        old.replace(dest)
                        break
            if not dest.exists() and not p.pdf_urls:
                closed += 1
                continue
            if dest.exists():
                p.pdf_path = str(dest.resolve())
                ok += 1
                continue
            # 挨个试所有开放副本: 出版社那份被拦了, PMC 或机构仓库的常常能下.
            for url in p.pdf_urls:
                if download_pdf(url, dest):
                    p.pdf_path = str(dest.resolve())
                    ok += 1
                    log(f"  ↓ [{p.tier}] {p.slug()[:64]}")
                    break
            else:
                failed += 1
        parts = [f"拿到 {ok} 篇 PDF"]
        if failed:
            parts.append(f"{failed} 篇有开放链接但下载失败 (出版社拦截或链接失效)")
        if closed:
            parts.append(f"{closed} 篇没有开放获取版本")
        if skipped:
            parts.append(f"跳过 {skipped} 篇种子 (--have-seeds)")
        log("  " + ", ".join(parts))
        if failed or closed:
            log("  导入 Zotero 后全选这些条目 → 右键 → 查找可用的 PDF, "
                "Zotero 会用自己的渠道 (含学校订阅) 再抓一遍")

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
        elsewhere: dict[str, list[Path]] = {}
        for _, name in TIERS:
            d = notes_root / name
            if d.is_dir():
                for f in d.glob("*.md"):
                    elsewhere.setdefault(f.name, []).append(f)
        n = kept = moved = 0
        wanted: set[str] = set()
        for p in final:
            folder = notes_root / TIER_DIRS[p.tier]
            folder.mkdir(parents=True, exist_ok=True)
            target = folder / f"{p.slug()}.md"
            wanted.add(target.name)
            if target.exists() and not args.force:
                kept += 1
                continue
            old = next((f for f in elsewhere.get(target.name, [])
                        if f != target and f.exists()), None)
            if old and not args.force:
                # 分级变了: 挪到新文件夹, 只改 frontmatter 里的分级, 你写的内容一字不动.
                old.replace(target)
                retier_note(target, p)
                moved += 1
                continue
            target.write_text(render_note(p, seed_titles), encoding="utf-8")
            n += 1
        orphans = [x for x in elsewhere if x not in wanted]
        pruned = 0
        if orphans and args.prune:
            retired = notes_root / RETIRED_DIR
            retired.mkdir(parents=True, exist_ok=True)
            for name in orphans:
                for f in elsewhere[name]:
                    if not f.exists():
                        continue
                    dest = retired / f.name
                    k = 2
                    while dest.exists():
                        dest = retired / f"{f.stem} ({k}){f.suffix}"
                        k += 1
                    f.replace(dest)
                    retire_note(dest)
                    pruned += 1
        parts = []
        if n:
            parts.append(f"新写入 {n} 篇")
        if moved:
            parts.append(f"{moved} 篇换了分级, 已挪到新文件夹 (你写的内容都在)")
        if kept:
            parts.append(f"{kept} 篇已存在没动")
        log(f"  ✓ Obsidian 文献笔记: {', '.join(parts) or '无'} → {notes_root}")
        if pruned:
            log(f"  · {pruned} 篇旧笔记不在这次的结果里, 已挪到 {RETIRED_DIR} (内容都在, 阅读面板不再列出)")
        elif orphans:
            log(f"  · {len(orphans)} 篇旧笔记不在这次的结果里, 留着没删; "
                f"加 --prune 可把它们挪到 {RETIRED_DIR}")

        # 没跑过 setup 的库没有这个目录; 前面的活都干完了, 不能在最后一步崩掉.
        (vault / "30-论文地图").mkdir(parents=True, exist_ok=True)
        (vault / "30-论文地图" / "主题地图.md").write_text(
            render_map(seeds, final), encoding="utf-8"
        )
        log("  ✓ 主题地图.md")
        build_outline(vault, Path(args.themes).expanduser() if args.themes else THEMES_PATH)

    # 必须在覆盖 paperkit-result.json 之前: 没有追踪记录时, 上一次的结果就是基准
    news = track_literature(out, final, candidates, this_year)
    if vault:
        write_news(vault / "30-论文地图", news, this_year)

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
    ap.add_argument("--version", action="version", version=f"paperkit {script_id()}")
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
    d.add_argument("--top-m", type=int, default=15, help="M 级 (研究方法文献) 最多几篇 (默认 15)")
    d.add_argument("--citers-per-seed", type=int, default=None,
                   help="每篇种子最多回溯多少引用它的文献 (默认 150, 种子多时自动减少)")
    d.add_argument("--min-year", type=int, help="只要这一年之后的")
    d.add_argument("--no-pdf", action="store_true", help="只出元数据, 不下载 PDF")
    d.add_argument("--have-seeds", action="store_true",
                   help="种子论文我已经有了: 不下载它们的 PDF, 也不写进 RIS, 免得 Zotero 里重复")
    d.add_argument("--force", action="store_true", help="覆盖已存在的笔记")
    d.add_argument("--themes", help="分节规则文件 (默认 paperkit.py 旁边的 themes.txt)")
    d.add_argument("--prune", action="store_true",
                   help=f"不在这次结果里的旧笔记挪到 {RETIRED_DIR} (内容保留, 阅读面板不再列出)")
    d.set_defaults(func=cmd_discover)

    i = sub.add_parser("install", help="一键安装: 自动找库, 铺结构, 装插件, 配 Zotero")
    i.add_argument("--vault", help="不自动找, 直接指定 Obsidian 库路径")
    i.add_argument("--skip-plugins", action="store_true", help="不装 Obsidian 插件")
    i.add_argument("--skip-zotero", action="store_true", help="不管 Zotero 那边")
    i.set_defaults(func=cmd_install)

    o = sub.add_parser("outline", help="按主题分节生成文献综述大纲 (不联网)")
    o.add_argument("--vault", required=True, help="Obsidian 库根目录")
    o.add_argument("--themes", help="分节规则文件 (默认 paperkit.py 旁边的 themes.txt)")
    o.set_defaults(func=cmd_outline)

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
    log(f"paperkit {script_id()}")
    try:
        return args.func(args)
    except KeyboardInterrupt:
        log("\n已中断.")
        return 130


if __name__ == "__main__":
    sys.exit(main())
