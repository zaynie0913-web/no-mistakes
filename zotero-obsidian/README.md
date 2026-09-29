# paperkit — Zotero + Obsidian 论文流水线

从几篇种子论文出发，自动找出强关联文献、按相关度分三级、下载开放获取 PDF、
生成可直接导入 Zotero 的分级 RIS，并在 Obsidian 里铺好笔记结构和模板。

单文件、纯标准库、零依赖。Python 3.9+ 即可，macOS / Windows / Linux 通用。

## 它解决什么

手动读文献的三个卡点：找关联论文靠运气、下下来堆在一个文件夹里分不清主次、
读的时候划了线但笔记散在 PDF 里回不到笔记库。这个工具把这三段接起来。

## 一键安装

**先把 Obsidian 彻底关掉**，然后在 PowerShell 里粘贴这一整行：

```powershell
mkdir -Force $HOME\paperkit | Out-Null; cd $HOME\paperkit; iwr -UseBasicParsing https://raw.githubusercontent.com/zaynie0913-web/no-mistakes/claude/zotero-obsidian-integration-oxvv2t/zotero-obsidian/paperkit.py -OutFile paperkit.py; py -3 paperkit.py install
```

macOS / Linux：

```bash
mkdir -p ~/paperkit && cd ~/paperkit && curl -fsSLo paperkit.py https://raw.githubusercontent.com/zaynie0913-web/no-mistakes/claude/zotero-obsidian-integration-oxvv2t/zotero-obsidian/paperkit.py && python3 paperkit.py install
```

`install` 会依次：

1. 从 Obsidian 自己的库列表里找到你的库（有多个会让你选）
2. 建好目录、笔记模板、阅读面板
3. 从 Obsidian 官方插件注册表查到 **Dataview** 和 **Zotero Integration** 的仓库，
   按仓库 manifest 里的版本号下载（和 Obsidian 自己装插件的方式一致），并加入启用列表
4. 给 Zotero Integration 预先配好导入格式「导入文献笔记」
5. 没装 Better BibTeX 的话，把最新 `.xpi` 下载到「下载」文件夹
6. 在当前目录建一个 `seeds.txt`

**版本要求**：最新的 Better BibTeX（9.x）要求 **Zotero 8.0.1 以上**，Zotero 7 装不上。
在 Zotero 的「帮助 → 关于 Zotero」里看版本，旧的就先升级。停更的 Zotero Integration
插件用到的 8 个 Better BibTeX 接口，在 9.x 里全部还在。

只剩两件事要手动点，装完会列出来：Obsidian 里开启社区插件（本来开着就跳过），
以及在 Zotero 里装那个 `.xpi`（**工具 → 插件 → 右上角齿轮 → 从文件安装插件**）。
Zotero 不提供命令行装插件的途径，这一步绕不开。

重复运行是安全的：已装的插件不重装，你改过的模板和 `seeds.txt` 不会被覆盖，
你原有的插件和 Zotero Integration 设置都会保留。

**为什么要先关 Obsidian**：Obsidian 开着时改插件列表没用，它退出时会用内存里
的旧列表覆盖回去。检测到它开着，`install` 会停下来等你关。

### 体检

装完、**Zotero 开着**的时候跑：

```powershell
py paperkit.py doctor --vault "C:\Users\你\Documents\Research"
```

它检查目录、模板、两个插件是否**已安装且已启用**，以及 Zotero 里的
Better BibTeX 能不能连上。

不需要 `.bib` 文件：Zotero Integration 直接调用 Better BibTeX 的本地接口，
从来不读 `.bib`。

### Windows 命令写法

下文其余命令按 macOS/Linux 写。PowerShell 里 `python3` 换成 `py`；
反斜杠 `\` 换行 PowerShell 不认，把命令写成一行；路径带空格的加英文双引号。

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

## 从已经下载的 PDF 出发

手上已经有一批论文（比如毕业论文的文献文件夹），可以直接拿它们当种子：

```powershell
py -3 paperkit.py seeds --from-pdfs "D:\毕业论文\01_文献\原始PDF"
py -3 paperkit.py discover --seeds seeds.txt --out papers --vault "你的库路径" --have-seeds
```

`seeds` 会扫描文件夹（含子文件夹）里的每篇 PDF，认出它自己的 DOI、arXiv 编号或标题，
追加到 `seeds.txt`，每行后面注明来源文件和认法。认的顺序：

1. PDF 元数据里写明的 DOI
2. 正文里反复出现的 DOI（期刊一般在每页页眉或页脚印本篇 DOI）
3. arXiv 页边水印（要求带版本号和分类，参考文献里引用的 arXiv 编号不会被误认）
4. 全文只有一个 DOI
5. PDF 元数据里的标题
6. 文件名（知网的「标题_作者.pdf」会去掉作者；`main.pdf`、`paper_final.pdf` 这类不算）

参考文献里有几十个别人的 DOI，各出现一次。所以当 DOI 很多、分不出哪个是本篇时，
宁可退回标题，也不挑一个可能是引用文献的 DOI。扫描版 PDF 或文件名是一串编号、
又没有元数据的，会报「认不出」，可以手动把标题补进清单。

`--have-seeds` 表示种子论文你已经有了：不再下载它们的 PDF，也不写进 `.ris`，
免得和你拖进 Zotero 的原文件重复。

中文文献要注意：OpenAlex 对中文期刊的收录不全，部分中文种子在 `discover` 时
可能显示「没找到」。

## 记住邮箱

```powershell
py -3 paperkit.py config --mailto 你的邮箱
```

存在 `paperkit.py` 旁边的 `paperkit.json` 里，只在你自己电脑上。之后 `discover`
自动使用，不用每次加 `--mailto`。优先级：命令行参数 > 环境变量 `PAPERKIT_MAILTO` > 配置文件。

## 分级是怎么算的

候选集从三个方向采集：种子的**参考文献**（领域基石）、**引用了种子**的文献
（最新进展）、OpenAlex 的 `related_works`（近邻）。然后加权打分：

| 信号 | 权重 | 含义 |
|---|---|---|
| 被种子引用 | 3.0 | 你选的论文都在引它，多半是绕不开的基石 |
| 引用了种子 | 2.5 | 直接的后续工作 |
| 同时关联多篇种子 | 2.5 ×(n−1) | **最强信号**：命中你关注的交集而非某一篇的邻居。只算真正的引用关系 |
| 文献耦合 | 2.0 | 与种子共享参考文献，说明在同一个问题域 |
| OpenAlex 近邻 | 1.0，最多算 2 篇种子 | 按主题相似度算的弱信号，只当排序的补充 |
| 影响力 | 0.8 × log(年均被引)，封顶 2.0 | 取年均值并对数压缩，再封顶：名气不能压过主题相关度 |

两个刻意的修正：近三年的论文 +0.6（还没来得及攒引用）；社论/勘误这类
非研究条目 ×0.75（往下压但不排除）。

文献耦合用 `√(自身参考文献数)` 归一化——否则一篇 300 条参考文献的综述
会纯靠体量霸榜。这条有回归测试盯着
（`test_coupling_normalisation_does_not_reward_bulk_refs`）。

**研究方法文献单独成一级（M-研究方法）。**问卷类研究几乎都引用结构方程、PLS、
因子分析这类方法文献，它们被引动辄上万次，会因为「被多篇种子引用」挤进前排。
标题像方法文献、且和种子标题的主题词毫无交集的，归到 M 级，不占主题论文的名额
（「主题公园与游客满意度的结构方程模型」这类仍算主题论文）。写方法论那章时
直接看 M 级。规则是拿第一份真实推荐列表逐条校验过的
（`TestMethodPapersGetTheirOwnTier`）。

重跑时分级变了的论文，笔记和 PDF 会自动挪到新的分级文件夹，笔记里你写的内容原样保留，
只改分级那几行。

篇数用 `--top-s` / `--top-a` / `--max` / `--top-m` 调，默认 12 / 20 / 60 / 15。

## 边看边写

在 Zotero 的 PDF 阅读器里用**颜色**划重点，模板会按颜色自动归位：

| 颜色 | 归到 |
|---|---|
| 🟡 黄 | 关键结论 |
| 🔴 红 | 存疑与反对 |
| 🟢 绿 | 可复用的方法 |
| 🔵 蓝 | 待深挖 |

读完在 Obsidian 里按 `Ctrl+P` 打开命令面板，执行 **`Zotero Integration: 导入文献笔记`**，
选中论文，划的线就落到对应小节。（插件会给每个导入格式注册一条同名命令，
所以命令名就是 `install` 配好的那个格式名。）笔记 frontmatter 里的 `status` 手动改 `未读 → 在读 → 已读`，
`00-面板/阅读面板.md` 会自动跟着变。

## 已知边界

- **只能下开放获取的 PDF。** 闭源论文只会有元数据条目，正文得靠机构订阅在
  Zotero 里另行抓取。工具会校验响应确实是 PDF，不会把登录页存成 `.pdf`。
- **重跑不会覆盖你改过的笔记**，默认跳过已存在的文件。要重建加 `--force`。
- **Obsidian 的 Zotero Integration 插件已停更**——最后一版 3.2.1 停在 2024-08，
  仓库先迁到 `community-archive/`，现在又挂在 `obsidian-community/` 下。目前仍能
  正常工作，但它是这条链路上唯一没人维护的一环，心里有数。`install` 每次都从
  Obsidian 官方注册表查它的当前地址，仓库再搬家也不会下错。备选是走 Zotero Local API 的 Zotero Bridge。
- RIS 里带了 `L1` 本地 PDF 路径，Zotero 导入时**可能**自动挂上附件，也可能不挂；
  不挂也没关系，PDF 本来就按分级躺在 `--out` 目录里。

## 测试

```bash
python3 -m unittest test_paperkit -v
```

112 个测试，全部离线，在 Python 3.9 / 3.10 / 3.12 / 3.13 / 3.14 上都跑过：

- **discover**：用按 OpenAlex 官方字段结构伪造的假 API 跑通整条流水线，
  覆盖打分排序、分级、RIS 格式、YAML 注入、重跑幂等、种子解析失败的降级，
  以及「HTML 登录页不能被存成 PDF」
- **笔记模板**：把插件源码里的颜色分类函数逐行移植进测试，钉死 Zotero 阅读器的
  四个默认标注色确实落在模板过滤的四个分类里，并且模板只用插件真正支持的
  过滤命令和变量
- **install**：找库、插件下载与版本锁定、启用列表合并、插件配置合并、
  Better BibTeX 检测与下载、网络失败时本地步骤照常完成、Obsidian 开着时拒绝改插件
- **从 PDF 认论文**：元数据 DOI（含带 xmlns 的 XMP 写法）、页眉页脚反复出现的 DOI、
  arXiv 水印、UTF-16 中文标题、嵌套括号和转义的字面量标题、知网文件名、垃圾文件不崩；
  另外用 fpdf2 + pikepdf 生成了元数据压进对象流的真实结构 PDF 做过手工验证
- **Windows**：带 BOM 的种子文件、GBK 终端输出、GBK 的 `tasklist` 输出、挪到 D 盘的已知文件夹
