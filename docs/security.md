# 安全

## 这个项目能做什么

一个 QQ 号上的 agent 可以：

- 读私聊和指定群的消息；
- 把消息写进本地 SQLite；
- 生成回复并发送；
- 检索历史、联网搜索。

默认值按“失败关闭”设计：白名单空 = 不发，`dry_run = true` = 只出草稿，群白名单空 = 群里不说话，`proactive = false` = 不主动找人。

## 主要风险

### 1. 账号风控

OneBot 11 是非官方协议。NapCat 驱动真实 QQ 客户端，腾讯的风控看行为：秒回、群发、频繁加好友、深夜活跃都可能触发限制。建议：

- 用小号，不用主号；
- 先只读；
- 白名单从自己开始；
- 保持 `min_interval_seconds`、`quiet_hours`、频次上限；
- 不要在群里主动刷屏。

### 2. `owner_uid` 配错

`owner_uid` 决定谁能用 `qq_friends`、`qq_friend_status`、`self_check`、`read_log`，以及可选桌面工具。配成陌生人 = 把号主权限给了陌生人。

启动前检查：

- `owner_uid` 是你的号；
- `owner_uid != self_id`；
- `owner_uid` 在 `friend_whitelist` 里（否则号主自己都聊不了）；
- `qq-agent doctor` 里 owner 检查 PASS。

### 3. 桌面控制（可选包）

`extras/desktop` 提供 `dsh_status` / `dsh_control` / `dsh_run`：

- `dsh_control` 能重启/关闭 DSH；
- `dsh_run` 能把任意指令交给 DSH 的会话，等于在机器上执行任意任务。

它默认不安装；装了也只对 `private:<owner_uid>` 开放，`dsh_control` 还要 `confirm=true`。如果你不需要，别装。

### 4. 图片与第三方 API

`[vision].enabled = true` 时，对方发来的图片会被下载并发给视觉模型。默认关闭。开启前想清楚：这是别人的图片。

LLM 调用会把对话内容发给 API 提供方；这是所有云端模型的共同前提。不要在不信任的机器上跑。

### 5. 数据落盘

运行目录里有：

```text
config.toml          含 LLM key、QQ token、SMTP 等
persona.md           你的角色卡
data/qq/qq.db        消息索引
data/qq/history.json 对话历史
data/qq/profiles.json 好友画像
logs/service.log     连接与错误日志（已去掉消息正文）
```

它们都是明文。磁盘加密、系统账户权限、备份策略自己负责。

### 6. 面板

面板只读，不解析人格卡，不直连 NapCat。它通过 DSH 的本地 web server 提供 `/qq-agent/state`。不要把 DSH 的 web server 暴露到公网或不可信局域网。

`showPreviews` 默认 `false`，`showLogs` 默认 `true`（服务日志已去掉正文）。好友画像默认显示，介意就设 `showProfiles: false`。

## 如果密钥泄露

1. 立刻轮换：LLM API key、QQ access token、SMTP 密码、B站 SESSDATA（如果还留着）。
2. 如果已经推到 GitHub：删仓库不够，历史里还在。最省事的是删库重建，从干净目录重新提交；或用 `git filter-repo` 清历史后强制推送。
3. 检查 NapCat 是否被陌生设备登录，必要时改 QQ 密码、踢下线。

## 报告问题

这是一个本地工具，没有托管服务。安全问题请在 GitHub issue 里描述复现步骤，不要贴真实 token、QQ 号或聊天记录。
