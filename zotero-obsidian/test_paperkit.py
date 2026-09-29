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


# Zotero 7 阅读器的默认标注色
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
