# dsh-qq-agent — bilingual overview

## English

**dsh-qq-agent** is a QQ-only agent for DeepSeek Harness (DSH). It connects one
QQ account through **NapCat / OneBot 11**, keeps private and group messages in a
local SQLite index, and replies in a persona you provide. It is not a
multi-platform scraper, and it ships no character card.

### How it runs

1. **Platform edge.** NapCat logs in a QQ account and exposes the OneBot 11 HTTP
   API and a forward WebSocket. The Python service uses HTTP for actions and the
   WebSocket for events.
2. **Local index.** Incoming messages become `Doc` records in SQLite FTS5.
   Chinese text is indexed as bigrams so two-character queries work. Dedupe,
   keyword filters, BM25 relevance, an optional LLM judge and MMR/quota run
   before anything reaches the model.
3. **Bounded agent loop.** The service gives the model a small tool set: local
   search, recent-history pull, result filtering, web search, friend lookup,
   self-check/log reading, and guarded send. With no model key it falls back to
   deterministic keyword retrieval.
4. **Outbound guardrails.** Every send passes code-level checks: private/group
   allowlists, per-minute/hour/day limits, minimum interval, quiet hours,
   random reply delay, proactive-send window, and a global `dry_run` switch.
   Empty allowlists fail closed. Group replies require an allowlisted group and
   an @ mention.
5. **DSH panel.** The DSH bundle is read-only: it reads a small `status.json`
   and optional profile/history files and renders the sidebar panel. It never
   parses the persona card and never connects to NapCat.
6. **Persona is external.** The service loads a user-supplied card at startup:
   Markdown/TXT, SillyTavern JSON (V2/V3), or SillyTavern PNG (`chara`/`ccv3`).
   If the card is missing, too short, or still a template, the service exits
   instead of falling back to a built-in character.

### Features

- QQ-only OneBot 11 ingestion for private chats and allowlisted groups.
- Local-first retrieval with SQLite FTS5, Chinese bigram indexing, dedupe,
  filters, BM25, optional LLM judging, MMR and per-author quotas.
- Persona-card compatibility: Markdown/TXT, SillyTavern JSON, SillyTavern PNG;
  no bundled persona.
- Guarded replies with fail-closed defaults and a dry-run mode.
- Bounded tool loop with citations and a deterministic fallback.
- Read-only DSH panel: heartbeat, WebSocket state, persona load state,
  allowlists, friend profiles, recent conversations, sanitized logs.
- Optional desktop-control bundle, installed separately: `dsh_status`,
  `dsh_control`, `dsh_run` and `dsh_result`; owner-only, high risk, and off by
  default. It can restart DSH or submit tasks to a DSH session.
- Windows autostart script; runtime data lives outside the repository.
- No telemetry. Data stays under `~/.dsh/qq-agent`; outbound traffic is limited
  to the configured LLM, optional web search and optional vision API.

### Requirements and limits

- Windows 10+ for the NapCat path; Python 3.11+; tested with DSH 0.2.2-rc.1
  (see `engines.dsh`).
- OneBot 11 is unofficial. Use a secondary QQ account; account restrictions are
  possible.
- QQ has no message-search API. History must be pulled into the local index
  first.
- The deep-history endpoint returns a limited number of messages per call.
- Desktop control is Windows-specific and optional.

### Quick start

```powershell
git clone https://github.com/CR1831/dsh-qq-agent.git
cd dsh-qq-agent
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

Then write `%USERPROFILE%\.dsh\qq-agent\config.toml`, add your own
`persona.md`, run `scripts\run-qq.ps1`, and install the repository root as a
DSH bundle. Keep `dry_run = true` until the drafts look right.

---

## 中文

**dsh-qq-agent** 是面向 DeepSeek Harness（DSH）的 QQ-only agent。它通过
NapCat / OneBot 11 接入一个 QQ 号，把私聊和群消息写进本地 SQLite 索引，再按
你自己提供的人格卡回复。它不是多平台爬虫，也不内置任何角色卡。

### 运行方式

1. **接入层。** NapCat 登录 QQ 小号，暴露 OneBot 11 的 HTTP API 和正向
   WebSocket；Python 服务用 HTTP 发动作，用 WebSocket 收事件。
2. **本地索引。** 消息先落成 `Doc` 写进 SQLite FTS5；中文按二元组切分，两个字的
   查询也能命中。模型看到内容之前，先去重、过关键词、算 BM25 相关度，可选让模型
   逐条判定，再做 MMR 去冗余和作者配额。
3. **有边界的工具循环。** 模型只有一小套工具：本地检索、拉最近历史、再筛结果、
   联网搜索、查好友、自检/读日志、受护栏约束的发送。没有模型 key 时自动退回
   确定性的关键词检索。
4. **发送护栏。** 每条外发都过代码硬闸：私聊/群白名单、每分钟/小时/天限额、最小
   间隔、免打扰、随机回复延迟、主动发送窗口，以及全局 `dry_run`。白名单为空时
   失败关闭；群聊必须同时在群白名单里、且被 @。
5. **DSH 面板。** DSH bundle 只读：读 `status.json` 和可选的画像/历史文件，渲染
   侧栏面板。它不解析人格卡，也不直连 NapCat。
6. **人格卡外置。** 服务启动时加载用户自己放的卡：Markdown/TXT、SillyTavern
   JSON（V2/V3）或 SillyTavern PNG（`chara`/`ccv3`）。卡不存在、太短、还是
   模板，服务就直接退出，不会退回内置角色。

### 功能

- 只做 QQ：OneBot 11，私聊 + 白名单群。
- 本地优先的检索：SQLite FTS5、中文二元组索引、去重、关键词过滤、BM25、可选
  模型判定、MMR、单作者配额。
- 人格卡兼容：Markdown/TXT、SillyTavern JSON、SillyTavern PNG；不内置人格。
- 失败关闭的发送护栏 + dry-run 草稿模式。
- 有边界的工具循环；带引用，模型不可用时走确定性兜底。
- 只读 DSH 面板：心跳、WS 状态、人格卡加载状态、白名单、好友画像、最近会话、
  已脱敏日志。
- 可选桌面控制包，单独安装：`dsh_status`、`dsh_control`、`dsh_run`、
  `dsh_result`；只对号主开放，高风险，默认不装。它可以重启 DSH，或把任务投进
  DSH 会话执行。
- Windows 开机自启脚本；运行数据全部在仓库之外。
- 没有遥测。数据在 `~/.dsh/qq-agent`；出站只到配置的 LLM、可选的联网搜索和
  可选的视觉 API。

### 要求与限制

- NapCat 路径要求 Windows 10+；Python 3.11+；按 `engines.dsh` 适配 DSH
  0.2.2-rc.1。
- OneBot 11 是非官方协议，建议用小号，账号有被限制的风险。
- QQ 没有消息搜索 API，历史必须先拉进本地索引。
- 历史接口单次返回条数有限，深历史要靠增量拉取。
- 桌面控制是 Windows 专用，且为可选包。

### 快速开始

```powershell
git clone https://github.com/CR1831/dsh-qq-agent.git
cd dsh-qq-agent
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

然后填 `%USERPROFILE%\.dsh\qq-agent\config.toml`，放自己的 `persona.md`，跑
`scripts\run-qq.ps1`，再把仓库根目录作为 DSH bundle 安装。草稿确认之前保持
`dry_run = true`。
