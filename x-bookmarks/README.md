# x-bookmarks

每天把我的 X（Twitter）书签同步到这个仓库，存成 JSON + Markdown，方便 Claude 读。

- `data/bookmarks.json` — 全量结构化数据，去重和增量都靠它
- `data/bookmarks/YYYY-MM.md` — 按推文发布月份分文件的 Markdown，给人和 Claude 读
- `data/index.md` — 索引：账号、总数、最近同步时间、月份列表
- `data/meta.json` — 同步状态（user id 缓存、回填进度）

图片只存链接，不下载。

## 为什么走 API 而不是浏览器抓取

2026 年 2 月 6 日起 X 把 Free/Basic/Pro 三档订阅换成了按量付费 credits。书签端点
`GET /2/users/:id/bookmarks` 属于 **Owned Read**（读自己的数据），**$0.001 / 条**，
最低充值 $5 且不过期。

对个人书签同步来说这比浏览器方案便宜也稳得多：X 现在对数据中心 IP 直接封禁，
GitHub Actions 的 runner 全是数据中心 IP，Playwright 方案要么挂住宅代理（月费远超
API 花费），要么反复触发登录挑战，而且自动化登录抓取有封号风险。

实际成本：存量书签一次性 `$1 / 1000 条`，之后每天只拉增量，大约 `$0.3 / 月`。

## 一次性配置

### 1. 开通 X 开发者账号和按量付费

1. 打开 <https://developer.x.com>，用你的 X 账号登录（国内需要科学上网；X 账号要
   完成手机号验证才能申请开发者）
2. 创建一个 **Project**，再在里面创建一个 **App**
3. 在开发者控制台里充值 credits，最低 $5。**没有生效的计费，所有读接口都会 403**
4. 进 App 的 **User authentication settings**，按下面填：

   | 项目 | 值 |
   | --- | --- |
   | App permissions | `Read` |
   | Type of App | **Native App / Public client**（这样才用 PKCE，不需要 client secret）|
   | Callback URI | `http://127.0.0.1:8723/callback` |
   | Website URL | 随便填一个，比如你的 GitHub 主页 |

5. 保存后复制 **OAuth 2.0 Client ID**（不是 API Key，也不是 Client Secret）

> Callback URI 必须和上面**一字不差**，端口 8723 是 `scripts/auth.py` 里写死的。

### 2. 本地授权，拿 refresh token

在**你自己的电脑**上（能访问 x.com 的那台）：

```bash
git clone https://github.com/<你>/x-bookmarks.git
cd x-bookmarks
pip install -r requirements.txt
python3 scripts/auth.py --client-id <上一步复制的 Client ID>
```

浏览器会弹出 X 的授权页，点 Authorize。脚本会：

1. 换到 access token 和 refresh token
2. 检查 `bookmark.read` 权限确实拿到了
3. **冒烟测试 `/2/users/me` 和书签端点** —— 这一步是故意放在写任何自动化之前的：
   2026 年有若干开发者报告说迁移到按量付费后这两个接口仍然返回 403，与其把 CI
   全部搭完才发现，不如在这里两分钟内就知道
4. 打印出要填进 GitHub Secrets 的两个值

### 3. 建一个 fine-grained PAT

X 的 refresh token **用一次就作废并换发新的**，所以 workflow 每天必须把新 token
写回 Secrets，这需要一个能写 Secrets 的 token：

1. GitHub → Settings → Developer settings → **Personal access tokens** →
   **Fine-grained tokens** → Generate new token
2. Repository access: **Only select repositories** → 选这个仓库
3. Repository permissions: **Secrets** → `Read and write`
4. 有效期建议选一年，到期前 GitHub 会发邮件提醒

### 4. 填 Secrets

仓库 → Settings → Secrets and variables → **Actions** → New repository secret，
建三个：

| 名字 | 值 |
| --- | --- |
| `X_CLIENT_ID` | 第 1 步的 OAuth 2.0 Client ID |
| `X_REFRESH_TOKEN` | 第 2 步脚本打印出来的 refresh token |
| `GH_PAT` | 第 3 步的 fine-grained PAT |

### 5. 手动跑一次

Actions → **Sync X bookmarks** → Run workflow。

第一次会开始回填存量书签，每次最多 30 页（3000 条，约 $3）。没跑完的部分会记在
`data/meta.json` 的 `backfill_cursor` 里，之后每天自动接着回填，直到
`backfill_complete` 变成 `true`。想一次跑完就把 workflow 里的 `MAX_PAGES` 调大，
但注意那是直接的花费。

之后每天 UTC 21:17（北京时间次日 05:17）自动跑。

## 日常使用

- **看书签**：读 `data/index.md`，或直接翻 `data/bookmarks/`
- **让 Claude 读**：把这个仓库挂进 Claude 的会话，或者直接贴 `data/bookmarks/2026-09.md`
- **手动同步**：Actions → Run workflow
- **全量重扫**（怀疑漏了东西时）：Run workflow 时勾上 `full`
- **只重新生成 Markdown**（改了渲染格式，不花钱）：`python3 scripts/render.py`

## 出问题时

| 现象 | 原因 / 处理 |
| --- | --- |
| `invalid_grant` | refresh token 作废了（通常是两个 run 撞车，或手动跑过一次本地 sync）。重跑 `scripts/auth.py`，更新 `X_REFRESH_TOKEN` |
| 书签端点 403 | 按顺序查：① 开发者控制台里 credits 余额是不是 0；② 授权时有没有勾到 `bookmark.read`（重跑 auth.py）；③ 都正常的话是 X 侧按量付费迁移的已知 bug，找 X support |
| `GH_PAT is not set` | Secret 没配，或者 PAT 过期了。脚本是在刷新 token **之前**检查的，所以这个报错不会浪费掉你的 refresh token |
| 429 rate limited | 书签端点限流很紧，脚本会自动等待重试，不用管 |
| Actions 没按时跑 | GitHub 的定时任务在高负载时会延迟甚至跳过，属正常。手动 Run workflow 即可 |

## 开发

```bash
python3 scripts/test_sync.py    # 离线测试，不联网不花钱
```

覆盖了几处容易悄悄写错的地方：长推文必须取 `note_tweet` 而不是被截断的 `text`、
视频类媒体要回退到预览帧、增量同步在遇到已存书签时的停止条件、翻页上限和续传
游标、以及月份文件的清理。
