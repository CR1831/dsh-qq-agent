# 安装

## 前置

- Windows 10+（NapCat 主要在 Windows 上跑）
- Python 3.11+
- DSH 0.2.2-rc.1（或兼容版本）
- QQ 小号 + NapCat
- 一个 LLM API key（不填则走关键词兜底，没有真正对话能力）

## 1. 安装 Python 环境

```powershell
git clone https://github.com/CR1831/dsh-qq-agent.git
cd dsh-qq-agent
powershell -ExecutionPolicy Bypass -File scripts\install.ps1
```

它会在 `service\.venv` 建虚拟环境、安装包，并在 `%USERPROFILE%\.dsh\qq-agent\` 建运行目录。不会创建 `persona.md`。

## 2. 填配置

编辑 `%USERPROFILE%\.dsh\qq-agent\config.toml`。最小可用：

```toml
[agent]
api_key = "sk-..."          # 或留空用环境变量

[qq]
enabled = true
api_base = "http://127.0.0.1:3000"
ws_url = "ws://127.0.0.1:3001"
access_token = "NapCat token"
self_id = 机器人小号
owner_uid = 你自己的 QQ 号
friend_whitelist = [你自己的 QQ 号]
dry_run = true
```

NapCat 细节见 [napcat.md](napcat.md)。

## 3. 放人格卡

把 `persona.example.md` 复制成 `%USERPROFILE%\.dsh\qq-agent\persona.md`，按 [persona-card.md](persona-card.md) 改写。仓库里没有可用卡。

检查：

```powershell
service\.venv\Scripts\qq-agent.exe persona --check
```

## 4. 安装 DSH 插件

在 DSH 里让 agent 执行：

```text
用 plugin_manager 的 install_bundle，目标是 <仓库绝对路径>
```

或者在 DSH 的插件管理界面选择这个仓库目录。**指向仓库根目录**，不是 `extras/desktop`。

安装后侧栏出现 **QQ Agent**。面板会显示“服务未运行”，因为服务还没启动。

## 5. 启动

```powershell
scripts\run-qq.ps1
```

或直接：

```powershell
$env:QQ_AGENT_HOME = "$env:USERPROFILE\.dsh\qq-agent"
service\.venv\Scripts\python.exe -m qq_agent qq --listen
```

日志：`%USERPROFILE%\.dsh\qq-agent\logs\service.log`。

## 6. 开机自启（可选）

```powershell
scripts\install-autostart.ps1
```

它会在当前用户的 Startup 目录创建 `qq-agent.lnk`，用 `pythonw.exe` 启动，日志同上。

卸载：

```powershell
scripts\uninstall-autostart.ps1
```

## Linux / macOS

```bash
./scripts/install.sh
```

服务：

```bash
QQ_AGENT_HOME="$HOME/.dsh/qq-agent" service/.venv/bin/python -m qq_agent qq --listen
```

自启用 systemd user unit 或 launchd；把上面的命令写进去，`WorkingDirectory` 设为 `service/`，环境变量 `QQ_AGENT_HOME` 指向运行目录。NapCat 的可用性请自行确认。

## 升级

```powershell
git pull
service\.venv\Scripts\python.exe -m pip install -e service
```

人格卡、配置、数据都在运行目录，不会被动。重启服务。

## 卸载

1. `scripts\uninstall-autostart.ps1`
2. 停服务
3. 在 DSH 插件管理里移除 `dsh-qq-agent`
4. 删除 `%USERPROFILE%\.dsh\qq-agent\`
5. 删除仓库目录

## 可选：桌面控制（高风险）

见 [desktop-control.md](desktop-control.md)。它是单独的 DSH bundle，默认不装、
不启用；启用后号主可以让 agent 重启/关闭 DSH 或把指令投进 DSH 会话执行。
不需要就不要装。
