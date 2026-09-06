# 这个目录是暂存，不属于 no-mistakes

`x-bookmarks` 是一个独立的 Python 工具（X 书签同步），跟 no-mistakes 这个 Go CLI
项目没有关系。它放在这个分支上只是因为创建新仓库的权限不够，需要一个不会随容器
销毁而丢失的地方。

搬到自己的私有仓库后，这个目录和分支就可以删掉：

```bash
gh repo create <你>/x-bookmarks --private
cd x-bookmarks && git init && git add -A
git commit -m "初始提交：X 书签同步工具"
git branch -M main
git remote add origin https://github.com/<你>/x-bookmarks.git
git push -u origin main
```

注意 `.github/workflows/sync.yml` 只有在仓库根目录时才会被 GitHub Actions 执行，
所以它待在这里是不会跑的。
