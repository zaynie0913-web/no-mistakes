"""paperkit 离线测试.

容器里连不上 OpenAlex, 所以用一个按官方文档字段结构伪造的假 API 把整条
discover 流水线跑通: 打分/分级/RIS/笔记/地图 全部走真代码路径.
"""

import json
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
