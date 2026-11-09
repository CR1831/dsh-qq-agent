# dsh-qq-agent

**版本 / Version:** 0.2.0 · [Changelog](CHANGELOG.md)

一个只做 QQ 的 OneBot 11 agent：收私聊/群消息，检索本地索引，按你自己放的人格卡回复；发送前过代码硬闸。

它由两部分组成：

- **DSH 插件**（仓库根目录）：侧栏面板 + 一个只读状态路由；
- **Python 服务**（`service/`）：连接 NapCat、维护索引、生成回复。

另有一个**可选、默认不安装**的高风险桌面控制包（`extras/desktop`），用于让号主
重启/关闭 DSH 或把指令投进 DSH 会话；不需要就不要装。详见
[桌面控制说明](docs/desktop-control.md)。

仓库里**没有任何人格卡**。安装后你必须自己放一张；没有卡，服务不会启动。

## Overview / 概览

**English.** `dsh-qq-agent` is a QQ-only OneBot 11 agent for DeepSeek Harness:
NapCat carries the QQ connection, a Python service keeps a local SQLite index
and runs a bounded tool loop, and a read-only DSH sidebar shows service state.
Replies are generated from a persona card **you** provide; the repository ships
no character. Every outbound message passes allowlists, rate limits, quiet
hours, random delay and a global `dry_run` switch.

**中文。** `dsh-qq-agent` 是面向 DeepSeek Harness 的 QQ-only OneBot 11 agent：
NapCat 负责 QQ 连接，Python 服务维护本地 SQLite 索引并跑有边界的工具循环，
DSH 侧栏只读显示服务状态。回复使用**你自己提供**的人格卡，仓库不内置角色；
每条外发都过白名单、限频、免打扰、随机延迟和全局 `dry_run`。

完整的中英双语说明见 [docs/overview-bilingual.md](docs/overview-bilingual.md)。

## 0.2.0 更新

- 可选桌面包增加后台任务队列：`background=true` 入队，完成/超时主动私信号主；
  同步超时会自动转入队列，不再丢结果。
- 可选桌面包增加 `dsh_result`，用于手动查后台任务。
- `dsh_run` 支持 `session` / `preset` / `isolate` / `background`；
  `isolate=true` 会在 `<cwd>/_tasks/` 下开独立目录，只保留最近 30 个。
- DSH desk bridge 支持 `agentPreset` 和每次请求的 `cwd`。
- `preset` 是能力档，不是安全沙箱；完整说明见
  [docs/desktop-control.md](docs/desktop-control.md)。
- 版本从 0.1.0 升到 0.2.0；变更清单见 [CHANGELOG.md](CHANGELOG.md)。

## 架构

```text
QQ 客户端 ── NapCat (OneBot 11) ──HTTP/WS──┐
                                          ▼
                              service/qq_agent
                          ┌───────────────┴───────────────┐
                          │  SQLite FTS5 索引              │
                          │  去重 → 过滤 → 相关度 → 配额    │
                          │  qq_guard（白名单/限流/免打扰） │
                          └───────────────┬───────────────┘
                                          │ status.json / logs
                                          ▼
                                   DSH 侧栏面板
```

## 快速开始

要求：Windows 10+（NapCat 目前主要在 Windows 上跑）、Python 3.11+、一个 QQ 小号、NapCat。

```powershell
git clone https://github.com/CR1831/dsh-qq-agent.git
cd dsh-qq-agent
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

安装脚本会：

1. 建 `service\.venv` 并安装 Python 包；
2. 在 `%USERPROFILE%\.dsh\qq-agent\` 下创建 `config.toml`；
3. 把 `persona.example.md` 复制到运行目录；
4. **不会**创建 `persona.md`。

然后：

1. 按 [NapCat 配置](docs/napcat.md) 打开 HTTP 3000 / WS 3001，记下 token。
2. 编辑 `%USERPROFILE%\.dsh\qq-agent\config.toml`：
   ```toml
   [qq]
   enabled = true
   self_id = 你的机器人小号
   owner_uid = 你自己的 QQ 号
   friend_whitelist = [你的 QQ 号]  # 先只放自己
   access_token = "NapCat 里填的 token"
   dry_run = true                   # 先只出草稿
   ```
   把 `[agent].api_key` 填上，或用环境变量 `QQ_AGENT_AGENT__API_KEY`。
3. 写你自己的卡：`%USERPROFILE%\.dsh\qq-agent\persona.md`。格式见 [人格卡](docs/persona-card.md)。支持 Markdown / TXT、SillyTavern JSON、SillyTavern PNG。
4. 启动：
   ```powershell
   scripts\run-qq.ps1
   ```
5. 在 DSH 里安装仓库根目录的 bundle（`plugin_manager install_bundle` 指向这个目录），侧栏出现 **QQ Agent**。
6. 确认草稿没问题后，把 `dry_run` 改成 `false`，重启服务。

开机自启（可选）：

```powershell
scripts\install-autostart.ps1
```

它在当前用户的 Startup 目录放一个 `qq-agent.lnk`，用 `pythonw.exe` 隐藏窗口启动，日志写 `%USERPROFILE%\.dsh\qq-agent\logs\service.log`。卸载：

```powershell
scripts\uninstall-autostart.ps1
```

macOS / Linux 的安装脚本是 `scripts/install.sh`；自启请用 systemd user unit 或 launchd，见 [安装说明](docs/install.md)。

## 人格卡：仓库里没有，必须自己放

服务启动时读 `[qq].persona_file`（默认 `<home>/persona.md`）。三种格式：

| 格式 | 说明 |
|---|---|
| `.md` / `.txt` | 直接作为角色设定文本 |
| `.json` | SillyTavern V2/V3 卡，或同名扁平对象 |
| `.png` | SillyTavern PNG 卡（`chara` / `ccv3` 文本块） |

校验：

- 至少 80 字，最多 20,000 字；
- 模板占位符（`TODO`、`<角色名>` 等）未替换会被拒绝；
- 不存在/读不了/格式不支持 → 退出码 2，面板显示“未配置”；
- `--allow-no-persona` 只给开发测试用。

你的卡只放在运行目录，不进仓库、不进插件、不进日志。仓库只提供 `persona.example.md` 这个**格式模板**，它本身不可用。

## 默认安全值

| 项 | 默认 | 说明 |
|---|---|---|
| `[qq].dry_run` | `true` | 只生成草稿，不发送；确认后再关 |
| `friend_whitelist` | `[]` | 空 = 不回复任何私聊 |
| `group_whitelist` | `[]` | 空 = 群里一句不说 |
| `group_require_at` | `true` | 群里只被 @ 才回 |
| `proactive` | `false` | 不主动找人；需要“发吧”临时授权 |
| `[vision].enabled` | `false` | 读图会把图片发给视觉模型 |
| `use_profile_facts` | `false` | 不把年龄/学校/生日注入 prompt |
| `cross_chat_digest` | `false` | 不允许号主问其他会话 |
| 桌面控制 | 不安装 | 单独的 `extras/desktop` 可选包，见下 |

`qq_guard` 在代码里执行白名单、频次、免打扰、最小间隔和随机延迟；模型改不了。

## 可选：桌面控制（高风险，默认关闭）

> **这不是只读面板。** 启用后，QQ 号主可以让 agent 重启/关闭 DSH，或把一段指令
> 投进 DSH 的会话里执行。默认不安装、不启用；不需要就不要装。

`extras/desktop/` 是一个**独立的 DSH bundle**，必须单独安装。启用后提供四个
号主专用工具：

| 工具 | 能做什么 | 风险 |
|---|---|---|
| `dsh_status` | 查 DSH 进程、内存、GUI 端口、最近会话 | 只读，低 |
| `dsh_control` | `restart` / `start` / `stop` DSH，要求 `confirm=true` | 会中断 DSH 会话 |
| `dsh_run` | 把指令投进 DSH 会话执行；支持 session / preset / isolate / background | 等于远程任务执行 |
| `dsh_result` | 查后台任务有没有跑完、有没有新回复 | 只读 |

`dsh_run` 的 `isolate=true` 会建 `<cwd>/_tasks/<时间-任务名>/`（只保留最近 30 个）；
`background=true` 会写入 `<data>/dsh_tasks.json`，服务每 15 秒轮询，完成或超时主动
私信号主。`preset` 是能力档，不是安全沙箱。

**默认状态：**

- `[desktop].enabled = false`；
- 核心 `install.ps1` 不会安装 `extras/desktop`；
- 不装这个 bundle、不打开配置，四个工具都不会注册。

它**不是** DSH 图形界面的远程控制，也不是从 QQ 直接开 shell；`dsh_run` 投的是
DSH 会话消息，实际能做什么取决于 DSH 里那个 agent 的工具和权限。`cwd` 决定
DSH 会话在哪个目录工作，安装时一定要设。

安装、威胁模型、验证和卸载步骤：[docs/desktop-control.md](docs/desktop-control.md)。
英文摘要：[extras/desktop/README.md](extras/desktop/README.md)。

## 面板

侧栏面板只读：

- 服务心跳、WS 连接、白名单人数、dry-run 状态；
- 人格卡是否加载、格式、字数、路径（**不显示卡内容**）；
- 好友画像、最近会话（预览默认关闭）；
- 服务日志（已去掉消息正文）。

它不解析人格卡，不直连 NapCat。`status.json` 由服务写，面板只读。

## 隐私

- 数据都在 `<home>` 下：`data/qq/`、`logs/`、`persona.md`、`config.toml`。
- 不发送遥测。
- 唯一的出站请求：LLM API；开启 `[vision]` 时才有图片请求；`web_search` 开启时才有搜索请求。
- 删除数据：停服务，删 `<home>` 即可。
- 不要把 `config.toml`、`persona.md`、`data/`、`logs/` 提交到 Git；`.gitignore` 已覆盖。

## 已知限制

- 走 NapCat/OneBot 11，是非官方协议；账号有被风控/限制的风险。先只读、先小号、先白名单。
- QQ 没有消息搜索 API；只能把群历史拉进本地索引再检索。
- 群历史接口一次最多几百条，深历史要靠定时增量拉取。
- SQLite 单机够用到十万级；再大需要换存储。
- DSH 插件按 0.2.2-rc.1 的接口写；升级 DSH 后先看面板是否正常。
- 可选桌面包默认关闭，且只在 Windows 上验证；`preset` 不是硬隔离，
  `_tasks` 只保留最近 30 个任务目录。
- 后台任务通知依赖 QQ 服务在跑；服务停着时不会主动发消息，但任务文件会保留。

## 文档索引

| 文档 | 讲什么 |
|---|---|
| [docs/install.md](docs/install.md) | 安装、DSH bundle、开机自启、升级/卸载 |
| [docs/napcat.md](docs/napcat.md) | NapCat / OneBot 11 接入与故障对照 |
| [docs/persona-card.md](docs/persona-card.md) | 人格卡格式、校验、写法、边界 |
| [docs/desktop-control.md](docs/desktop-control.md) | 可选桌面包：能力、安装、威胁模型、卸载 |
| [docs/security.md](docs/security.md) | 默认安全值、owner 校验、密钥与数据 |
| [docs/troubleshooting.md](docs/troubleshooting.md) | 起不来、不回消息、面板不显示 |
| [docs/overview-bilingual.md](docs/overview-bilingual.md) | 完整中英双语说明 |
| [docs/github.md](docs/github.md) | 发布、审计、打 tag |
| [CHANGELOG.md](CHANGELOG.md) | 版本变更 |

## 一句话速查

```text
服务起不来/不回消息  → docs/troubleshooting.md（先 qq-agent doctor / self_check）
QQ 连不上 NapCat     → docs/napcat.md
人格卡被拒           → docs/persona-card.md（qq-agent persona --check）
面板不显示           → 确认 bundle 指向仓库根目录，刷新/重启 DSH
想远程让 DSH 干活    → docs/desktop-control.md（默认关闭，高风险）
想发布               → docs/github.md
```

## 开发

```powershell
cd service
python -m venv .venv
.venv\Scripts\python -m pip install -e ".[dev]"
.venv\Scripts\python -m pytest -q
```

测试不访问网络，不包含真实 QQ 号，也不包含任何人的人格卡。

## License

MIT。人格卡由使用者自己提供，使用者负责其内容与角色版权。
