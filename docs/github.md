# 上传到 GitHub

## 0. 先确认目录

只从**干净仓库**上传：

```powershell
cd <你的仓库路径>\dsh-qq-agent
```

不要从 `social-agent` 上传。那个目录里有 `config.toml`、`data/`、`logs/`、`exports/` 和人格卡。

## 1. 初始化并检查

```powershell
git init -b main
git add .
git status --short
```

确认 `config.toml`、`persona.md`、`data/`、`logs/`、`exports/`、`.venv/` 都不在列表里。如果出现了，先修 `.gitignore`，再 `git reset`。

```powershell
git check-ignore -v config.toml persona.md data logs exports service\.venv
git ls-files
```

做一次泄漏扫描：

```powershell
git grep -nE "C:\\\\Users\\\\|sk-[A-Za-z0-9]{8,}|access_token\s*=\s*['\"]?[A-Za-z0-9]|smtp_password\s*=\s*['\"]?[^'\"\s]+" -- .
```

再手工扫一遍真实 QQ 号、角色名、学校名、群名：

```powershell
git grep -nE "[1-9][0-9]{8,10}" -- .
```

九到十一位数字可能是 QQ 号，也可能是时间戳、端口或 BV 号；逐条看，不要直接忽略。真实标识不应该出现在测试夹具、文档、示例配置里。

## 2. 首次提交

```powershell
git commit -m "Initial commit: QQ-only agent, persona-card based, DSH panel"
```

## 3. 建远程仓库

### 有 GitHub CLI

```powershell
gh auth status
gh repo create <你的用户名>/dsh-qq-agent --private --source=. --remote=origin --push
```

如果 `gh` 没装：<https://cli.github.com/>。

### 没有 GitHub CLI（网页）

1. 打开 <https://github.com/new>。
2. Repository name：`dsh-qq-agent`。
3. 选 **Private**。
4. **不要**勾 “Add a README / .gitignore / license”，本地已经有了。
5. Create repository。
6. 复制页面上的仓库地址，然后：

```powershell
git remote add origin https://github.com/<你的用户名>/dsh-qq-agent.git
git push -u origin main
```

第一次 push 会要求登录。用 Git Credential Manager、SSH key 或 Personal Access Token；不要把 token 写进仓库或聊天。

## 4. 私库先验收

在 GitHub 网页上确认：

- 仓库是 **Private**；
- 文件树里没有 `config.toml`、`persona.md`、`data/`、`logs/`、`exports/`、`.venv/`；
- README 能正常显示；
- `git log` 只有一个干净的初始提交。

在另一台机器（或新目录）clone 一次，按 README 跑 `install.ps1` 和 `persona --check`，确认不依赖你本机的路径。

## 5. 转公开

确认没有问题后：

```powershell
gh repo edit <你的用户名>/dsh-qq-agent --visibility public --accept-visibility-change-consequences
```

或网页：Settings → General → Danger Zone → Change repository visibility → Public。

转公开前再扫一遍：

```powershell
git grep -nE "C:\\\\Users\\\\|sk-[A-Za-z0-9]{8,}" -- .
git grep -nE "[1-9][0-9]{8,10}" -- .
```

第二条要逐条确认；真实 QQ 号、角色名、学校名都不该出现。

## 6. 打 tag / Release

```powershell
git tag -a v0.1.0 -m "v0.1.0"
git push origin v0.1.0
```

网页：Releases → Draft a new release → 选 `v0.1.0` → 写变更说明。

## 7. 如果误推了密钥

1. 立刻轮换所有相关密钥；
2. 删除远程仓库，重新建一个，从干净目录重新提交；
3. 或者用 `git filter-repo` 清历史后 force push。

删文件不等于删历史。`git log -p` 还能翻出来。

## 8. 常见 push 错误

- `remote origin already exists`：`git remote set-url origin <新地址>`。
- `failed to push some refs`：远程有初始 README，先 `git pull --rebase origin main`，或建空仓库。
- `Authentication failed`：检查 GitHub CLI 登录、PAT 权限（`repo`）、或 SSH key。
- 文件太大：检查有没有把 `.venv`、`data/` 加进来；`git rm -r --cached` 后重新提交。
