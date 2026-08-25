# paperkit — Zotero + Obsidian 论文流水线

从几篇种子论文出发，自动找出强关联文献、按相关度分三级、下载开放获取 PDF、
生成可直接导入 Zotero 的分级 RIS，并在 Obsidian 里铺好笔记结构和模板。

单文件、纯标准库、零依赖。Python 3.9+ 即可，macOS / Windows / Linux 通用。

## 它解决什么

手动读文献的三个卡点：找关联论文靠运气、下下来堆在一个文件夹里分不清主次、
读的时候划了线但笔记散在 PDF 里回不到笔记库。这个工具把这三段接起来。

## 一次性配置

### 1. Zotero 侧

1. Zotero 7（已装则跳过）
2. 装 **Better BibTeX**：下载 `.xpi` → Zotero → 工具 → 附加组件 → 齿轮 →
   Install Add-on From File
3. Better BibTeX 设置 → Automatic Export → 把你的库自动导出成 `.bib`，
   **导出目标放进 Obsidian 库根目录**（Obsidian 侧要读它）

### 2. Obsidian 侧

```bash
python3 paperkit.py setup --vault ~/Documents/Obsidian/Research
```

会建好目录、写入模板和阅读面板，并打印插件清单。然后在 Obsidian 里装：

| 插件 | 用途 | 必需 |
|---|---|---|
| Zotero Integration | 把 Zotero 里的彩色标注拉进笔记 | 是 |
| Dataview | 渲染阅读面板 | 是 |
| Templater | 新建永久笔记时自动套模板 | 否 |

Zotero Integration 设置 → Import Formats → 新建一条：

- **Output Path**：`10-文献笔记/{{citekey}}.md`
- **Template File**：`90-模板/literature-note.md`

### 3. 体检

```bash
python3 paperkit.py doctor --vault ~/Documents/Obsidian/Research
```

每一项都打勾才算真的接上了。

## 日常用法

把想读的论文写进 `seeds.txt`（格式见 `seeds.example.txt`），然后：

```bash
python3 paperkit.py discover \
  --seeds seeds.txt \
  --out ~/Downloads/papers \
  --vault ~/Documents/Obsidian/Research \
  --mailto 你的邮箱@example.com
```

产出：

```
~/Downloads/papers/
  S-核心必读/*.pdf      S-核心必读.ris
  A-强相关/*.pdf        A-强相关.ris
  B-背景扩展/*.pdf      B-背景扩展.ris
  paperkit-result.json          # 每篇的分数和入选理由
```

最后一步手动：Zotero → 文件 → 导入 → 选中某个 `.ris` →
勾选「将导入的分类和条目放入新分类」。文件名就是分类名，三级目录在 Zotero 里
自动成型。分级和入选理由也写进了每条的标签与备注。

`--mailto` 强烈建议填：进 OpenAlex 礼貌池后限速从 1 req/s 放宽到 10 req/s。

## 分级是怎么算的

候选集从三个方向采集：种子的**参考文献**（领域基石）、**引用了种子**的文献
（最新进展）、OpenAlex 的 `related_works`（近邻）。然后加权打分：

| 信号 | 权重 | 含义 |
|---|---|---|
| 被种子引用 | 3.0 | 你选的论文都在引它，多半是绕不开的基石 |
| 引用了种子 | 2.5 | 直接的后续工作 |
| 同时关联多篇种子 | 2.5 ×(n−1) | **最强信号**：命中你关注的交集而非某一篇的邻居 |
| 文献耦合 | 2.0 | 与种子共享参考文献，说明在同一个问题域 |
| OpenAlex 近邻 | 1.5 | 弱信号，兜底 |
| 影响力 | 0.8 × log(年均被引) | 取年均值并对数压缩，老论文不靠年头碾压新工作 |

两个刻意的修正：近三年的论文 +0.6（还没来得及攒引用）；社论/勘误这类
非研究条目 ×0.75（往下压但不排除）。

文献耦合用 `√(自身参考文献数)` 归一化——否则一篇 300 条参考文献的综述
会纯靠体量霸榜。这条有回归测试盯着
（`test_coupling_normalisation_does_not_reward_bulk_refs`）。

篇数用 `--top-s` / `--top-a` / `--max` 调，默认 12 / 20 / 60。

## 边看边写

在 Zotero 的 PDF 阅读器里用**颜色**划重点，模板会按颜色自动归位：

| 颜色 | 归到 |
|---|---|
| 🟡 黄 | 关键结论 |
| 🔴 红 | 存疑与反对 |
| 🟢 绿 | 可复用的方法 |
| 🔵 蓝 | 待深挖 |

读完在 Obsidian 里执行 `Zotero Integration: Import notes` 拉取，划的线就落到
对应小节。笔记 frontmatter 里的 `status` 手动改 `未读 → 在读 → 已读`，
`00-面板/阅读面板.md` 会自动跟着变。

## 已知边界

- **只能下开放获取的 PDF。** 闭源论文只会有元数据条目，正文得靠机构订阅在
  Zotero 里另行抓取。工具会校验响应确实是 PDF，不会把登录页存成 `.pdf`。
- **重跑不会覆盖你改过的笔记**，默认跳过已存在的文件。要重建加 `--force`。
- **Obsidian 的 Zotero Integration 插件已停更**——最后一版 3.2.1 停在 2024-08，
  仓库已迁到 `community-archive/`。目前仍能正常工作，但它是这条链路上唯一没人
  维护的一环，心里有数。备选是走 Zotero 7 Local API 的 Zotero Bridge。
- RIS 里带了 `L1` 本地 PDF 路径，Zotero 导入时**可能**自动挂上附件，也可能不挂；
  不挂也没关系，PDF 本来就按分级躺在 `--out` 目录里。

## 测试

```bash
python3 -m unittest test_paperkit -v
```

30 个测试，全部离线：用一份按 OpenAlex 官方字段结构伪造的假 API 把
`discover` 整条流水线跑通，覆盖打分排序、分级、RIS 格式、YAML 注入、
重跑幂等、种子解析失败的降级，以及"HTML 登录页不能被存成 PDF"。
