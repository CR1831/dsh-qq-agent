# NapCat / OneBot 11 配置

QQ 没有对个人开放的“搜群消息”接口。这个项目走 **OneBot 11**：在本机跑一个协议实现（NapCat），用它登录的 QQ 小号收发消息。

> 非官方协议。腾讯的风控会识别异常行为，后果可能是账号被限制或封禁。用小号、先只读、先白名单、频率克制。

## 1. 安装 NapCat

从 [NapCat Releases](https://github.com/NapNeko/NapCatQQ/releases/) 下载 Windows 包，解压，运行 `napcat.exe`。首次启动会在 `config\` 下生成配置，并在终端打印 WebUI 地址（默认 `http://127.0.0.1:6099`）和登录 token。

## 2. 登录小号

用手机 QQ 扫码登录**小号**。登录成功后终端会显示账号 UIN。

## 3. 开两个网络服务

WebUI → 网络配置：

| 类型 | 端口 | 用途 |
|---|---|---|
| HTTP 服务器 | 3000 | 本项目主动调用：拉历史、发消息 |
| WebSocket 服务器 | 3001 | NapCat 主动推送：新消息事件 |

两个都填：

- Host：`127.0.0.1`（不要 `0.0.0.0`）
- Token：同一个随机串，例如 `qbot-7f3a9c`
- 消息格式：`array`

## 4. 填进配置

```toml
[qq]
enabled = true
api_base = "http://127.0.0.1:3000"
ws_url = "ws://127.0.0.1:3001"
access_token = "qbot-7f3a9c"
self_id = 机器人小号的 QQ 号
```

先手工验一下 HTTP：

```powershell
curl.exe -X POST http://127.0.0.1:3000/get_login_info `
  -H "Authorization: Bearer qbot-7f3a9c" `
  -H "Content-Type: application/json" -d "{}"
```

期望返回 `{"status":"ok",...,"data":{"user_id":...,"nickname":...}}`。

## 5. 体检

```powershell
qq-agent doctor
```

它会检查配置、人格卡、owner、白名单、`websockets`、NapCat HTTP 和 WS。全部 PASS 再启动。

## 6. 先只读

第一次建议：

```toml
dry_run = true
friend_whitelist = [你自己的 QQ 号]
group_whitelist = []
```

启动后只跟自己的号聊，看草稿质量。确认后再把 `dry_run` 改 `false`，再逐步加白名单。

## 常见问题

- **401/403**：token 不一致。
- **连不上 3000**：HTTP 服务器没开，或端口不对。
- **连不上 3001**：WebSocket 服务器没开；NapCat 掉登录后端口也会消失。
- **能登录但好友列表为空**：会话可能坏了，重启 NapCat 重新扫码。
- **群消息收不到**：群号不在 `group_whitelist`，或没有 @ 机器人（`group_require_at = true`）。
