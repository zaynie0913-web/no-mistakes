"""paperkit 离线测试.

容器里连不上 OpenAlex, 所以用一个按官方文档字段结构伪造的假 API 把整条
discover 流水线跑通: 打分/分级/RIS/笔记/地图 全部走真代码路径.
"""

import json
import unittest.mock
import re
import sys
import tempfile
import unittest
import urllib.parse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import paperkit as pk


# --------------------------------------------------------------------------
# 假 OpenAlex
# --------------------------------------------------------------------------

def work(wid, title, year, cited, refs=(), related=(), pdf=None, typ="article",
         authors=("Ada Lovelace", "Alan Turing"), doi=None):
    return {
        "id": f"https://openalex.org/{wid}",
        "doi": f"https://doi.org/{doi}" if doi else None,
        "title": title,
        "display_name": title,
        "publication_year": year,
        "type": typ,
        "cited_by_count": cited,
        "referenced_works": [f"https://openalex.org/{r}" for r in refs],
        "related_works": [f"https://openalex.org/{r}" for r in related],
        "primary_location": {"source": {"display_name": "Journal of Fake"}},
        "best_oa_location": {"is_oa": bool(pdf), "pdf_url": pdf,
                             "source": {"display_name": "Journal of Fake"}},
        "authorships": [{"author": {"display_name": a}} for a in authors],
        "abstract_inverted_index": {"A": [0], "fake": [1], "abstract": [2]},
    }


# 两篇种子, 共享一篇上游基石 W100, 且都被 W200 引用 -> W100/W200 应当进 S 级
CORPUS = {
    "W1": work("W1", "Seed One on Transformers", 2020, 900,
               refs=["W100", "W101"], related=["W300"], doi="10.1000/seed1"),
    "W2": work("W2", "Seed Two on Attention", 2021, 700,
               refs=["W100", "W102"], related=["W300"], doi="10.1000/seed2"),
    "W100": work("W100", "Foundational Work Everyone Cites", 2015, 5000,
                 refs=["W900"], pdf="https://example.org/w100.pdf"),
    "W101": work("W101", "Narrow Upstream Paper", 2016, 40, refs=["W900"]),
    "W102": work("W102", "Another Upstream Paper", 2017, 60, refs=["W900"]),
    "W200": work("W200", "Follow Up Citing Both Seeds", 2024, 120,
                 refs=["W1", "W2", "W100", "W900"], pdf="https://example.org/w200.pdf"),
    "W201": work("W201", "Follow Up Citing One Seed", 2023, 30, refs=["W1", "W900"]),
    "W300": work("W300", "OpenAlex Neighbour", 2022, 15, refs=["W900"]),
    "W400": work("W400", "An Editorial Aside", 2024, 5, refs=["W1"], typ="editorial"),
    "W900": work("W900", "Very Old Common Reference", 2000, 20000),
}
CITERS = {"W1": ["W200", "W201", "W400"], "W2": ["W200"]}


class FakeClient(pk.Client):
    def __init__(self):
        super().__init__(mailto="test@example.com")
        self.calls = 0

    def get(self, path, **params):
        self.calls += 1
        if re.fullmatch(r"/works/W\d+", path):
            return CORPUS[path.rsplit("/", 1)[-1]]
        if path.startswith("/works/doi:"):
            doi = path.split("doi:", 1)[1]
            for w in CORPUS.values():
                if (w.get("doi") or "").endswith(doi):
                    return w
            raise pk.urllib.error.HTTPError(path, 404, "nf", None, None)
        if path == "/works":
            f = params.get("filter", "")
            if f.startswith("openalex_id:"):
                ids = f.split(":", 1)[1].split("|")
                return {"results": [CORPUS[i] for i in ids if i in CORPUS],
                        "meta": {"next_cursor": None}}
            if f.startswith("cites:"):
                seed = f.split(":", 1)[1]
                return {"results": [CORPUS[i] for i in CITERS.get(seed, [])],
                        "meta": {"next_cursor": None}}
            if "search" in params:
                q = pk.norm_title(params["search"])
                best = max(CORPUS.values(),
                           key=lambda w: pk.title_overlap(q, pk.norm_title(w["title"])))
                return {"results": [best]}
        raise AssertionError(f"假 API 没覆盖到的请求: {path} {params}")


class TestPureHelpers(unittest.TestCase):
    def test_inverted_abstract_is_restored_in_order(self):
        idx = {"world": [1], "hello": [0], "again": [2]}
        self.assertEqual(pk.undo_inverted_abstract(idx), "hello world again")

    def test_missing_abstract_is_empty_not_crash(self):
        self.assertEqual(pk.undo_inverted_abstract(None), "")

    def test_slug_strips_filesystem_hostile_chars(self):
        p = pk.Paper.from_json(work("W5", 'Bad/Name: "quoted" <x>?', 2020, 1))
        s = p.slug()
        for bad in '<>:"/\\|?*':
            self.assertNotIn(bad, s)

    def test_slug_is_truncated_on_word_boundary(self):
        long = "word " * 60
        p = pk.Paper.from_json(work("W6", long, 2020, 1))
        self.assertLessEqual(len(p.slug()), 90)

    def test_citekey_skips_stopwords(self):
        p = pk.Paper.from_json(work("W7", "Deep Learning With Transformers", 2020, 1,
                                    authors=("Grace Hopper",)))
        self.assertEqual(p.citekey, "hopper2020transformers")

    def test_doi_prefix_is_stripped(self):
        p = pk.Paper.from_json(work("W8", "T", 2020, 1, doi="10.1/x"))
        self.assertEqual(p.doi, "10.1/x")


class TestScoring(unittest.TestCase):
    def setUp(self):
        c = FakeClient()
        self.seeds = [pk.resolve_seed(c, "10.1000/seed1"),
                      pk.resolve_seed(c, "10.1000/seed2")]
        for s in self.seeds:
            s.is_seed = True
        self.cands = pk.expand(c, self.seeds, per_seed_citers=50)
        pk.score_all(self.cands, self.seeds, this_year=2026)
        self.ranked = sorted(self.cands.values(), key=lambda p: -p.score)

    def test_seeds_are_excluded_from_candidates(self):
        self.assertNotIn("W1", self.cands)
        self.assertNotIn("W2", self.cands)

    def test_multi_seed_papers_outrank_single_seed_ones(self):
        rank = {p.oid: i for i, p in enumerate(self.ranked)}
        # W100 (被两篇种子引用) 和 W200 (引用了两篇种子) 都该压过只挂一篇的
        self.assertLess(rank["W100"], rank["W101"])
        self.assertLess(rank["W200"], rank["W201"])

    def test_breadth_is_recorded_from_both_seeds(self):
        self.assertEqual(self.cands["W100"].seed_links, {"W1", "W2"})
        self.assertEqual(self.cands["W201"].seed_links, {"W1"})

    def test_reasons_are_human_readable(self):
        why = " ".join(self.cands["W100"].reasons)
        self.assertIn("种子", why)

    def test_non_article_types_are_penalised(self):
        self.assertLess(self.cands["W400"].score, self.cands["W201"].score)

    def test_coupling_normalisation_does_not_reward_bulk_refs(self):
        big = pk.Paper.from_json(work("W501", "Survey", 2024, 10,
                                      refs=[f"W9{i:03d}" for i in range(300)] + ["W100"]))
        small = pk.Paper.from_json(work("W502", "Focused", 2024, 10, refs=["W100", "W101"]))
        pool = {"W501": big, "W502": small}
        pk.score_all(pool, self.seeds, this_year=2026)
        self.assertGreater(small.score, big.score)

    def test_tiers_are_assigned_in_rank_order(self):
        pk.assign_tiers(self.ranked, n_s=2, n_a=2)
        self.assertEqual([p.tier for p in self.ranked][:5], ["S", "S", "A", "A", "B"])


class TestOutputs(unittest.TestCase):
    def setUp(self):
        self.p = pk.Paper.from_json(
            work("W100", "Foundational Work", 2015, 5000, doi="10.1/found",
                 pdf="https://example.org/x.pdf"))
        self.p.tier = "S"
        self.p.reasons = ["被 2 篇种子引用"]
        self.p.seed_links = {"W1", "W2"}

    def test_ris_has_required_records(self):
        ris = pk.to_ris(self.p)
        self.assertTrue(ris.startswith("TY  - JOUR"))
        self.assertIn("TI  - Foundational Work", ris)
        self.assertIn("DO  - 10.1/found", ris)
        self.assertIn("KW  - paperkit/S-核心必读", ris)
        self.assertTrue(ris.rstrip().endswith("ER  -"))

    def test_ris_authors_get_one_line_each(self):
        self.assertEqual(pk.to_ris(self.p).count("AU  - "), 2)

    def test_ris_newlines_in_abstract_are_flattened(self):
        self.p.abstract = "line one\n  line two"
        body = pk.to_ris(self.p)
        self.assertIn("AB  - line one line two", body)

    def test_note_frontmatter_parses_as_yaml_ish(self):
        note = pk.render_note(self.p, {"W1": "[[Seed One]]"})
        self.assertTrue(note.startswith("---\n"))
        head = note.split("---")[1]
        for key in ("citekey:", "tier:", "status:", "score:"):
            self.assertIn(key, head)
        self.assertIn("[[Seed One]]", note)

    def test_note_title_quotes_cannot_break_frontmatter(self):
        self.p.title = 'A "quoted" title'
        head = pk.render_note(self.p, {}).split("---")[1]
        self.assertIn("""title: "A 'quoted' title\"""", head)

    def test_map_lists_every_tier_present(self):
        seed = pk.Paper.from_json(work("W1", "Seed One", 2020, 9))
        seed.is_seed = True
        body = pk.render_map([seed], [seed, self.p])
        self.assertIn("## 种子论文", body)
        self.assertIn("S-核心必读", body)
        self.assertIn("[[Lovelace2015 - Foundational Work]]", body)


class TestVaultCommands(unittest.TestCase):
    def test_setup_then_doctor_reports_missing_plugins(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            rc = pk.main(["setup", "--vault", str(vault)])
            self.assertEqual(rc, 0)
            for d in pk.VAULT_DIRS:
                self.assertTrue((vault / d).is_dir(), d)
            self.assertIn("{% for annot in annotations",
                          (vault / "90-模板" / "literature-note.md").read_text())
            self.assertIn("dataview", (vault / "00-面板" / "阅读面板.md").read_text())
            # 插件还没装, doctor 必须报非零
            self.assertEqual(pk.main(["doctor", "--vault", str(vault)]), 1)

    def test_setup_does_not_clobber_edited_template_without_force(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pk.main(["setup", "--vault", str(vault)])
            tpl = vault / "90-模板" / "literature-note.md"
            tpl.write_text("我改过的模板")
            pk.main(["setup", "--vault", str(vault)])
            self.assertEqual(tpl.read_text(), "我改过的模板")
            pk.main(["setup", "--vault", str(vault), "--force"])
            self.assertNotEqual(tpl.read_text(), "我改过的模板")

    def test_setup_on_missing_vault_fails_loudly(self):
        self.assertEqual(pk.main(["setup", "--vault", "/nope/not/here"]), 1)


class TestDiscoverEndToEnd(unittest.TestCase):
    def test_full_pipeline_writes_ris_notes_and_map(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault, out = root / "vault", root / "out"
            vault.mkdir()
            seeds = root / "seeds.txt"
            seeds.write_text("# 注释行会被忽略\n10.1000/seed1\n10.1000/seed2\n")

            pk.main(["setup", "--vault", str(vault)])
            real = pk.Client
            pk.Client = lambda **kw: FakeClient()
            try:
                rc = pk.main([
                    "discover", "--seeds", str(seeds), "--out", str(out),
                    "--vault", str(vault), "--no-pdf",
                    "--max", "6", "--top-s", "2", "--top-a", "2",
                ])
            finally:
                pk.Client = real
            self.assertEqual(rc, 0)

            self.assertTrue((out / "S-核心必读.ris").exists())
            ris = (out / "S-核心必读.ris").read_text()
            self.assertIn("Seed One on Transformers", ris)  # 种子恒为 S

            result = json.loads((out / "paperkit-result.json").read_text())
            self.assertEqual(sum(1 for r in result if r["seed"]), 2)
            tiers = {r["oid"]: r["tier"] for r in result}
            self.assertEqual(tiers["W1"], "S")
            self.assertIn(tiers["W100"], ("S", "A"))

            notes = list((vault / "10-文献笔记").rglob("*.md"))
            self.assertEqual(len(notes), len(result))
            self.assertTrue((vault / "30-论文地图" / "主题地图.md").exists())

    def test_rerun_is_idempotent_and_keeps_my_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault, out = root / "vault", root / "out"
            vault.mkdir()
            seeds = root / "seeds.txt"
            seeds.write_text("10.1000/seed1\n")
            pk.main(["setup", "--vault", str(vault)])
            real = pk.Client
            pk.Client = lambda **kw: FakeClient()
            argv = ["discover", "--seeds", str(seeds), "--out", str(out),
                    "--vault", str(vault), "--no-pdf"]
            try:
                pk.main(argv)
                note = next((vault / "10-文献笔记").rglob("*.md"))
                note.write_text("我读了一半写的笔记")
                pk.main(argv)          # 再跑一次不能把我的笔记冲掉
                self.assertEqual(note.read_text(), "我读了一半写的笔记")
            finally:
                pk.Client = real

    def test_unresolvable_seeds_do_not_abort_the_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            out = root / "out"
            seeds = root / "seeds.txt"
            seeds.write_text("10.9999/does-not-exist\n10.1000/seed1\n")
            real = pk.Client
            pk.Client = lambda **kw: FakeClient()
            try:
                rc = pk.main(["discover", "--seeds", str(seeds),
                              "--out", str(out), "--no-pdf"])
            finally:
                pk.Client = real
            self.assertEqual(rc, 0)

    def test_all_seeds_unresolvable_is_an_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds = root / "seeds.txt"
            seeds.write_text("10.9999/nope\n")
            real = pk.Client
            pk.Client = lambda **kw: FakeClient()
            try:
                rc = pk.main(["discover", "--seeds", str(seeds),
                              "--out", str(root / "out"), "--no-pdf"])
            finally:
                pk.Client = real
            self.assertEqual(rc, 1)


class TestPdfGuard(unittest.TestCase):
    def test_html_login_page_is_not_saved_as_pdf(self):
        import io
        from unittest import mock

        class FakeResp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x.pdf"
            with mock.patch.object(pk.urllib.request, "urlopen",
                                   return_value=FakeResp(b"<!DOCTYPE html><html>")):
                self.assertFalse(pk.download_pdf("https://x/y", dest))
            self.assertFalse(dest.exists())

    def test_real_pdf_is_saved_whole(self):
        import io
        from unittest import mock

        class FakeResp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        body = b"%PDF-1.7\n" + b"x" * 200000
        with tempfile.TemporaryDirectory() as tmp:
            dest = Path(tmp) / "x.pdf"
            with mock.patch.object(pk.urllib.request, "urlopen",
                                   return_value=FakeResp(body)):
                self.assertTrue(pk.download_pdf("https://x/y", dest))
            self.assertEqual(dest.read_bytes(), body)
            self.assertFalse(dest.with_suffix(".part").exists())

    def test_network_error_is_swallowed_not_raised(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pk.urllib.request, "urlopen",
                                   side_effect=OSError("boom")):
                self.assertFalse(pk.download_pdf("https://x/y", Path(tmp) / "x.pdf"))


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestFrontmatterHardening(unittest.TestCase):
    def test_author_names_with_quotes_do_not_break_yaml(self):
        p = pk.Paper.from_json(work("W9", "T", 2020, 1, authors=('Ann "Q" Lee',)))
        p.tier = "S"
        head = pk.render_note(p, {}).split("---")[1]
        self.assertIn("""authors: ["Ann 'Q' Lee"]""", head)


class TestWindowsRobustness(unittest.TestCase):
    """Windows 上两个真实会炸的地方."""

    def test_seeds_saved_by_old_notepad_with_bom_still_parse(self):
        # 老版记事本另存为 UTF-8 会带 BOM, 第一行开头多一个 ﻿,
        # W 号那一行 fullmatch 会失败, 标题搜索也会被污染.
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "seeds.txt"
            path.write_bytes("﻿W2741809807\n10.1000/seed1\n".encode("utf-8"))
            self.assertEqual(pk.read_seeds(path), ["W2741809807", "10.1000/seed1"])

    def test_gbk_console_cannot_crash_on_status_symbols(self):
        # 中文 Windows 终端输出被重定向时编码是 GBK, "✓" 不在 GBK 里,
        # 严格模式下 print 直接抛 UnicodeEncodeError.
        import io
        gbk = io.TextIOWrapper(io.BytesIO(), encoding="gbk", errors="strict")
        real = sys.stderr
        sys.stderr = gbk
        try:
            with tempfile.TemporaryDirectory() as tmp:
                rc = pk.main(["setup", "--vault", tmp])
        finally:
            sys.stderr = real
        self.assertEqual(rc, 0)


# --------------------------------------------------------------------------
# 模板必须和 Zotero Integration 插件源码对得上
# --------------------------------------------------------------------------

def zi_color_category(hexstr):
    """逐行移植自插件源码 src/bbt/helpers.ts 的 hexToHSL + getColorCategory."""
    r, g, b = (int(hexstr[i:i + 2], 16) / 255 for i in (1, 3, 5))
    cmin, cmax = min(r, g, b), max(r, g, b)
    delta = cmax - cmin
    if delta == 0:
        h = 0.0
    elif cmax == r:
        h = ((g - b) / delta) % 6
    elif cmax == g:
        h = (b - r) / delta + 2
    else:
        h = (r - g) / delta + 4
    h = round(h * 60)
    if h < 0:
        h += 360
    l = (cmax + cmin) / 2
    s = 0 if delta == 0 else delta / (1 - abs(2 * l - 1))
    s, l = s * 100, l * 100
    if l < 12: return "Black"
    if l > 98: return "White"
    if s < 2: return "Gray"
    for bound, name in [(15, "Red"), (45, "Orange"), (65, "Yellow"), (170, "Green"),
                        (190, "Cyan"), (255, "Blue"), (280, "Purple"), (335, "Magenta")]:
        if h < bound:
            return name
    return "Red"


# Zotero 阅读器的默认标注色 (7 起沿用)
ZOTERO_COLORS = {"#ffd400": "Yellow", "#ff6666": "Red", "#5fb236": "Green", "#2ea8e5": "Blue"}
# 插件 filterBy 真正支持的命令 (src/bbt/template.env.ts FilterByCmd)
ZI_FILTER_CMDS = {"startswith", "endswith", "contains",
                  "dateafter", "dateonorafter", "datebefore", "dateonorbefore"}


class TestTemplateMatchesPluginSource(unittest.TestCase):
    def test_zotero_default_colours_land_in_the_four_categories(self):
        for hexstr, want in ZOTERO_COLORS.items():
            self.assertEqual(zi_color_category(hexstr), want, hexstr)

    def test_template_only_uses_filter_commands_the_plugin_supports(self):
        # 之前用的 "eq" 插件不认, filterBy 会一路落到 return false,
        # 四个颜色小节永远是空的, 而且不报错.
        cmds = re.findall(r'filterby\("[^"]+",\s*"([^"]+)"', pk.ZI_TEMPLATE)
        self.assertTrue(cmds)
        for c in cmds:
            self.assertIn(c, ZI_FILTER_CMDS)

    def test_every_colour_section_matches_a_zotero_default_colour(self):
        wanted = re.findall(r'filterby\("colorCategory",\s*"startswith",\s*"([^"]+)"\)',
                            pk.ZI_TEMPLATE)
        self.assertEqual(sorted(wanted), sorted(v.lower() for v in ZOTERO_COLORS.values()))

    def test_template_uses_variables_the_plugin_actually_sets(self):
        self.assertNotIn("pdfZoteroLink", pk.ZI_TEMPLATE)   # 插件里不存在这个变量
        self.assertIn("{{desktopURI}}", pk.ZI_TEMPLATE)     # export.ts 第 279 行

    def test_missing_date_does_not_leak_an_error_string_into_frontmatter(self):
        # 论文没日期时 date 是 null, 裸调 format 会输出一串报错文字.
        head = pk.ZI_TEMPLATE.split("---")[1]
        self.assertRegex(head, r"\{% if date %\}\{\{date \| format\(\"YYYY\"\)\}\}\{% endif %\}")


# --------------------------------------------------------------------------
# install: 一键安装
# --------------------------------------------------------------------------

REGISTRY = json.dumps([
    {"id": "dataview", "repo": "blacksmithgu/obsidian-dataview"},
    {"id": "obsidian-zotero-desktop-connector",
     "repo": "obsidian-community/obsidian-zotero-integration"},
]).encode()


def fake_web(missing=(), fail=()):
    """按 URL 返回假内容. missing 里的返回 None (404), fail 里的抛网络错误."""
    def get(url, timeout=30):
        for key in fail:
            if key in url:
                raise OSError(f"网络不通: {url}")
        for key in missing:
            if key in url:
                return None
        if url == pk.PLUGIN_REGISTRY:
            return REGISTRY
        if "api.github.com/repos/retorquere/zotero-better-bibtex" in url:
            return json.dumps({"assets": [
                {"name": "zotero-better-bibtex-9.0.64.xpi.sha256", "browser_download_url": "https://x/sha"},
                {"name": "zotero-better-bibtex-9.0.64.xpi", "browser_download_url": "https://x/bbt.xpi"},
            ]}).encode()
        if url == "https://x/bbt.xpi":
            return b"PK\x03\x04fake-xpi"
        if url.startswith("https://raw.githubusercontent.com/") and url.endswith("/HEAD/manifest.json"):
            repo = url.split("raw.githubusercontent.com/", 1)[1].rsplit("/HEAD/", 1)[0]
            pid = {"blacksmithgu/obsidian-dataview": "dataview"}.get(
                repo, "obsidian-zotero-desktop-connector")
            return json.dumps({"id": pid, "version": "1.2.3"}).encode()
        if "/releases/download/" in url or "/releases/latest/download/" in url:
            return f"// {url.rsplit('/', 1)[-1]}".encode()
        raise AssertionError(f"没覆盖到的 URL: {url}")
    return get


def write_obsidian_json(cfg_dir, vaults):
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "obsidian.json").write_text(json.dumps({"vaults": {
        f"id{i}": {"path": str(p), "ts": ts, "open": i == 0}
        for i, (p, ts) in enumerate(vaults)
    }}), encoding="utf-8")


class TestFindVaults(unittest.TestCase):
    def test_lists_existing_vaults_most_recent_first(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old, new = root / "旧库", root / "Research"
            old.mkdir(); new.mkdir()
            write_obsidian_json(root / "cfg", [(old, 100), (new, 900), (root / "已删除", 999)])
            self.assertEqual(pk.find_vaults(root / "cfg"), [new, old])

    def test_no_config_or_broken_config_means_no_vaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = Path(tmp)
            self.assertEqual(pk.find_vaults(cfg), [])
            (cfg / "obsidian.json").write_text("{broken", encoding="utf-8")
            self.assertEqual(pk.find_vaults(cfg), [])


class TestChooseVault(unittest.TestCase):
    def test_single_vault_is_used_without_asking(self):
        v = Path("/v")
        ask = unittest.mock.Mock(side_effect=AssertionError("不该问"))
        self.assertEqual(pk.choose_vault([v], ask), v)

    def test_multiple_vaults_pick_by_number_and_retry_on_garbage(self):
        a, b = Path("/a"), Path("/b")
        ask = unittest.mock.Mock(side_effect=["x", "9", "2"])
        self.assertEqual(pk.choose_vault([a, b], ask), b)
        self.assertEqual(ask.call_count, 3)

    def test_no_vault_asks_for_a_path_until_it_exists(self):
        with tempfile.TemporaryDirectory() as tmp:
            ask = unittest.mock.Mock(side_effect=["/不存在的路径", f'"{tmp}"'])
            self.assertEqual(pk.choose_vault([], ask), Path(tmp))

    def test_empty_answer_aborts_instead_of_looping_forever(self):
        ask = unittest.mock.Mock(side_effect=[""])
        self.assertIsNone(pk.choose_vault([], ask))


class TestPlugins(unittest.TestCase):
    def test_install_downloads_release_assets_into_plugin_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            reg = pk.load_plugin_registry(fake_web())
            pk.install_plugin(vault, "dataview", reg, fake_web())
            folder = vault / ".obsidian" / "plugins" / "dataview"
            self.assertEqual((folder / "main.js").read_text(), "// main.js")
            self.assertTrue((folder / "manifest.json").exists())
            self.assertTrue((folder / "styles.css").exists())

    def test_repo_is_resolved_from_registry_not_hardcoded(self):
        # Zotero Integration 已经搬过两次家, 写死地址迟早下错.
        seen = []
        web = fake_web()
        def spy(url, timeout=30):
            seen.append(url)
            return web(url, timeout)
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(spy)
            pk.install_plugin(Path(tmp), "obsidian-zotero-desktop-connector", reg, spy)
        self.assertTrue(any("obsidian-community/obsidian-zotero-integration" in u for u in seen))

    def test_missing_styles_css_is_fine(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(fake_web())
            pk.install_plugin(Path(tmp), "dataview", reg, fake_web(missing=["styles.css"]))
            self.assertTrue((Path(tmp) / ".obsidian/plugins/dataview/main.js").exists())

    def test_missing_main_js_fails_and_leaves_no_half_installed_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(fake_web())
            with self.assertRaises(RuntimeError):
                pk.install_plugin(Path(tmp), "dataview", reg, fake_web(missing=["main.js"]))
            self.assertFalse((Path(tmp) / ".obsidian/plugins/dataview/manifest.json").exists())

    def test_unknown_plugin_id_fails_clearly(self):
        reg = pk.load_plugin_registry(fake_web())
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(RuntimeError):
                pk.install_plugin(Path(tmp), "no-such-plugin", reg, fake_web())

    def test_enable_merges_without_dropping_or_duplicating(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            (vault / ".obsidian").mkdir()
            f = vault / ".obsidian" / "community-plugins.json"
            f.write_text(json.dumps(["calendar", "dataview"]))
            pk.enable_plugins(vault, ["dataview", "obsidian-zotero-desktop-connector"])
            self.assertEqual(json.loads(f.read_text()),
                             ["calendar", "dataview", "obsidian-zotero-desktop-connector"])

    def test_enable_creates_the_list_when_absent(self):
        with tempfile.TemporaryDirectory() as tmp:
            pk.enable_plugins(Path(tmp), ["dataview"])
            f = Path(tmp) / ".obsidian" / "community-plugins.json"
            self.assertEqual(json.loads(f.read_text()), ["dataview"])


class TestZoteroIntegrationConfig(unittest.TestCase):
    def data(self, vault):
        return vault / ".obsidian/plugins/obsidian-zotero-desktop-connector/data.json"

    def test_fresh_config_gets_our_import_format(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self.assertTrue(pk.configure_zotero_integration(vault))
            cfg = json.loads(self.data(vault).read_text(encoding="utf-8"))
            fmt = cfg["exportFormats"][0]
            # 字段名来自插件 src/types.ts 的 ExportFormat
            self.assertEqual(fmt["name"], pk.ZI_FORMAT_NAME)
            self.assertEqual(fmt["outputPathTemplate"], "10-文献笔记/{{citekey}}.md")
            self.assertEqual(fmt["templatePath"], "90-模板/literature-note.md")
            self.assertIn("imageOutputPathTemplate", fmt)
            self.assertIn("imageBaseNameTemplate", fmt)

    def test_existing_settings_and_formats_are_preserved(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self.data(vault).parent.mkdir(parents=True)
            self.data(vault).write_text(json.dumps({
                "database": "Zotero", "citeFormats": [{"name": "我的引用"}],
                "exportFormats": [{"name": "我自己的格式", "outputPathTemplate": "x.md"}],
            }), encoding="utf-8")
            pk.configure_zotero_integration(vault)
            pk.configure_zotero_integration(vault)   # 跑两次不能重复加
            cfg = json.loads(self.data(vault).read_text(encoding="utf-8"))
            self.assertEqual([f["name"] for f in cfg["exportFormats"]],
                             ["我自己的格式", pk.ZI_FORMAT_NAME])
            self.assertEqual(cfg["citeFormats"], [{"name": "我的引用"}])

    def test_corrupt_config_is_left_alone(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            self.data(vault).parent.mkdir(parents=True)
            self.data(vault).write_text("{not json", encoding="utf-8")
            self.assertFalse(pk.configure_zotero_integration(vault))
            self.assertEqual(self.data(vault).read_text(encoding="utf-8"), "{not json")


class TestBetterBibTeX(unittest.TestCase):
    def test_detects_installed_xpi_in_any_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            p1, p2 = Path(tmp) / "a.default", Path(tmp) / "b.default"
            (p1 / "extensions").mkdir(parents=True)
            (p2 / "extensions").mkdir(parents=True)
            self.assertFalse(pk.bbt_installed([p1, p2]))
            (p2 / "extensions" / f"{pk.BBT_ID}.xpi").write_bytes(b"x")
            self.assertTrue(pk.bbt_installed([p1, p2]))

    def test_downloads_the_xpi_not_the_checksum(self):
        with tempfile.TemporaryDirectory() as tmp:
            dest = pk.download_bbt(Path(tmp), fake_web())
            self.assertEqual(dest.name, "zotero-better-bibtex-9.0.64.xpi")
            self.assertEqual(dest.read_bytes(), b"PK\x03\x04fake-xpi")


class TestInstallCommand(unittest.TestCase):
    def run_install(self, root, web=None, running=False, argv=()):
        from unittest import mock
        vault = root / "Research"
        vault.mkdir(exist_ok=True)
        cfg = root / "cfg"
        write_obsidian_json(cfg, [(vault, 1)])
        profile = root / "zotero" / "x.default"
        (profile / "extensions").mkdir(parents=True, exist_ok=True)
        work = root / "work"
        work.mkdir(exist_ok=True)
        with mock.patch.object(pk, "http_get", web or fake_web()), \
             mock.patch.object(pk, "obsidian_config_dir", lambda: cfg), \
             mock.patch.object(pk, "zotero_profile_dirs", lambda: [profile]), \
             mock.patch.object(pk, "downloads_dir", lambda: root / "dl"), \
             mock.patch.object(pk, "obsidian_running", lambda: running), \
             mock.patch.object(pk, "bbt_live", lambda: False), \
             mock.patch("builtins.input", lambda *_: ""), \
             mock.patch.object(pk.Path, "cwd", lambda: work):
            rc = pk.main(["install", *argv])
        return rc, vault, work

    def test_one_shot_install_wires_everything(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, vault, work = self.run_install(root)
            for d in pk.VAULT_DIRS:
                self.assertTrue((vault / d).is_dir(), d)
            for pid in pk.PLUGINS:
                self.assertTrue((vault / ".obsidian/plugins" / pid / "main.js").exists(), pid)
            enabled = json.loads((vault / ".obsidian/community-plugins.json").read_text())
            self.assertEqual(sorted(enabled), sorted(pk.PLUGINS))
            self.assertTrue((vault / ".obsidian/plugins/obsidian-zotero-desktop-connector/data.json").exists())
            self.assertTrue((work / "seeds.txt").exists())
            self.assertTrue((root / "dl" / "zotero-better-bibtex-9.0.64.xpi").exists())
            # 只剩 BBT 需要手动点 -> 不算失败
            self.assertEqual(rc, 0)

    def test_rerun_is_safe_and_keeps_user_edits(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _, vault, work = self.run_install(root)
            (work / "seeds.txt").write_text("我的种子\n", encoding="utf-8")
            tpl = vault / "90-模板" / "literature-note.md"
            tpl.write_text("我改过的模板", encoding="utf-8")
            self.run_install(root)
            self.assertEqual((work / "seeds.txt").read_text(encoding="utf-8"), "我的种子\n")
            self.assertEqual(tpl.read_text(encoding="utf-8"), "我改过的模板")
            enabled = json.loads((vault / ".obsidian/community-plugins.json").read_text())
            self.assertEqual(len(enabled), len(set(enabled)))

    def test_network_failure_does_not_stop_the_rest(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, vault, work = self.run_install(root, web=fake_web(fail=["github"]))
            self.assertNotEqual(rc, 0)                       # 要报失败
            self.assertTrue((vault / "90-模板").is_dir())     # 但本地步骤都做了
            self.assertTrue((work / "seeds.txt").exists())

    def test_refuses_to_touch_plugins_while_obsidian_is_open(self):
        # Obsidian 开着的时候改插件列表, 它退出时会用内存里的旧列表覆盖回去.
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            rc, vault, _ = self.run_install(root, running=True)
            self.assertNotEqual(rc, 0)
            self.assertFalse((vault / ".obsidian/community-plugins.json").exists())


class TestDoctorAfterInstall(unittest.TestCase):
    def test_bib_file_is_not_required(self):
        # Zotero Integration 直接走 BBT 的 JSON-RPC, 从来不读 .bib.
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pk.main(["setup", "--vault", str(vault)])
            for pid in pk.PLUGINS:
                (vault / ".obsidian/plugins" / pid).mkdir(parents=True)
                (vault / ".obsidian/plugins" / pid / "manifest.json").write_text("{}")
            pk.enable_plugins(vault, list(pk.PLUGINS))
            from unittest import mock
            with mock.patch.object(pk, "bbt_live", lambda: True):
                self.assertEqual(pk.main(["doctor", "--vault", str(vault)]), 0)

    def test_installed_but_not_enabled_plugin_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pk.main(["setup", "--vault", str(vault)])
            for pid in pk.PLUGINS:
                (vault / ".obsidian/plugins" / pid).mkdir(parents=True)
                (vault / ".obsidian/plugins" / pid / "manifest.json").write_text("{}")
            from unittest import mock
            with mock.patch.object(pk, "bbt_live", lambda: True):
                self.assertEqual(pk.main(["doctor", "--vault", str(vault)]), 1)

    def test_zotero_not_reachable_is_flagged(self):
        with tempfile.TemporaryDirectory() as tmp:
            vault = Path(tmp)
            pk.main(["setup", "--vault", str(vault)])
            for pid in pk.PLUGINS:
                (vault / ".obsidian/plugins" / pid).mkdir(parents=True)
                (vault / ".obsidian/plugins" / pid / "manifest.json").write_text("{}")
            pk.enable_plugins(vault, list(pk.PLUGINS))
            from unittest import mock
            with mock.patch.object(pk, "bbt_live", lambda: False):
                self.assertEqual(pk.main(["doctor", "--vault", str(vault)]), 1)


class TestPluginVersionPinning(unittest.TestCase):
    """和 Obsidian 自己装插件的方式一致: 读仓库 manifest 的版本号, 下那个版本."""

    def spy(self, web):
        seen = []
        def get(url, timeout=30):
            seen.append(url)
            return web(url, timeout)
        return get, seen

    def test_downloads_the_version_named_in_the_repo_manifest(self):
        get, seen = self.spy(fake_web())
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(get)
            pk.install_plugin(Path(tmp), "dataview", reg, get)
        assets = [u for u in seen if "/releases/" in u]
        self.assertTrue(assets)
        self.assertTrue(all("/releases/download/1.2.3/" in u for u in assets), assets)

    def test_falls_back_to_latest_when_the_versioned_asset_is_missing(self):
        get, seen = self.spy(fake_web(missing=["/releases/download/1.2.3/main.js"]))
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(get)
            pk.install_plugin(Path(tmp), "dataview", reg, get)
            self.assertTrue((Path(tmp) / ".obsidian/plugins/dataview/main.js").exists())
        self.assertTrue(any("/releases/latest/download/main.js" in u for u in seen))

    def test_falls_back_to_latest_when_manifest_is_unreadable(self):
        get, seen = self.spy(fake_web(missing=["/HEAD/manifest.json"]))
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(get)
            pk.install_plugin(Path(tmp), "dataview", reg, get)
        self.assertTrue(any("/releases/latest/download/" in u for u in seen))

    def test_refuses_a_repo_whose_manifest_is_a_different_plugin(self):
        # 注册表被篡改或仓库被转手时, 不能把别的插件装成这个 id.
        def web(url, timeout=30):
            if url.endswith("/HEAD/manifest.json"):
                return json.dumps({"id": "something-else", "version": "9.9.9"}).encode()
            return fake_web()(url, timeout)
        with tempfile.TemporaryDirectory() as tmp:
            reg = pk.load_plugin_registry(web)
            with self.assertRaises(RuntimeError):
                pk.install_plugin(Path(tmp), "dataview", reg, web)
            self.assertFalse((Path(tmp) / ".obsidian/plugins/dataview").exists())


class TestObsidianRunningOnChineseWindows(unittest.TestCase):
    def test_gbk_tasklist_output_cannot_hide_a_running_obsidian(self):
        # 中文 Windows 的 tasklist 输出 GBK. Python 开了 UTF-8 模式时严格解码会抛错,
        # 旧代码吞掉异常返回"没开", 结果在 Obsidian 开着时去改插件列表.
        from unittest import mock

        def fake_run(cmd, **kw):
            if kw.get("errors") not in ("replace", "ignore"):
                raise UnicodeDecodeError("utf-8", b"\xd0\xc5", 0, 1, "invalid")
            return mock.Mock(stdout="Obsidian.exe  1234 Console  1  300,000 K\n", returncode=0)

        import subprocess
        with mock.patch.object(pk.sys, "platform", "win32"), \
             mock.patch.object(subprocess, "run", fake_run):
            self.assertTrue(pk.obsidian_running())


class TestVaultDiscoveryFallback(unittest.TestCase):
    """obsidian.json 读不到时, 直接在硬盘上找带 .obsidian 的目录."""

    def make_vault(self, path):
        (path / ".obsidian").mkdir(parents=True)
        return path

    def test_scan_finds_vaults_by_their_dot_obsidian_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            a = self.make_vault(home / "Documents" / "Research")
            b = self.make_vault(home / "OneDrive" / "文档" / "读书笔记")
            (home / "Documents" / "不是库").mkdir()
            self.assertEqual(sorted(pk.scan_for_vaults([home])), sorted([a, b]))

    def test_scan_does_not_descend_into_a_vault_or_system_folders(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            outer = self.make_vault(home / "Vault")
            self.make_vault(outer / "附件" / "嵌套")          # 库里面的不算
            self.make_vault(home / "AppData" / "Roaming" / "x")  # 系统目录跳过
            self.make_vault(home / ".cache" / "y")               # 隐藏目录跳过
            self.make_vault(home / "node_modules" / "z")
            self.assertEqual(pk.scan_for_vaults([home]), [outer])

    def test_scan_respects_depth_limit(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            self.make_vault(home / "a" / "b" / "c" / "d" / "e" / "too-deep")
            self.assertEqual(pk.scan_for_vaults([home], depth=3), [])

    def test_scan_survives_unreadable_and_missing_roots(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            v = self.make_vault(home / "V")
            self.assertEqual(pk.scan_for_vaults([home / "不存在", home]), [v])

    def test_scan_lists_each_vault_once_when_roots_overlap(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp)
            v = self.make_vault(home / "Documents" / "V")
            self.assertEqual(pk.scan_for_vaults([home, home / "Documents"]), [v])

    def test_missing_config_explains_why(self):
        import io
        from unittest import mock
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pk.sys, "stderr", buf):
            pk.find_vaults(Path(tmp))
        self.assertIn("obsidian.json", buf.getvalue())

    def test_config_pointing_at_deleted_folders_explains_why(self):
        import io
        from unittest import mock
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pk.sys, "stderr", buf):
            cfg = Path(tmp)
            write_obsidian_json(cfg, [(cfg / "早就删了", 1)])
            self.assertEqual(pk.find_vaults(cfg), [])
        self.assertIn("早就删了", buf.getvalue())

    def test_install_falls_back_to_disk_scan(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = self.make_vault(root / "home" / "Documents" / "Research")
            (root / "prof" / "extensions").mkdir(parents=True)
            (root / "work").mkdir()
            with mock.patch.object(pk, "http_get", fake_web()), \
                 mock.patch.object(pk, "obsidian_config_dir", lambda: root / "没有这个目录"), \
                 mock.patch.object(pk, "vault_search_roots", lambda: [root / "home"]), \
                 mock.patch.object(pk, "zotero_profile_dirs", lambda: [root / "prof"]), \
                 mock.patch.object(pk, "downloads_dir", lambda: root / "dl"), \
                 mock.patch.object(pk, "obsidian_running", lambda: False), \
                 mock.patch.object(pk, "bbt_live", lambda: False), \
                 mock.patch("builtins.input", mock.Mock(side_effect=AssertionError("不该问"))), \
                 mock.patch.object(pk.Path, "cwd", lambda: root / "work"):
                rc = pk.main(["install"])
            self.assertEqual(rc, 0)
            self.assertTrue((vault / "90-模板" / "literature-note.md").exists())

    def test_search_roots_include_other_drives_on_windows(self):
        from unittest import mock
        with mock.patch.object(pk.sys, "platform", "win32"), \
             mock.patch.object(pk, "_windows_drives", lambda: [Path("C:/"), Path("D:/")]):
            roots = pk.vault_search_roots()
        self.assertIn(Path("D:/"), roots)
        self.assertNotIn(Path("C:/"), roots)   # C 盘根太大, 只搜用户目录


class TestInstallTellsWhatItIsWaitingOn(unittest.TestCase):
    def test_announces_bbt_download_before_starting_it(self):
        # 真实用户在这里以为卡死了: 下载前没有任何输出.
        import io
        from unittest import mock
        buf = io.StringIO()
        seen_before_download = []

        def slow_download(dest, get=None):
            seen_before_download.append(buf.getvalue())
            return dest / "zotero-better-bibtex-9.0.64.xpi"

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "V"; vault.mkdir()
            (root / "prof" / "extensions").mkdir(parents=True)
            with mock.patch.object(pk.sys, "stderr", buf), \
                 mock.patch.object(pk, "zotero_profile_dirs", lambda: [root / "prof"]), \
                 mock.patch.object(pk, "downloads_dir", lambda: root / "dl"), \
                 mock.patch.object(pk, "bbt_live", lambda: False), \
                 mock.patch.object(pk, "download_bbt", slow_download), \
                 mock.patch.object(pk.Path, "cwd", lambda: root):
                pk.main(["install", "--vault", str(vault), "--skip-plugins"])
        self.assertIn("Better BibTeX", seen_before_download[0].rsplit("✓ 库结构就绪", 1)[-1])
        self.assertIn("Ctrl+C", seen_before_download[0])


class TestWindowsKnownFolders(unittest.TestCase):
    """真实用户的"下载"和文档在 D 盘, 只看 %USERPROFILE%/%APPDATA% 会找错地方.
    Windows 上以系统的已知文件夹 API 为准, 环境变量只作为候选之一."""

    def patched(self, known, env_appdata, home):
        from unittest import mock
        return [
            mock.patch.object(pk.sys, "platform", "win32"),
            mock.patch.object(pk, "_known_folder", lambda guid: known.get(guid)),
            mock.patch.dict(pk.os.environ, {"APPDATA": str(env_appdata)}),
            mock.patch.object(pk.Path, "home", lambda: home),
        ]

    def run_with(self, patches, fn):
        import contextlib
        with contextlib.ExitStack() as st:
            for p in patches:
                st.enter_context(p)
            return fn()

    def test_downloads_follow_the_relocated_known_folder(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            moved = root / "D" / "Users" / "ASUS" / "Downloads"; moved.mkdir(parents=True)
            home = root / "C" / "Users" / "ASUS"; home.mkdir(parents=True)   # 没有 Downloads
            got = self.run_with(
                self.patched({pk.FOLDERID_DOWNLOADS: moved}, root / "none", home),
                pk.downloads_dir)
            self.assertEqual(got, moved)

    def test_obsidian_config_found_via_known_appdata_when_env_var_is_stale(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "D" / "AppData" / "Roaming"
            (real / "obsidian").mkdir(parents=True)
            (real / "obsidian" / "obsidian.json").write_text("{}")
            stale = root / "C" / "AppData" / "Roaming"; stale.mkdir(parents=True)
            got = self.run_with(
                self.patched({pk.FOLDERID_ROAMING_APPDATA: real}, stale, root / "home"),
                pk.obsidian_config_dir)
            self.assertEqual(got, real / "obsidian")

    def test_zotero_profiles_are_collected_from_every_appdata_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            real = root / "D" / "Roaming"
            prof = real / "Zotero" / "Zotero" / "Profiles" / "abc.default"
            prof.mkdir(parents=True)
            stale = root / "C" / "Roaming"; stale.mkdir(parents=True)
            got = self.run_with(
                self.patched({pk.FOLDERID_ROAMING_APPDATA: real}, stale, root / "home"),
                pk.zotero_profile_dirs)
            self.assertEqual(got, [prof])

    def test_known_folder_api_failure_falls_back_to_env(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            env = root / "Roaming"
            (env / "obsidian").mkdir(parents=True)
            (env / "obsidian" / "obsidian.json").write_text("{}")
            got = self.run_with(self.patched({}, env, root / "home"), pk.obsidian_config_dir)
            self.assertEqual(got, env / "obsidian")

    def test_known_folder_is_none_off_windows(self):
        from unittest import mock
        with mock.patch.object(pk.sys, "platform", "linux"):
            self.assertIsNone(pk._known_folder(pk.FOLDERID_DOWNLOADS))

    def test_missing_zotero_hint_names_a_supported_version(self):
        # 最新 Better BibTeX 要求 Zotero >= 8.0.1, 让人去装 Zotero 7 是错的.
        self.assertNotIn("Zotero 7", pk.cmd_install.__code__.co_consts.__repr__())


# --------------------------------------------------------------------------
# 从已下载的 PDF 生成种子
# --------------------------------------------------------------------------

import zlib


def make_pdf(path, *, info=b"", xmp=b"", streams=(), raw=b""):
    """造一个足够像的 PDF: 可选 Info 字典、XMP、Flate 压缩的内容流."""
    parts = [b"%PDF-1.7\n"]
    for i, content in enumerate(streams, 1):
        comp = zlib.compress(content)
        parts.append(b"%d 0 obj\n<< /Length %d /Filter /FlateDecode >>\nstream\n"
                     % (i, len(comp)) + comp + b"\nendstream\nendobj\n")
    if xmp:
        parts.append(b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>" + xmp + b"</x:xmpmeta>\n")
    if info:
        parts.append(b"99 0 obj\n<< " + info + b" /Producer (test) >>\nendobj\n")
    parts.append(raw)
    parts.append(b"%%EOF\n")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"".join(parts))
    return path


REFS = b"".join(b"[%d] Some ref. doi:10.1000/ref%d\n" % (i, i) for i in range(30))


class TestPdfIdentifiers(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_xmp_doi_beats_the_reference_list(self):
        p = make_pdf(self.dir / "a.pdf", xmp=b"<prism:doi>10.1016/j.cell.2020.01.001</prism:doi>",
                     streams=[REFS])
        got = pk.pdf_identifiers(p)
        self.assertEqual(got["doi"], "10.1016/j.cell.2020.01.001")
        self.assertEqual(got["how"], "元数据 DOI")

    def test_xmp_attribute_form_and_doi_url_prefix(self):
        p = make_pdf(self.dir / "b.pdf",
                     xmp=b'<rdf:Description crossmark:DOI="https://doi.org/10.1038/S41586-021-03819-2"/>')
        self.assertEqual(pk.pdf_identifiers(p)["doi"], "10.1038/s41586-021-03819-2")

    def test_doi_repeated_in_page_headers_beats_singletons(self):
        page = b"BT (https://doi.org/10.1109/TPAMI.2019.2913372) Tj ET\n"
        p = make_pdf(self.dir / "c.pdf", streams=[page * 8, REFS])
        got = pk.pdf_identifiers(p)
        self.assertEqual(got["doi"], "10.1109/tpami.2019.2913372")
        self.assertEqual(got["how"], "正文反复出现的 DOI")

    def test_single_distinct_doi_is_taken(self):
        p = make_pdf(self.dir / "d.pdf", raw=b"/URI (https://doi.org/10.1145/3290605.3300233.)")
        self.assertEqual(pk.pdf_identifiers(p)["doi"], "10.1145/3290605.3300233")

    def test_many_singleton_dois_are_ambiguous_so_title_is_used(self):
        p = make_pdf(self.dir / "e.pdf", streams=[REFS],
                     info=b"/Title (Deep Residual Learning for Image Recognition)")
        got = pk.pdf_identifiers(p)
        self.assertIsNone(got["doi"])
        self.assertEqual(got["title"], "Deep Residual Learning for Image Recognition")
        self.assertEqual(got["how"], "PDF 标题")

    def test_utf16_hex_title_decodes_chinese(self):
        title = "基于深度学习的图像分割方法研究"
        hexed = ("FEFF" + title.encode("utf-16-be").hex()).encode()
        p = make_pdf(self.dir / "f.pdf", info=b"/Title <" + hexed + b">")
        self.assertEqual(pk.pdf_identifiers(p)["title"], title)

    def test_literal_title_with_escapes(self):
        p = make_pdf(self.dir / "g.pdf", info=rb"/Title (Attention \(Is\) All You Need\\Really)")
        self.assertEqual(pk.pdf_identifiers(p)["title"], r"Attention (Is) All You Need\Really")

    def test_junk_word_title_falls_back_to_filename(self):
        p = make_pdf(self.dir / "Graph Neural Networks A Review.pdf",
                     info=b"/Title (Microsoft Word - draft_v3.docx)")
        got = pk.pdf_identifiers(p)
        self.assertEqual(got["title"], "Graph Neural Networks A Review")
        self.assertEqual(got["how"], "文件名")

    def test_cnki_style_filename_drops_trailing_author(self):
        p = make_pdf(self.dir / "基于深度学习的图像分割研究_张三.pdf")
        self.assertEqual(pk.pdf_identifiers(p)["title"], "基于深度学习的图像分割研究")

    def test_duplicate_download_suffix_is_dropped(self):
        p = make_pdf(self.dir / "Graph Attention Networks (1).pdf")
        self.assertEqual(pk.pdf_identifiers(p)["title"], "Graph Attention Networks")

    def test_arxiv_filename(self):
        p = make_pdf(self.dir / "2103.00020v2.pdf")
        got = pk.pdf_identifiers(p)
        self.assertEqual(got["arxiv"], "2103.00020")
        self.assertEqual(got["how"], "arXiv 编号")

    def test_arxiv_watermark_in_text(self):
        p = make_pdf(self.dir / "paper.pdf", streams=[b"(arXiv:1706.03762v5  [cs.CL]  6 Dec 2017) Tj"])
        self.assertEqual(pk.pdf_identifiers(p)["arxiv"], "1706.03762")

    def test_garbage_file_does_not_crash(self):
        p = self.dir / "Some Paper Title Here.pdf"
        p.write_bytes(b"\x00\xff not a pdf at all stream\n\x78\x9c garbage endstream")
        got = pk.pdf_identifiers(p)
        self.assertEqual(got["title"], "Some Paper Title Here")

    def test_seed_line_carries_the_source_as_inline_comment(self):
        p = make_pdf(self.dir / "x.pdf", xmp=b"<prism:doi>10.1000/abc1</prism:doi>")
        line = pk.seed_line(pk.pdf_identifiers(p), p)
        self.assertTrue(line.startswith("10.1000/abc1"))
        self.assertIn("# x.pdf", line)


class TestInlineSeedComments(unittest.TestCase):
    def test_inline_comment_is_stripped(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "s.txt"
            f.write_text("10.1000/abc1  # a.pdf (元数据 DOI)\n"
                         "Graph Attention Networks  # b.pdf (文件名)\n"
                         "C# in Depth\n", encoding="utf-8")
            self.assertEqual(pk.read_seeds(f),
                             ["10.1000/abc1", "Graph Attention Networks", "C# in Depth"])


class TestSeedsCommand(unittest.TestCase):
    def test_scans_folder_recursively_and_appends_without_duplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            lib = root / "毕业论文" / "01_文献" / "原始PDF"
            make_pdf(lib / "a.pdf", xmp=b"<prism:doi>10.1000/aaa1</prism:doi>")
            make_pdf(lib / "子文件夹" / "b.pdf", xmp=b"<prism:doi>10.1000/bbb1</prism:doi>")
            make_pdf(lib / "c.PDF", xmp=b"<prism:doi>10.1000/aaa1</prism:doi>")   # 同一篇下了两次
            (lib / "notes.docx").write_bytes(b"x")
            seeds = root / "seeds.txt"
            seeds.write_text("# 我的注释\n10.9/mine\n", encoding="utf-8")

            rc = pk.main(["seeds", "--from-pdfs", str(lib), "--out", str(seeds)])
            self.assertEqual(rc, 0)
            self.assertEqual(pk.read_seeds(seeds), ["10.9/mine", "10.1000/aaa1", "10.1000/bbb1"])

            pk.main(["seeds", "--from-pdfs", str(lib), "--out", str(seeds)])  # 再跑一次
            self.assertEqual(pk.read_seeds(seeds), ["10.9/mine", "10.1000/aaa1", "10.1000/bbb1"])
            self.assertIn("# 我的注释", seeds.read_text(encoding="utf-8"))

    def test_missing_folder_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = pk.main(["seeds", "--from-pdfs", str(Path(tmp) / "没有"),
                          "--out", str(Path(tmp) / "s.txt")])
            self.assertEqual(rc, 1)

    def test_folder_without_pdfs_fails_loudly(self):
        with tempfile.TemporaryDirectory() as tmp:
            rc = pk.main(["seeds", "--from-pdfs", tmp, "--out", str(Path(tmp) / "s.txt")])
            self.assertEqual(rc, 1)


class TestConfigRemembersMailto(unittest.TestCase):
    def test_config_saves_mailto_and_discover_uses_it(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "paperkit.json"
            with mock.patch.object(pk, "CONFIG_PATH", cfg):
                self.assertEqual(pk.main(["config", "--mailto", "Someone@Example.COM"]), 0)
                self.assertEqual(json.loads(cfg.read_text(encoding="utf-8"))["mailto"],
                                 "Someone@example.com")   # 域名不分大小写, 用户名保留

                seen = {}
                def fake_client(mailto=None, **kw):
                    seen["mailto"] = mailto
                    return FakeClient()
                seeds = root / "s.txt"
                seeds.write_text("10.1000/seed1\n", encoding="utf-8")
                with mock.patch.object(pk, "Client", fake_client), \
                     mock.patch.dict(pk.os.environ, {}, clear=False):
                    pk.os.environ.pop("PAPERKIT_MAILTO", None)
                    pk.main(["discover", "--seeds", str(seeds), "--out", str(root / "o"), "--no-pdf"])
                self.assertEqual(seen["mailto"], "Someone@example.com")

    def test_explicit_flag_beats_saved_config(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cfg = root / "paperkit.json"
            cfg.write_text(json.dumps({"mailto": "saved@example.com"}), encoding="utf-8")
            with mock.patch.object(pk, "CONFIG_PATH", cfg):
                self.assertEqual(pk.resolve_mailto("flag@example.com"), "flag@example.com")
                self.assertEqual(pk.resolve_mailto(None), "saved@example.com")

    def test_rejects_something_that_is_not_an_email(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.object(pk, "CONFIG_PATH", Path(tmp) / "c.json"):
                self.assertEqual(pk.main(["config", "--mailto", "not-an-email"]), 1)


class TestManySeedsScaleDownCiters(unittest.TestCase):
    def test_citers_per_seed_shrinks_with_many_seeds(self):
        self.assertEqual(pk.citers_budget(None, 5), 150)
        self.assertEqual(pk.citers_budget(None, 20), 75)
        self.assertEqual(pk.citers_budget(None, 200), 30)
        self.assertEqual(pk.citers_budget(40, 200), 40)   # 显式指定的不改


class TestPdfIdentifiersFromRealWriters(unittest.TestCase):
    """用真实 PDF 库 (fpdf2 + pikepdf) 生成的文件暴露出来的问题."""

    def test_xmp_element_with_inline_namespace_declaration(self):
        # pikepdf 等工具写成 <prism:doi xmlns:prism="...">, 标签名后面还有属性
        with tempfile.TemporaryDirectory() as tmp:
            p = make_pdf(Path(tmp) / "x.pdf", streams=[REFS], xmp=(
                b'<rdf:Description rdf:about=""><prism:doi xmlns:prism='
                b'"http://prismstandard.org/namespaces/basic/1.0/">10.1038/s41586-021-03819-2'
                b'</prism:doi></rdf:Description>'))
            got = pk.pdf_identifiers(p)
            self.assertEqual(got["doi"], "10.1038/s41586-021-03819-2")
            self.assertEqual(got["how"], "元数据 DOI")

    def test_single_word_ascii_filename_is_not_a_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            for name in ("xmp-paper.pdf", "main.pdf", "paper_final.pdf", "manuscript.pdf"):
                p = make_pdf(Path(tmp) / name, streams=[REFS])
                self.assertIsNone(pk.pdf_identifiers(p)["title"], name)

    def test_slug_style_filename_becomes_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_pdf(Path(tmp) / "graph-attention-networks.pdf", streams=[REFS])
            self.assertEqual(pk.pdf_identifiers(p)["title"], "graph attention networks")

    def test_cjk_title_without_spaces_is_still_a_title(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = make_pdf(Path(tmp) / "交通流预测方法综述.pdf", streams=[REFS])
            self.assertEqual(pk.pdf_identifiers(p)["title"], "交通流预测方法综述")


class TestShortRealTitles(unittest.TestCase):
    def test_two_word_real_title_is_kept(self):
        # LeCun, Bengio & Hinton 2015 的 Nature 综述就叫 "Deep learning"
        with tempfile.TemporaryDirectory() as tmp:
            p = make_pdf(Path(tmp) / "x.pdf", streams=[REFS], info=b"/Title (Deep learning)")
            self.assertEqual(pk.pdf_identifiers(p)["title"], "Deep learning")


class TestHaveSeeds(unittest.TestCase):
    """种子来自自己已下载的 PDF 时: 不重复下载, 也不写进 RIS (免得 Zotero 里重复)."""

    def run_discover(self, extra):
        from unittest import mock
        downloaded = []
        def fake_download(url, dest, timeout=60):
            downloaded.append(dest.name)
            return False
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds = root / "s.txt"
            seeds.write_text("10.1000/seed1\n", encoding="utf-8")
            CORPUS["W1"]["best_oa_location"]["pdf_url"] = "https://example.org/seed1.pdf"
            try:
                with mock.patch.object(pk, "Client", lambda **kw: FakeClient()), \
                     mock.patch.object(pk, "download_pdf", fake_download):
                    pk.main(["discover", "--seeds", str(seeds), "--out", str(root / "o"), *extra])
            finally:
                CORPUS["W1"]["best_oa_location"]["pdf_url"] = None
            ris = (root / "o" / "S-核心必读.ris").read_text(encoding="utf-8")
        return downloaded, ris

    def test_default_downloads_and_lists_seeds(self):
        downloaded, ris = self.run_discover([])
        self.assertTrue(any("Seed One" in d for d in downloaded))
        self.assertIn("Seed One on Transformers", ris)

    def test_have_seeds_skips_their_pdfs_and_ris_entries(self):
        downloaded, ris = self.run_discover(["--have-seeds"])
        self.assertFalse(any("Seed One" in d for d in downloaded))
        self.assertNotIn("Seed One on Transformers", ris)
        self.assertIn("Foundational Work Everyone Cites", ris)   # 关联论文照常


class TestFirstRealLibraryRun(unittest.TestCase):
    """用户第一次拿自己 7 篇文献跑 seeds 暴露出来的问题."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_bookmark_titles_are_not_the_paper_title(self):
        # Wu_2025 被认成 "（一）服务质量": 那是书签 (outline) 条目, 也用 /Title 键
        heading = "（一）服务质量".encode("utf-16-be").hex().encode()
        p = make_pdf(self.dir / "x.pdf", streams=[REFS], raw=(
            b"7 0 obj\n<< /Title <FEFF" + heading + b"> /Parent 3 0 R /Next 8 0 R "
            b"/Dest [1 0 R /XYZ 0 792 0] >>\nendobj\n"))
        self.assertIsNone(pk.pdf_identifiers(p)["title"])

    def test_info_title_still_found_next_to_bookmarks(self):
        p = make_pdf(self.dir / "x.pdf", streams=[REFS],
                     raw=b"7 0 obj\n<< /Title (Section 1 Introduction here) /Parent 3 0 R >>\nendobj\n",
                     info=b"/Title (Service Quality and Theme Park Satisfaction)")
        self.assertEqual(pk.pdf_identifiers(p)["title"],
                         "Service Quality and Theme Park Satisfaction")

    def test_truncated_doi_fragments_are_rejected(self):
        # Yuan_2024 被认成 "10.1016/j": 参考文献里的 Elsevier DOI 在同一处被排版拆断
        for frag in (b"10.1016/j", b"10.1016/j.", b"10.1016/j.tourman", b"10.3389/fpsyg."):
            self.assertIsNone(pk._clean_doi(frag), frag)
        for ok in (b"10.3390/su10103409", b"10.1155/2022/6120511", b"10.1038/nature14539"):
            self.assertIsNotNone(pk._clean_doi(ok), ok)

    def test_repeated_truncated_fragment_cannot_win(self):
        split_refs = b"".join(b"[(%d. ... doi: 10.1016/j)-30(.tourman.20%02d.1%04d)]TJ\n" % (i, i, i)
                              for i in range(12))
        p = make_pdf(self.dir / "Yuan.pdf", streams=[split_refs])
        got = pk.pdf_identifiers(p)
        self.assertNotEqual(got["doi"], "10.1016/j")

    def test_doi_split_across_tj_pieces_is_rejoined(self):
        # 本篇 DOI 在每页页脚被排版拆成两段, 拼回来才认得出
        footer = b"BT [(https://doi.org/10.3389/fpsyg.)-20(2024.1234567)]TJ ET\n"
        p = make_pdf(self.dir / "f.pdf", streams=[footer] * 6 + [REFS])
        got = pk.pdf_identifiers(p)
        self.assertEqual(got["doi"], "10.3389/fpsyg.2024.1234567")
        self.assertEqual(got["how"], "正文反复出现的 DOI")

    def test_unsplit_doi_inside_tj_is_not_double_counted(self):
        # 同一处 DOI 在原始流和拼接文本里各出现一次, 不能算成"反复出现"
        one = b"BT [(Ref doi:10.1000/single2024)]TJ ET\n"
        p = make_pdf(self.dir / "g.pdf", streams=[one + REFS])
        self.assertNotEqual(pk.pdf_identifiers(p)["how"], "正文反复出现的 DOI")

    def test_personal_naming_convention_is_not_a_title(self):
        # 用户命名: 作者_年份_中文概括_期刊. 中文概括是自己写的, 不是论文标题.
        for name in ("Wu_2025_服务质量提升主题公园满意度_人文社科学刊.pdf",
                     "{Wang_2024}_心理账户视角主题公园重游意愿_PJLSS.pdf",
                     "{Bae等_2018}_游客态度三维度塑造城市形象_Sustainability.pdf"):
            p = make_pdf(self.dir / name, streams=[REFS])
            self.assertIsNone(pk.pdf_identifiers(p)["title"], name)

    def test_unrecognised_file_gets_a_lookup_hint_from_its_name(self):
        hint = pk.filename_hint(Path("{Wang_2024}_心理账户视角主题公园重游意愿_PJLSS.pdf"))
        self.assertEqual(hint, "Wang, 2024, PJLSS")
        hint = pk.filename_hint(Path("{Moisescu等_2021}_户外公园满意度驱动忠诚度_IJERPH.pdf"))
        self.assertEqual(hint, "Moisescu 等, 2021, IJERPH")
        self.assertIsNone(pk.filename_hint(Path("random.pdf")))


class TestTitleSearchNeedsARealMatch(unittest.TestCase):
    def test_unrelated_search_results_are_not_accepted_as_a_seed(self):
        # 以前永远取相似度最高的一条, 哪怕相似度是 0, 随便一篇论文就成了种子.
        self.assertIsNone(pk.resolve_seed(FakeClient(), "心理账户视角主题公园重游意愿"))
        self.assertIsNone(pk.resolve_seed(FakeClient(), "Completely Unrelated Words Here"))

    def test_close_title_still_matches(self):
        got = pk.resolve_seed(FakeClient(), "Seed One on Transformers")
        self.assertEqual(got.oid, "W1")

    def test_cjk_titles_are_compared_by_characters(self):
        a = pk.norm_title("基于图神经网络的交通流预测研究")
        b = pk.norm_title("基于图神经网络的交通流预测")
        self.assertGreater(pk.title_overlap(a, b), 0.8)


class TestVersionFingerprint(unittest.TestCase):
    """用户跑了旧版却看不出来. 指纹由文件内容算出, 不用每次手动改版本号."""

    def test_fingerprint_is_derived_from_file_content(self):
        import hashlib
        want = hashlib.sha256(Path(pk.__file__).read_bytes()).hexdigest()[:8]
        self.assertEqual(pk.script_id(), want)

    def test_version_flag_prints_it(self):
        import io
        from unittest import mock
        out = io.StringIO()
        with mock.patch.object(pk.sys, "stdout", out), self.assertRaises(SystemExit) as cm:
            pk.main(["--version"])
        self.assertEqual(cm.exception.code, 0)
        self.assertIn(pk.script_id(), out.getvalue())

    def test_every_command_announces_its_version(self):
        import io
        from unittest import mock
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp, mock.patch.object(pk.sys, "stderr", buf):
            pk.main(["seeds", "--from-pdfs", tmp, "--out", str(Path(tmp) / "s.txt")])
        self.assertIn(f"paperkit {pk.script_id()}", buf.getvalue().splitlines()[0])


class TestPdfYieldAfterFirstRealRun(unittest.TestCase):
    """真实跑下来 60 篇只下到 4 篇, 统计还把主动跳过的种子算成"没有开放获取"."""

    def test_all_open_access_locations_are_collected(self):
        w = work("W7", "T", 2020, 1)
        w["best_oa_location"] = {"is_oa": True, "pdf_url": None, "source": {}}
        w["locations"] = [
            {"is_oa": False, "pdf_url": "https://paywalled.example/x.pdf"},
            {"is_oa": True, "pdf_url": "https://www.mdpi.com/x/pdf"},
            {"is_oa": True, "pdf_url": "https://europepmc.org/x.pdf"},
            {"is_oa": True, "pdf_url": "https://www.mdpi.com/x/pdf"},   # 重复
        ]
        p = pk.Paper.from_json(w)
        self.assertEqual(p.pdf_urls, ["https://www.mdpi.com/x/pdf", "https://europepmc.org/x.pdf"])

    def test_best_location_is_tried_first(self):
        w = work("W8", "T", 2020, 1, pdf="https://best.example/a.pdf")
        w["locations"] = [{"is_oa": True, "pdf_url": "https://other.example/b.pdf"}]
        self.assertEqual(pk.Paper.from_json(w).pdf_urls,
                         ["https://best.example/a.pdf", "https://other.example/b.pdf"])

    def test_locations_are_requested_from_openalex(self):
        self.assertIn("locations", pk.WORK_FIELDS.split(","))

    def test_pdf_download_looks_like_a_browser(self):
        # MDPI、Frontiers 等会拦截 python-urllib, 返回网页而不是 PDF
        import io
        from unittest import mock
        seen = {}

        class Resp(io.BytesIO):
            def __enter__(self): return self
            def __exit__(self, *a): return False

        def fake_urlopen(req, timeout=None):
            seen.update({k.lower(): v for k, v in req.header_items()})
            return Resp(b"%PDF-1.7 body")

        with tempfile.TemporaryDirectory() as tmp, \
             mock.patch.object(pk.urllib.request, "urlopen", fake_urlopen):
            self.assertTrue(pk.download_pdf("https://www.mdpi.com/x/pdf", Path(tmp) / "a.pdf"))
        self.assertIn("Mozilla/5.0", seen["user-agent"])
        self.assertIn("application/pdf", seen["accept"])

    def run_discover(self, fake_download, extra=()):
        import io
        from unittest import mock
        buf = io.StringIO()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            seeds = root / "s.txt"
            seeds.write_text("10.1000/seed1\n10.1000/seed2\n", encoding="utf-8")
            CORPUS["W1"]["best_oa_location"]["pdf_url"] = "https://example.org/seed1.pdf"
            CORPUS["W201"]["locations"] = [{"is_oa": True, "pdf_url": "https://mirror.example/w201.pdf"}]
            CORPUS["W201"]["best_oa_location"]["pdf_url"] = "https://blocked.example/w201.pdf"
            try:
                with mock.patch.object(pk, "Client", lambda **kw: FakeClient()), \
                     mock.patch.object(pk, "download_pdf", fake_download), \
                     mock.patch.object(pk.sys, "stderr", buf):
                    pk.main(["discover", "--seeds", str(seeds), "--out", str(root / "o"), *extra])
            finally:
                CORPUS["W1"]["best_oa_location"]["pdf_url"] = None
                CORPUS["W201"].pop("locations", None)
                CORPUS["W201"]["best_oa_location"]["pdf_url"] = None
        return buf.getvalue()

    def test_falls_back_to_the_next_location_when_one_is_blocked(self):
        tried = []
        def dl(url, dest, timeout=60):
            tried.append(url)
            return "blocked" not in url
        self.run_discover(dl)
        self.assertEqual([u for u in tried if "w201" in u],
                         ["https://blocked.example/w201.pdf", "https://mirror.example/w201.pdf"])

    def test_summary_separates_skipped_seeds_failed_downloads_and_closed_papers(self):
        def dl(url, dest, timeout=60):
            return "mirror" in url           # 只有 W201 的镜像能下
        out = self.run_discover(dl, extra=["--have-seeds"])
        line = next(l for l in out.splitlines() if "拿到" in l)
        self.assertIn("拿到 1 篇", line)
        self.assertIn("下载失败", line)       # W100/W200 有链接但下不来
        self.assertIn("没有开放获取", line)
        self.assertIn("跳过 2 篇种子", line)  # --have-seeds 主动跳过, 不能算成"没有开放获取"
        self.assertIn("查找可用的 PDF", out)  # 告诉用户 Zotero 能接着抓


class TestRerunNoteMessage(unittest.TestCase):
    def test_rerun_says_notes_were_kept_not_that_zero_were_written(self):
        import io
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "v"; vault.mkdir()
            seeds = root / "s.txt"; seeds.write_text("10.1000/seed1\n", encoding="utf-8")
            argv = ["discover", "--seeds", str(seeds), "--out", str(root / "o"),
                    "--vault", str(vault), "--no-pdf"]
            with mock.patch.object(pk, "Client", lambda **kw: FakeClient()):
                pk.main(argv)
                buf = io.StringIO()
                with mock.patch.object(pk.sys, "stderr", buf):
                    pk.main(argv)
        line = next(l for l in buf.getvalue().splitlines() if "文献笔记" in l)
        self.assertNotIn("写入 0 篇", line)
        self.assertIn("已存在", line)


# --------------------------------------------------------------------------
# 用户第一份真实推荐列表暴露出来的排序问题
# --------------------------------------------------------------------------

# 用户 7 篇种子的标题 (主题地图里显示的样子)
REAL_SEED_TITLES = [
    "The Impact of Consumers’ Attitudes toward a Theme Park: A Focus on Dis",
    "Study on Tourism Consumer Behavior and Countermeasures Based on Big Da",
    "Exploring the Relationship Between Hedonism, Tourist Experience, and R",
    "Exploring the Drivers of Visitor Loyalty in the Context of Outdoor Adv",
    "What keeps historical theme park visitors coming? Research based on ex",
    "中小型主题公园的服务质量与品牌资产、游客满意度、目的地形象间的影响关系研究",
    "A Study on the Factors Influencing Theme Park Visitors' Revisit Intent",
]

# 真实结果里的统计方法文献, 必须归进 M-研究方法
REAL_METHOD_TITLES = [
    "Assessing measurement model quality in PLS-SEM using confirmatory composite analysis",
    "Predictive model assessment in PLS-SEM: guidelines for using PLSpredict",
    "Principles and Practice of Structural Equation Modeling",
    "Comparative fit indexes in structural models",
    "An assessment of the use of partial least squares structural equation modeling in marketing research",
    "Structural model robustness checks in PLS-SEM",
    "Partial Least Squares Structural Equation Modeling",
    "Multivariate Data Analysis",
    "Structural Equation Models with Unobservable Variables and Measurement Error: Algebra and Statistics",
    "An Index of Factorial Simplicity",
    "Marketing Research: An Applied Orientation",
    "Identifying and treating unobserved heterogeneity with FIMIX-PLS",
    "Gain more insight from your PLS-SEM results: The importance-performance map analysis",
    "The elephant in the room: Predictive performance of PLS models",
]

# 真实结果里的主题论文, 一篇都不能被误判成方法文献
REAL_TOPICAL_TITLES = [
    "Theme parks and a structural equation model of determinants of visitor satisfaction",
    "How destination image and evaluative factors affect behavioral intentions?",
    "Exploring the experiential and sociodemographic drivers of satisfaction and loyalty in the theme park context",
    "The theme park experience: An analysis of pleasure, arousal and satisfaction",
    "Servicescape elements, customer predispositions and service experience",
    "Experiential Marketing",
    "Determining the Factors Affecting the Memorable Nature of Travel Experiences",
    "The development of measurement scale for entertainment tourism experience",
    "Development of a Scale to Measure Memorable Tourism Experiences",
    "Work and/or Fun: Measuring Hedonic and Utilitarian Shopping Value",
    "Customer Satisfaction, Market Share, and Profitability: Findings from Sweden",
    "A Cognitive Model of the Antecedents and Consequences of Satisfaction Decisions",
    "Antecedents of revisit intention",
    "Tourists’ Experiences with Smart Tourism Technology at Smart Destinations",
    "Observations: SAM: The Self-Assessment Manikin",
    "Study on the Marketing Modes of Theme Parks in China",
]


class TestMethodPapersGetTheirOwnTier(unittest.TestCase):
    def setUp(self):
        self.vocab = pk.seed_vocabulary(REAL_SEED_TITLES)

    def test_every_real_method_paper_is_classified_as_method(self):
        for t in REAL_METHOD_TITLES:
            self.assertTrue(pk.is_method_paper(t, self.vocab), t)

    def test_no_real_topical_paper_is_misclassified(self):
        for t in REAL_TOPICAL_TITLES:
            self.assertFalse(pk.is_method_paper(t, self.vocab), t)

    def test_generic_words_in_seed_titles_do_not_make_methods_topical(self):
        # 种子标题里的 "Big Data" "Study" "Research" 是泛用词, 不能让
        # "Multivariate Data Analysis" 因为共享 data 就被当成主题论文
        for w in ("data", "study", "research", "based", "factor", "impact"):
            self.assertNotIn(w, self.vocab)
        for w in ("theme", "park", "visitor", "loyalty", "tourism"):
            self.assertIn(w, self.vocab)

    def test_chinese_method_title_with_seed_topic_stays_topical(self):
        self.assertFalse(pk.is_method_paper("主题公园游客满意度的结构方程模型研究", self.vocab))
        self.assertTrue(pk.is_method_paper("结构方程模型的原理与应用", self.vocab))


def scored(**kw):
    p = pk.Paper.from_json(work(kw.pop("oid", "W9"), kw.pop("title", "Topic paper words"),
                                kw.pop("year", 2015), kw.pop("cited", 10),
                                refs=kw.pop("refs", ())))
    p.prov = kw.pop("prov", {})
    p.seed_links = set(kw.pop("links", ()))
    p.cite_links = set(kw.pop("cite_links", ()))
    return p


class TestScoringFixesFromRealList(unittest.TestCase):
    def seeds(self):
        s1 = pk.Paper.from_json(work("S1", "Seed", 2020, 1, refs=[f"R{i}" for i in range(20)]))
        s2 = pk.Paper.from_json(work("S2", "Seed", 2021, 1, refs=[f"R{i}" for i in range(10, 30)]))
        return [s1, s2]

    def test_related_only_links_do_not_earn_the_multi_seed_bonus(self):
        # 被引 0 次的中文会议论文只是 OpenAlex 的"近邻", 却吃到了"同时关联 3 篇种子"的加分
        near = scored(oid="W1", cited=0, year=2009, prov={"rel": 3}, links={"S1", "S2", "S3"})
        cited = scored(oid="W2", cited=40, year=2012, prov={"back": 1}, links={"S1"},
                       cite_links={"S1"}, refs=["R1", "R2", "R3"])
        pk.score_all({"W1": near, "W2": cited}, self.seeds(), this_year=2026)
        self.assertGreater(cited.score, near.score)
        self.assertFalse(any("同时关联" in r for r in near.reasons))

    def test_fame_cannot_outweigh_topical_relevance(self):
        # Bentler 1990 (被引 24156) 只被 1 篇种子引用, 不能压过同样被 1 篇引用、
        # 且和种子共享大量参考文献的主题论文
        famous = scored(oid="W1", cited=24156, year=1990, prov={"back": 1},
                        links={"S1"}, cite_links={"S1"})
        topical = scored(oid="W2", cited=462, year=2013, prov={"back": 1}, links={"S1"},
                         cite_links={"S1"}, refs=[f"R{i}" for i in range(12)] + ["X1", "X2"])
        pk.score_all({"W1": famous, "W2": topical}, self.seeds(), this_year=2026)
        self.assertGreater(topical.score, famous.score)

    def test_multi_seed_citation_bonus_still_applies(self):
        both = scored(oid="W1", prov={"back": 2}, links={"S1", "S2"}, cite_links={"S1", "S2"})
        one = scored(oid="W2", prov={"back": 1}, links={"S1"}, cite_links={"S1"})
        pk.score_all({"W1": both, "W2": one}, self.seeds(), this_year=2026)
        self.assertGreater(both.score, one.score)
        self.assertTrue(any("同时关联 2 篇种子" in r for r in both.reasons))


class TestDiscoverWithMethodTier(unittest.TestCase):
    def setUp(self):
        CORPUS["W500"] = work("W500", "Partial Least Squares Structural Equation Modeling",
                              2017, 3000, refs=["W900"])
        CORPUS["W1"]["referenced_works"].append("https://openalex.org/W500")

    def tearDown(self):
        CORPUS.pop("W500")
        CORPUS["W1"]["referenced_works"].remove("https://openalex.org/W500")

    def run_discover(self, root, vault=None, extra=()):
        from unittest import mock
        seeds = root / "s.txt"
        seeds.write_text("10.1000/seed1\n10.1000/seed2\n", encoding="utf-8")
        argv = ["discover", "--seeds", str(seeds), "--out", str(root / "o"), *extra]
        if vault:
            argv += ["--vault", str(vault)]
        with mock.patch.object(pk, "Client", lambda **kw: FakeClient()):
            return pk.main(argv)

    def test_method_paper_lands_in_M_and_not_in_topical_tiers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_discover(root, extra=["--no-pdf"])
            result = json.loads((root / "o" / "paperkit-result.json").read_text(encoding="utf-8"))
            tiers = {r["oid"]: r["tier"] for r in result}
            self.assertEqual(tiers["W500"], "M")
            self.assertIn("Partial Least Squares",
                          (root / "o" / "M-研究方法.ris").read_text(encoding="utf-8"))
            for name in ("S-核心必读", "A-强相关", "B-背景扩展"):
                f = root / "o" / f"{name}.ris"
                if f.exists():
                    self.assertNotIn("Partial Least Squares", f.read_text(encoding="utf-8"))

    def test_method_papers_do_not_consume_topical_slots(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.run_discover(root, extra=["--no-pdf", "--max", "3", "--top-s", "1", "--top-a", "1"])
            result = json.loads((root / "o" / "paperkit-result.json").read_text(encoding="utf-8"))
            topical = [r for r in result if not r["seed"] and r["tier"] in "SAB"]
            self.assertEqual(len(topical), 3)
            self.assertTrue(any(r["tier"] == "M" for r in result))

    def test_rerun_moves_notes_to_their_new_tier_and_keeps_user_text(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            vault = root / "v"; vault.mkdir()
            self.run_discover(root, vault=vault, extra=["--no-pdf"])
            notes = vault / "10-文献笔记"
            note = next(notes.rglob("Hopper*")) if list(notes.rglob("Hopper*")) else None
            # 找 W500 的笔记, 模拟"上次它在 A 级、用户还写了东西"
            m_note = next(p for p in notes.rglob("*.md") if "Partial Least Squares" in p.read_text(encoding="utf-8"))
            text = m_note.read_text(encoding="utf-8").replace("tier: M", "tier: A")
            text += "\n我读的时候写的笔记\n"
            old_home = notes / "A-强相关" / m_note.name
            old_home.parent.mkdir(parents=True, exist_ok=True)
            old_home.write_text(text, encoding="utf-8")
            m_note.unlink()

            self.run_discover(root, vault=vault, extra=["--no-pdf"])
            self.assertFalse(old_home.exists())
            moved = notes / "M-研究方法" / m_note.name
            body = moved.read_text(encoding="utf-8")
            self.assertIn("我读的时候写的笔记", body)
            self.assertIn("tier: M", body)
            self.assertNotIn("tier: A", body)

    def test_rerun_moves_downloaded_pdf_instead_of_downloading_again(self):
        from unittest import mock
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            CORPUS["W500"]["best_oa_location"] = {"is_oa": True, "pdf_url": "https://x/pls.pdf", "source": {}}
            slug = pk.Paper.from_json(CORPUS["W500"]).slug()
            stale = root / "o" / "A-强相关" / f"{slug}.pdf"
            stale.parent.mkdir(parents=True)
            stale.write_bytes(b"%PDF-1.4 old download")
            calls = []
            with mock.patch.object(pk, "download_pdf", lambda *a, **k: calls.append(a) or False):
                self.run_discover(root)
            self.assertFalse(stale.exists())
            self.assertEqual((root / "o" / "M-研究方法" / f"{slug}.pdf").read_bytes(), b"%PDF-1.4 old download")
            self.assertFalse([c for c in calls if "pls.pdf" in c[0]])


class TestPruneOrphanNotes(unittest.TestCase):
    """旧笔记不在新结果里时还带着 tier: S, 阅读面板会继续把它列成"没读的核心论文"."""

    def run(self, result=None):
        return super().run(result)

    def make_orphan(self, vault):
        f = vault / "10-文献笔记" / "S-核心必读" / "Old2009 - Dropped Paper.md"
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("---\ntier: S\nscore: 9.5\ntags: [论文, S/核心必读]\nstatus: 未读\n---\n\n"
                     "> 分级: **S-核心必读**\n\n我写过的一句话\n", encoding="utf-8")
        return f

    def discover(self, root, vault, extra=()):
        from unittest import mock
        seeds = root / "s.txt"
        seeds.write_text("10.1000/seed1\n", encoding="utf-8")
        with mock.patch.object(pk, "Client", lambda **kw: FakeClient()):
            pk.main(["discover", "--seeds", str(seeds), "--out", str(root / "o"),
                     "--vault", str(vault), "--no-pdf", *extra])

    def test_without_prune_orphans_stay_put(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "v"; vault.mkdir()
            f = self.make_orphan(vault)
            self.discover(root, vault)
            self.assertTrue(f.exists())

    def test_prune_moves_orphans_out_of_the_dashboard_but_keeps_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "v"; vault.mkdir()
            f = self.make_orphan(vault)
            self.discover(root, vault, extra=["--prune"])
            self.assertFalse(f.exists())
            moved = vault / "10-文献笔记" / pk.RETIRED_DIR / f.name
            text = moved.read_text(encoding="utf-8")
            self.assertIn("我写过的一句话", text)
            self.assertIn(f"tier: {pk.RETIRED_TIER}", text)
            self.assertNotIn("tier: S\n", text)
            self.assertNotIn("分级: **S-核心必读**", text)

    def test_prune_never_touches_current_results(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "v"; vault.mkdir()
            self.discover(root, vault)
            before = sorted(p.name for p in (vault / "10-文献笔记").rglob("*.md"))
            self.discover(root, vault, extra=["--prune"])
            after = sorted(p.name for p in (vault / "10-文献笔记").rglob("*.md"))
            self.assertEqual(before, after)
            self.assertFalse((vault / "10-文献笔记" / pk.RETIRED_DIR).exists())

    def test_retired_notes_are_not_rescanned_as_tier_notes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); vault = root / "v"; vault.mkdir()
            self.make_orphan(vault)
            self.discover(root, vault, extra=["--prune"])
            self.discover(root, vault, extra=["--prune"])   # 第二次不该再动它
            self.assertEqual(len(list((vault / "10-文献笔记" / pk.RETIRED_DIR).glob("*.md"))), 1)


# 用户第一份真实推荐列表的标题 (主题地图里被截短的样子): 分级|键|标题
REAL_MAP_TITLES = """
S|Bae2018|The Impact of Consumers’ Attitudes toward a Theme Park A
S|Li2022|Study on Tourism Consumer Behavior and Countermeasures
S|Luo2021|Exploring the Relationship Between Hedonism, Tourist
S|Moisescu2021|Exploring the Drivers of Visitor Loyalty in the Context of
S|Yuan2024|What keeps historical theme park visitors coming Research
S|武扬2025|中小型主题公园的服务质量与品牌资产、游客满意度、目的地形象间的影响关系研究
S|Yiguo2024|A Study on the Factors Influencing Theme Park Visitors'
S|Milman2017|Exploring the experiential and sociodemographic drivers of
S|Ali2016|Make it delightful Customers' experience, satisfaction and
S|Dong2012|Servicescape elements, customer predispositions and service
S|Alcañiz2004|The theme park experience An analysis of pleasure, arousal
S|Chen2006|How destination image and evaluative factors affect
S|Caber2016|Push or pull Identifying rock climbing tourists' motivations
S|Goossens2000|Tourism information and pleasure motivation
S|Kao2008|Effects of Theatrical Elements on Experiential Quality and
S|Huang2009|Effects of Travel Motivation, Past Experience, Perceived
S|Kim2010|Determining the Factors Affecting the Memorable Nature of
S|Cheng2015|Visitors’ brand loyalty to a historical and cultural theme
S|Ryan2010|Theme parks and a structural equation model of determinants
A|Schmitt1999|Experiential Marketing
A|Geissler2011|The overall theme park experience A visitor satisfaction
A|Milman2012|Examining the guest experience in themed amusement parks
A|Liang2024|The Role of Single Landscape Elements in Enhancing
A|Jin2013|The Effect of Experience Quality on Perceived Value,
A|Luo2018|The development of measurement scale for entertainment
A|Akel2022|Prioritization of the Theme Park Satisfaction Criteria with
A|Torres2017|Delighted or outraged Uncovering key drivers of exceedingly
A|Wu2014|A Study of Experiential Quality, Experiential Value,
A|Morris1995|Observations SAM The Self-Assessment Manikin An Efficient
A|Zhu2022|Rethinking the Impact of Theme Park Image on Perceived
A|Ryu2010|Relationships among hedonic and utilitarian values,
A|Chen2009|Experience quality, perceived value, satisfaction and
A|Lucarelli2011|City branding a state‐of‐the‐art review of the research
A|Kaplan2010|Branding places applying brand personality concept to cities
A|Zhang2017|A model of perceived image, memorable tourism experiences
A|Boo2018|Tourists’ hotel event experience and satisfaction an
A|Calver2013|Enlightened hedonism Exploring the relationship of service
A|Chang2014|Creative tourism a preliminary examination of creative
A|Wang2012|Tourist experience and Wetland parks A case of Zhejiang,
B|Wang2019|Antecedents and Consequences of Brand Experiences in a
B|Ma2016|Delighted or Satisfied Positive Emotional Responses Derived
B|Asmelash2019|The structural relationship between tourist satisfaction
B|Yim2013|Hedonic shopping motivation and co-shopper influence on
B|Lee2010|The impact of tour quality and tourist satisfaction on
B|Tian-Cole2003|A conceptualization of the relationships between service
B|Grappi2010|The role of social identification and hedonism in affecting
B|Um2006|Antecedents of revisit intention
B|Back2003|A Brand Loyalty Model Involving Cognitive, Affective, and
B|Başarangil2016|The relationships between the factors affecting perceived
B|Suhartanto2019|Tourist loyalty in creative tourism the role of experience
B|Alcañiz2008|The impact of experiential consumption cognitions and
B|Stylidis2018|Characteristics of destination image visitors and
B|Hanna2008|An analysis of terminology use in place branding
B|Moilanen2015|Challenges of city branding A comparative study of 10
B|Hultman2016|Demand- and supply-side perspectives of city branding A
B|Kim2003|The influence of push and pull factors at Korean national
B|Wang2015|Toward an integrated model of tourist expectation formation
B|Zhang2021|Chinese cultural theme parks text mining and sentiment
B|Liu2015|The role of travel experience in the structural
B|Hsu2008|The preference analysis for tourist choice of destination A
B|Fan2005|Branding the nation What is being branded
B|Kirillova2015|Destination Aesthetics and Aesthetic Distance in Tourism
B|Kim2010b|Development of a Scale to Measure Memorable Tourism
B|Rajesh2013|Impact of Tourist Perceptions, Destination Image and
B|Kruger2010|Travel Motivation of Tourists to Kruger and Tsitsikamma
B|Guido2014|An Italian version of the 10-item Big Five Inventory An
B|Manojlović2025|Effects of Cultural Tourism Experience on Tourist Behavior
"""

THEMES_FILE = Path(__file__).parent / "examples" / "themes-theme-park.txt"


def real_rows():
    return [line.split("|") for line in REAL_MAP_TITLES.strip().splitlines()]


class TestThemeRules(unittest.TestCase):
    def test_parses_names_keywords_and_skips_comments(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "t.txt"
            f.write_text("# 注释\n\n品牌: branding, brand\n动机：motivation、push and pull\n"
                         "没有冒号的行\n", encoding="utf-8")
            self.assertEqual(pk.load_themes(f), [("品牌", ["branding", "brand"]),
                                                 ("动机", ["motivation", "push and pull"])])

    def test_first_matching_theme_wins(self):
        themes = [("主题公园", ["theme park"]), ("满意度", ["satisfaction"])]
        self.assertEqual(pk.theme_of("Theme parks and visitor satisfaction", "", themes), "主题公园")

    def test_title_beats_abstract(self):
        themes = [("主题公园", ["theme park"]), ("满意度", ["satisfaction"])]
        self.assertEqual(pk.theme_of("Customer satisfaction", "... at a theme park ...", themes), "满意度")

    def test_abstract_is_used_when_title_misses(self):
        themes = [("主题公园", ["theme park"])]
        self.assertEqual(pk.theme_of("Observations", "we surveyed theme park guests", themes), "主题公园")
        self.assertIsNone(pk.theme_of("Observations", "", themes))

    def test_keywords_match_word_starts_not_inside_words(self):
        themes = [("品牌", ["brand"]), ("公园", ["park"])]
        self.assertEqual(pk.theme_of("City branding review", "", themes), "品牌")
        self.assertIsNone(pk.theme_of("Sparkling wine", "", themes))

    def test_chinese_keywords_match_literally(self):
        themes = [("主题公园", ["主题公园"])]
        self.assertEqual(pk.theme_of("中小型主题公园的服务质量研究", "", themes), "主题公园")


class TestCuratedThemesOnRealTitles(unittest.TestCase):
    """用我手工分过的真实列表校验示例分节规则."""

    def setUp(self):
        self.themes = pk.load_themes(THEMES_FILE)
        self.got = {key: pk.theme_of(title, "", self.themes) for _, key, title in real_rows()}

    def test_expected_sections(self):
        expect = {
            "Alcañiz2004": "主题公园与娱乐体验", "Ryan2010": "主题公园与娱乐体验",
            "Geissler2011": "主题公园与娱乐体验", "Luo2018": "主题公园与娱乐体验",
            "武扬2025": "主题公园与娱乐体验",
            "Lucarelli2011": "城市与地方品牌", "Fan2005": "城市与地方品牌",
            "Goossens2000": "旅游动机与目的地选择", "Kim2003": "旅游动机与目的地选择",
            "Hsu2008": "旅游动机与目的地选择",
            "Chen2006": "难忘体验与目的地形象", "Kim2010": "难忘体验与目的地形象",
            "Ali2016": "愉悦、惊喜与享乐", "Torres2017": "愉悦、惊喜与享乐",
            "Dong2012": "服务场景与体验质量", "Jin2013": "服务场景与体验质量",
            "Chang2014": "文化与创意旅游", "Um2006": "满意、忠诚与重游意愿",
            "Schmitt1999": "消费行为与体验营销", "Li2022": "消费行为与体验营销",
        }
        for key, theme in expect.items():
            self.assertEqual(self.got[key], theme, key)

    def test_korean_national_parks_is_not_branding(self):
        # "nation" 作关键词会误中 "national"; 示例规则里不能有它
        self.assertNotEqual(self.got["Kim2003"], "城市与地方品牌")

    def test_few_titles_are_left_unsorted(self):
        # 地图里的标题被截短了, 实际运行用完整标题, 命中只会更多
        unsorted = [k for k, v in self.got.items() if v is None]
        self.assertLessEqual(len(unsorted), 7, unsorted)


class TestDraftThemes(unittest.TestCase):
    def test_draft_from_real_titles_finds_the_obvious_topics(self):
        titles = [t for _, _, t in real_rows()]
        names = [n for n, _ in pk.draft_themes(titles)]
        for want in ("theme park", "destination image", "city branding"):
            self.assertIn(want, names)
        self.assertLessEqual(len(names), 8)

    def test_broad_single_words_go_below_specific_phrases(self):
        titles = [t for _, _, t in real_rows()]
        names = [n for n, _ in pk.draft_themes(titles)]
        if "satisfaction" in names:
            self.assertGreater(names.index("satisfaction"), names.index("theme park"))


class TestOutline(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "v"
        self.vault.mkdir()
        self.themes = self.root / "themes.txt"
        self.themes.write_text("种子: seed\n基石: foundational\n", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def discover(self, extra=()):
        from unittest import mock
        seeds = self.root / "s.txt"
        seeds.write_text("10.1000/seed1\n10.1000/seed2\n", encoding="utf-8")
        with mock.patch.object(pk, "Client", lambda **kw: FakeClient()):
            return pk.main(["discover", "--seeds", str(seeds), "--out", str(self.root / "o"),
                            "--vault", str(self.vault), "--no-pdf", "--themes", str(self.themes), *extra])

    def outline_text(self):
        return (self.vault / "30-论文地图" / f"{pk.OUTLINE_NOTE}.md").read_text(encoding="utf-8")

    def test_discover_writes_outline_with_a_live_table_per_theme(self):
        self.discover()
        text = self.outline_text()
        for name in ("种子", "基石", pk.UNSORTED_THEME):
            self.assertIn(f"\n## {name}\n", text)
            self.assertIn(f'WHERE theme = "{name}"', text)
        self.assertIn("```dataview", text)
        self.assertIn(str(self.themes), text)          # 告诉用户规则文件在哪

    def test_notes_get_a_theme_field_and_body_is_untouched(self):
        self.discover()
        note = next(p for p in (self.vault / "10-文献笔记").rglob("*.md")
                    if "Foundational Work" in p.read_text(encoding="utf-8"))
        text = note.read_text(encoding="utf-8")
        head = text.split("---")[1]
        self.assertIn('theme: "基石"', head)
        body_before = text.split("---", 2)[2]
        note.write_text(text + "\n我的读书笔记\n", encoding="utf-8")
        self.themes.write_text("基石改名: foundational\n", encoding="utf-8")
        pk.main(["outline", "--vault", str(self.vault), "--themes", str(self.themes)])
        text2 = note.read_text(encoding="utf-8")
        self.assertIn('theme: "基石改名"', text2)
        self.assertEqual(text2.count("theme:"), 1)
        self.assertIn("我的读书笔记", text2)
        self.assertIn(body_before.strip()[:200], text2)

    def test_method_papers_go_to_the_methods_section(self):
        CORPUS["W500"] = work("W500", "Partial Least Squares Structural Equation Modeling", 2017, 3000)
        CORPUS["W1"]["referenced_works"].append("https://openalex.org/W500")
        try:
            self.discover()
        finally:
            CORPUS.pop("W500")
            CORPUS["W1"]["referenced_works"].remove("https://openalex.org/W500")
        note = next((self.vault / "10-文献笔记" / "M-研究方法").glob("*.md"))
        self.assertIn(f'theme: "{pk.METHOD_THEME}"', note.read_text(encoding="utf-8"))
        self.assertIn(f"\n## {pk.METHOD_THEME}\n", self.outline_text())

    def test_retired_notes_are_excluded(self):
        self.discover()
        self.assertIn('tier != "不再推荐"', self.outline_text())

    def test_draft_is_created_once_and_never_overwritten(self):
        self.discover()
        draft = self.vault / "30-论文地图" / f"{pk.DRAFT_NOTE}.md"
        text = draft.read_text(encoding="utf-8")
        self.assertIn("## 一、种子", text)
        self.assertIn(f"[[{pk.OUTLINE_NOTE}#种子]]", text)
        draft.write_text(text + "\n我写了一段综述\n", encoding="utf-8")
        self.discover()
        self.assertIn("我写了一段综述", draft.read_text(encoding="utf-8"))

    def test_outline_command_works_offline(self):
        self.discover()
        from unittest import mock
        with mock.patch.object(pk, "Client", mock.Mock(side_effect=AssertionError("不该联网"))), \
             mock.patch.object(pk, "http_get", mock.Mock(side_effect=AssertionError("不该联网"))):
            rc = pk.main(["outline", "--vault", str(self.vault), "--themes", str(self.themes)])
        self.assertEqual(rc, 0)

    def test_missing_theme_rules_are_drafted_and_saved(self):
        self.themes.unlink()
        self.discover()
        self.assertTrue(self.themes.exists())
        self.assertTrue(pk.load_themes(self.themes))
        self.assertIn("\n## ", self.outline_text())

    def test_outline_without_notes_fails_clearly(self):
        empty = self.root / "empty"; empty.mkdir()
        self.assertEqual(pk.main(["outline", "--vault", str(empty), "--themes", str(self.themes)]), 1)


class TestUserEditedFilesInGbk(unittest.TestCase):
    """用户会用记事本改 themes.txt 和 seeds.txt; 老版记事本存成 GBK, 不能一读就崩."""

    def test_gbk_theme_rules_are_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "themes.txt"
            f.write_bytes("主题公园: theme park, 主题公园\n".encode("gbk"))
            self.assertEqual(pk.load_themes(f), [("主题公园", ["theme park", "主题公园"])])

    def test_gbk_seed_file_is_readable(self):
        with tempfile.TemporaryDirectory() as tmp:
            f = Path(tmp) / "seeds.txt"
            f.write_bytes("# 我的种子\n10.1000/abc1  # 张三 2024\n".encode("gbk"))
            self.assertEqual(pk.read_seeds(f), ["10.1000/abc1"])
