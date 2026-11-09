# 故障对照

## 服务起不来

先看退出码和日志：

```powershell
service\.venv\Scripts\qq-agent.exe persona --check
service\.venv\Scripts\qq-agent.exe doctor
Get-Content "$env:USERPROFILE\.dsh\qq-agent\logs\service.log" -Tail 40
```

| 现象 | 原因 | 处理 |
|---|---|---|
| `人格卡错误：找不到人格卡` | 没放 `persona.md` | 按 `docs/persona-card.md` 写一张 |
| `人格卡太短` | 少于 80 字 | 补身份、说话方式、边界 |
| `还有占位符` | 模板没改 | 替换 `TODO` / `<角色名>` |
| `PNG 里没有角色卡数据` | 普通图片，不是 SillyTavern 卡 | 换 `.json` 或 `.md` |
| `QQ 未启用` | `[qq].enabled = false` | 改成 `true` |
| `ModuleNotFoundError: websockets` | 依赖没装 | `pip install -e service` 重装 |

## 能连上但不在线

面板显示“服务运行中 / WS 未连接”：

- NapCat 掉登录：重新扫码；
- 3001 没开：WebUI 网络配置里加 WebSocket 服务器；
- token 不一致：`access_token` 和 NapCat 里填的一样；
- 端口被占用：换端口，同时改 `ws_url`。

## 不回消息

按顺序查：

1. `dry_run = true`：只出草稿，日志里找 `[dry-run]`；
2. 对方不在 `friend_whitelist`；
3. 群不在 `group_whitelist`，或没 @（`group_require_at = true`）；
4. 免打扰时段（默认 23–7 点）；
5. 限流：`rate_per_minute` / `min_interval_seconds`；
6. 模型没有 key，且 `allow_heuristic_fallback = false`；
7. 日志里的 `处理失败` / `连不上 NapCat`。

## 发了但内容不对

- 卡里写的规则太抽象：改成可检查的判准（句子长度、称呼、什么时候收住）；
- `persona.example.md` 还没替换完：`persona --check` 会拒绝；
- 历史太长挤掉设定：调小 `history_turns` / `history_max_chars`；
- 模型在编：卡里和系统提示都要求“工具没给就是没有”，但模型仍可能违反；降低 temperature，或换模型。

## 面板 404 / 不显示

- bundle 是否指向仓库根目录，而不是 `service/`；
- DSH 版本是否满足 `engines.dsh`；
- 重启 DSH 后再看；
- `/qq-agent/state` 是否被别的插件占用（看 DSH 日志）。

## 开机自启没生效

- `scripts\install-autostart.ps1` 是否成功；
- `%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\qq-agent.lnk` 是否存在；
- 双击快捷方式看是否报错；
- 日志里是否有 `人格卡错误` 或 `QQ 未启用`；
- 用 `pythonw.exe` 时没有控制台，错误只进日志。

## 可选桌面包

- `/desk/ping` 返回 404：`extras/desktop` 没装，或装到了错误的目录；
- `dsh_run` 返回“DSH 的 desk-bridge 没响应”：DSH 没开，或桌面 bundle 没加载；
- DSH 升级后接口失效：这些 API 是版本敏感的，先看 `extras/desktop/README.md` 的版本说明。
