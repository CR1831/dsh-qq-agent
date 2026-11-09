# 可选桌面控制包

> **高风险 / High risk.** 这不是只读面板。装上并启用后，QQ 号主可以让这个 agent
> 重启/关闭 DSH，或把一段指令投进 DSH 的会话里执行。默认不安装、不启用；
> 不需要就不要装。

## 它是什么，不是什么

`extras/desktop/` 是一个**独立的 DSH bundle**，和核心的 QQ 面板分开安装。它给
Python 服务提供三个号主专用工具：

| 工具 | 能做什么 | 是否需要 `/desk/*` |
|---|---|---|
| `dsh_status` | 查 DSH 是否在跑、进程数、内存、GUI 端口、最近会话 | 不需要，走本机 PowerShell |
| `dsh_control` | `restart` / `start` / `stop` DSH；要求 `confirm=true` | 不需要，走本机 PowerShell |
| `dsh_run` | 把一段文字投进一个 DSH 会话，等它执行；可 `background=true` | 需要，走 `extras/desktop` 的 `/desk/run` |
| `dsh_result` | 查 `background=true` 的任务是否跑完、有没有新回复 | 需要，走 `/desk/result` |

它**不是**：

- 不是 DSH 的图形界面远程控制，不会点按钮、拖窗口；
- 不是从 QQ 直接开一个 shell；`dsh_run` 投的是 DSH 会话消息，实际能做什么取决于
  DSH 里那个 agent 的工具和权限；
- 不是默认能力。`[desktop].enabled` 默认 `false`，`extras/desktop` 也不会被核心
  安装步骤自动安装。

## 前置条件

- Windows 10+；`dsh_status` / `dsh_control` 依赖 PowerShell 和 DSH 的可执行文件。
- 已装好核心 `dsh-qq-agent`，QQ 服务能正常收发。
- DSH 能正常启动；桌面 bridge 使用 DSH 的 `sessionController` /
  `sessionPersistence`，这些接口对 DSH 版本敏感。
- `owner_uid` 必须正确；这些工具只对 `private:<owner_uid>` 开放。

## 安装

1. 先按主 README 安装并跑通核心 QQ 服务。
2. 在 DSH 里安装桌面 bundle，target 指向 **`extras/desktop`**，不是仓库根目录：

   ```text
   用 plugin_manager 的 install_bundle，目标是 <仓库>\extras\desktop
   ```

   或在 DSH 插件管理界面选择这个子目录。
3. 在 `%USERPROFILE%\.dsh\qq-agent\config.toml` 打开：

   ```toml
   [desktop]
   enabled = true
   dsh_exe = ""          # 留空自动找 Windows 安装路径；也可填绝对路径
   dsh_home = ""         # 留空 ~/.dsh
   session_id = "qq-agent"
   cwd = "C:\你的工作区"  # 重要：空 = 进程 cwd，通常是 profile 目录
   timeout_ms = 90000
   ```

   `cwd` 决定 DSH 会话在哪个目录工作。不要留空，除非你清楚进程 cwd 是什么。
4. 重启 QQ 服务。`[desktop].enabled=true` 时，工具才会注册。
5. 在号主私信里验证：

   ```text
   dsh_status {}
   dsh_control {"action": "restart", "confirm": true}
   dsh_run {"instruction": "看一下当前工作区有多大", "background": false}
   ```

   第一次只建议测 `dsh_status`；`dsh_control` 会中断 DSH 会话。

## 边界与风险

- **owner 校验**：`desktop.py` 只认 `private:<owner_uid>`。`owner_uid` 填错 =
  把重启 DSH、投递任务的能力给了错误的人。
- **`confirm` 不是密码**：`dsh_control` 要求模型传 `confirm=true`，但这是模型
  自己填的布尔值；它只能减少误触，不能抵抗被诱导的模型。
- **`dsh_run` 最危险**：它把文字投进 DSH 会话，DSH agent 能做什么，它就能间接
  做什么。不要把它理解成“只是问 DSH 一个问题”。
- **本地路由**：`/desk/ping`、`/desk/run`、`/desk/result` 只接受 loopback 请求，
  但它们没有额外的认证；任何本机进程都能调用。真正的 owner 闸在 Python 侧。
- **提示注入**：非号主私信没有这些工具；但不要把号主账号借给别人，也不要在号主
  会话里粘贴来路不明的“系统指令”。
- **版本敏感**：DSH 升级后先看 `/desk/ping` 和 `dsh_status`；接口变了就更新
  `extras/desktop/lib/index.js` 或直接卸载这个可选包。
- **数据**：`dsh_run` 的内容会进入 DSH 会话日志；不要把密钥、密码、身份证号
  交给它。

## 关闭 / 卸载

只关能力：

```toml
[desktop]
enabled = false
```

重启 QQ 服务后，`dsh_status` / `dsh_control` / `dsh_run` / `dsh_result` 不再注册。

彻底移除：

1. 在 DSH 插件管理里移除 `dsh-qq-agent-desktop` bundle；
2. 删除 `extras/desktop`（如果你不需要保留源码）；
3. 保持 `[desktop].enabled=false`。

## 和核心面板的区别

核心面板是只读的：只读 `status.json` 和可选的画像/历史文件，不解析人格卡，不直连
NapCat，也不能操作 DSH。桌面控制是**另一个** bundle，默认不装；它的存在不应该
被理解成核心插件的默认能力。

## English summary

`extras/desktop` is an optional, separately installed DSH bundle. When
`[desktop].enabled = true` it exposes owner-only tools: `dsh_status`
(read-only), `dsh_control` (restart/start/stop DSH, requires `confirm=true`),
`dsh_run` (submit a message to a DSH session, optionally in the background) and
`dsh_result` (poll a background task). `dsh_status` and `dsh_control` use local
PowerShell; `dsh_run` / `dsh_result` need the bundle's local `/desk/*` routes.
It is off by default, Windows-specific, and effectively grants remote task
execution to the owner's QQ account. Treat it as a high-risk extra, not as a
read-only panel.
