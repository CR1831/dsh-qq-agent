"""DSH（DeepSeek Harness）桥：看它活着没、必要时从外部重启它。

给 QQ agent 用。为什么这件事成立：`qq_service` 是独立的 pythonw 进程，
不挂在 DSH 的进程树下，所以 DSH 崩了/重启了它都还在——反过来才能去管 DSH。

安全边界（与 tools.py 里的调用点配合）：
- 只在「和主账号的私信」里可用，别人问一律只回"这是号主的机器"；
- 查询（status）只读；
- 操作（control）要求 `confirm=True`，模型必须确认号主是明确要求了才传；
- `stop` 单独存在但默认不开放给模型，只有显式的 force 才执行。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any


def _default_exe() -> str:
    env = os.environ.get("DSH_EXE", "").strip()
    if env:
        return env
    candidates = [
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "DeepSeek Harness" / "DeepSeek Harness.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "DeepSeek Harness" / "DeepSeek Harness.exe",
    ]
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return "DeepSeek Harness"


EXE = _default_exe()
PROCESS_NAME = "DeepSeek Harness"
HOME = Path(os.environ.get("DSH_HOME") or (Path.home() / ".dsh"))
WORKSPACE = Path.cwd()
TASKS_ROOT = WORKSPACE / "_tasks"
KEEP_TASK_DIRS = 30

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def configure(*, dsh_exe: str = "", dsh_home: str = "", workspace: str = "") -> None:
    """Apply the optional [desktop] config before the tools are called."""
    global EXE, HOME, WORKSPACE, TASKS_ROOT
    if dsh_exe:
        EXE = str(Path(dsh_exe).expanduser())
    if dsh_home:
        HOME = Path(dsh_home).expanduser()
    if workspace:
        WORKSPACE = Path(workspace).expanduser().resolve()
        TASKS_ROOT = WORKSPACE / "_tasks"


def task_dir(text: str, *, tag: str = "") -> Path:
    """Create a per-task working directory under ``<workspace>/_tasks``.

    The directory name keeps Chinese/English characters, is capped at 24 chars,
    and only the most recent ``KEEP_TASK_DIRS`` directories are kept.
    """
    raw = (tag or text or "").strip()
    slug = re.sub(r"[^\w\u4e00-\u9fff]+", "-", raw)[:24].strip("-") or "task"
    stamp = time.strftime("%m%d-%H%M%S")
    target = TASKS_ROOT / f"{stamp}-{slug}"
    target.mkdir(parents=True, exist_ok=True)
    try:
        dirs = sorted(p for p in TASKS_ROOT.iterdir() if p.is_dir())
        for old in dirs[:-KEEP_TASK_DIRS]:
            shutil.rmtree(old, ignore_errors=True)
    except OSError:
        pass
    return target


def _ps(script: str, timeout: float = 15.0) -> str:
    """跑一段 PowerShell，返回 stdout。失败抛 RuntimeError。"""
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-NonInteractive",
                "-ExecutionPolicy",
                "Bypass",
                "-Command",
                script,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"PowerShell 超时（{timeout:.0f}s）") from exc
    except OSError as exc:
        raise RuntimeError(f"跑不了 PowerShell：{exc}") from exc
    out = (proc.stdout or "").strip()
    if proc.returncode != 0 and not out:
        err = (proc.stderr or "").strip()[:300]
        raise RuntimeError(f"PowerShell 退出码 {proc.returncode}：{err}")
    return out


_STATUS_PS = r"""
$ErrorActionPreference = "SilentlyContinue"
$procs = @(Get-Process -Name 'DeepSeek Harness')
$main = $procs | Sort-Object StartTime | Select-Object -First 1
$ids = @($procs | ForEach-Object { $_.Id })
$ports = @()
if ($ids.Count -gt 0) {
    $ports = @(Get-NetTCPConnection -State Listen |
        Where-Object { $ids -contains $_.OwningProcess } |
        Select-Object -ExpandProperty LocalPort -Unique)
}
$memMB = 0
if ($procs.Count -gt 0) {
    $memMB = [math]::Round((($procs | Measure-Object WorkingSet64 -Sum).Sum / 1MB))
}
$result = [ordered]@{
    running        = ($procs.Count -gt 0)
    processCount   = $procs.Count
    mainPid        = if ($main) { $main.Id } else { $null }
    startedAt      = if ($main) { $main.StartTime.ToString("yyyy-MM-dd HH:mm:ss") } else { $null }
    uptimeMinutes  = if ($main) { [math]::Round(((Get-Date) - $main.StartTime).TotalMinutes, 1) } else { $null }
    memoryMB       = $memMB
    listenPorts    = $ports
    exeExists      = (Test-Path '__EXE__')
}
$result | ConvertTo-Json -Compress
"""


def _session_stats() -> dict[str, Any]:
    """数一下会话目录：有多少个会话、最近一次是什么时候。"""
    sessions = HOME / "sessions"
    if not sessions.is_dir():
        return {"sessionsDir": str(sessions), "sessionFiles": 0, "lastSessionAt": None}
    files = [p for p in sessions.rglob("*") if p.is_file()]
    last = max((p.stat().st_mtime for p in files), default=None)
    return {
        "sessionsDir": str(sessions),
        "sessionFiles": len(files),
        "lastSessionAt": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(last)) if last else None,
    }


def status() -> dict[str, Any]:
    """DSH 现在什么状态。只读，随时可调。"""
    script = _STATUS_PS.replace("__EXE__", EXE.replace("'", "''"))
    try:
        raw = _ps(script)
        data = json.loads(raw) if raw else {}
    except (RuntimeError, json.JSONDecodeError) as exc:
        return {
            "ok": False,
            "error": f"查 DSH 状态失败：{exc}",
            "exe": EXE,
            "exeExists": Path(EXE).exists(),
        }

    data.update(_session_stats())
    data["exe"] = EXE
    data["ok"] = True

    gui = [p for p in (data.get("listenPorts") or []) if isinstance(p, int)]
    data["guiPort"] = gui[0] if gui else None
    lines = []
    if data.get("running"):
        lines.append(
            f"DSH 在跑：主进程 {data.get('mainPid')}，共 {data.get('processCount')} 个进程，"
            f"内存约 {data.get('memoryMB')} MB"
        )
        lines.append(f"启动于 {data.get('startedAt')}，已运行 {data.get('uptimeMinutes')} 分钟")
        if data.get("guiPort"):
            lines.append(f"Web GUI 端口 {data['guiPort']}")
        else:
            lines.append("没有监听端口——GUI 可能没起来")
    else:
        lines.append("DSH 没在运行")
    if data.get("lastSessionAt"):
        lines.append(f"会话目录 {data.get('sessionFiles')} 个文件，最近改动 {data['lastSessionAt']}")
    data["summary"] = "\n".join(lines)
    return data


def _stop(timeout: float = 20.0) -> dict[str, Any]:
    """结束所有 DSH 进程。返回细节。"""
    script = r"""
$ErrorActionPreference = "SilentlyContinue"
$procs = @(Get-Process -Name 'DeepSeek Harness')
$count = $procs.Count
$procs | Stop-Process -Force
Start-Sleep -Milliseconds 500
$left = @(Get-Process -Name 'DeepSeek Harness')
[ordered]@{ stopped = $count; remaining = $left.Count } | ConvertTo-Json -Compress
"""
    out = _ps(script, timeout=timeout)
    try:
        return json.loads(out) if out else {"stopped": 0, "remaining": 0}
    except json.JSONDecodeError:
        return {"stopped": -1, "remaining": -1, "raw": out[:200]}


def _start(wait_seconds: float = 8.0) -> dict[str, Any]:
    """从外部拉起 DSH。用 explorer 启动，避免挂在本进程树下。"""
    exe = Path(EXE)
    if not exe.exists():
        return {"ok": False, "error": f"找不到 DSH 可执行文件：{EXE}"}
    try:
        subprocess.run(
            ["explorer.exe", str(exe)],
            capture_output=True,
            text=True,
            timeout=15,
            creationflags=CREATE_NO_WINDOW,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "error": f"启动失败：{type(exc).__name__}: {exc}"}
    deadline = time.time() + wait_seconds
    while time.time() < deadline:
        time.sleep(1.0)
        probe = status()
        if probe.get("running"):
            return {"ok": True, "waitedSeconds": round(wait_seconds - (deadline - time.time()), 1),
                    "mainPid": probe.get("mainPid"), "guiPort": probe.get("guiPort")}
    return {"ok": False, "error": f"已发起启动，但 {wait_seconds:.0f} 秒内没看到进程"}


def control(action: str, *, confirm: bool = False) -> dict[str, Any]:
    """对 DSH 动手：restart / start / stop。

    `confirm` 必须为 True 才执行——调用方（模型）要在号主明确要求时才传。
    """
    act = (action or "").strip().lower()
    if act not in {"restart", "start", "stop"}:
        return {"ok": False, "error": f"不认识的动作：{action}（只支持 restart / start / stop）"}
    if not confirm:
        return {
            "ok": False,
            "needsConfirm": True,
            "error": f"要执行 {act}，需要先跟号主确认，再带 confirm=true 调用",
            "hint": "重启会中断 DSH 里正在进行的会话；请先问一句再动手。",
        }

    before = status()
    if act == "start":
        if before.get("running"):
            return {"ok": True, "action": act, "skipped": True, "message": "DSH 本来就在跑，没动它",
                    "status": before}
        started = _start()
        return {"ok": bool(started.get("ok")), "action": act, **started,
                "status": status()}

    if act == "stop":
        stopped = _stop()
        return {"ok": True, "action": act, **stopped, "status": status()}

    # restart
    stopped = _stop()
    time.sleep(2.0)
    started = _start()
    return {
        "ok": bool(started.get("ok")),
        "action": act,
        "stopped": stopped,
        "started": started,
        "status": status(),
    }


__all__ = [
    "EXE",
    "PROCESS_NAME",
    "status",
    "control",
    "run_task",
    "check_result",
    "find_bridge_port",
    "task_dir",
]


# ---------------------------------------------------------------- 任务桥
def _no_proxy_opener() -> urllib.request.OpenerDirector:
    """本机地址绝不能走系统代理——踩过这个坑。"""
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _dsh_listen_ports() -> list[int]:
    script = r"""
$p = Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue
$ids = @($p | ForEach-Object { $_.Id })
if ($ids.Count -eq 0) { exit 0 }
Get-NetTCPConnection -State Listen -ErrorAction SilentlyContinue |
    Where-Object { $ids -contains $_.OwningProcess } |
    Select-Object -ExpandProperty LocalPort -Unique
"""
    out = _ps(script, timeout=20)
    ports = []
    for token in out.replace("\r", "\n").split():
        token = token.strip()
        if token.isdigit():
            ports.append(int(token))
    return sorted({p for p in ports if 1024 < p < 65535})


def find_bridge_port() -> int | None:
    """找到挂了 desk-bridge 插件的那个端口（DSH 桌面版端口每次启动都变）。"""
    opener = _no_proxy_opener()
    for port in _dsh_listen_ports():
        try:
            with opener.open(f"http://127.0.0.1:{port}/desk/ping", timeout=2) as resp:
                data = json.loads(resp.read().decode("utf-8") or "{}")
            if data.get("plugin") == "dsh-desk-bridge":
                return port
        except (urllib.error.URLError, OSError, ValueError):
            continue
    return None


def run_task(
    text: str,
    *,
    timeout_ms: int = 90000,
    background: bool = False,
    session_id: str | None = None,
    preset: str | None = None,
    workdir: str | None = None,
) -> dict[str, Any]:
    """把一条指令交给 DSH 的会话执行。

    - ``background=True``：不等结果，立刻返回投递回执（长任务用）
    - ``session_id``：指定会话名；不给就用插件默认值。每次给不同名字 =
      开一个独立会话（上下文隔离）
    - ``preset``：用哪套 agent 能力；不填用 standard
    - ``workdir``：这次任务的工作目录；建议配合 ``task_dir`` 使用
    """
    text = (text or "").strip()
    if not text:
        return {"ok": False, "error": "指令是空的"}
    port = find_bridge_port()
    if port is None:
        return {
            "ok": False,
            "error": "DSH 的 desk-bridge 没响应：要么 DSH 没开，要么插件没加载。",
            "hint": "让号主重启一下 DSH 试试。",
        }
    payload: dict[str, Any] = {
        "text": text,
        "timeoutMs": timeout_ms,
        "background": bool(background),
    }
    if session_id:
        payload["sessionId"] = session_id
    if preset:
        payload["agentPreset"] = preset
    if workdir:
        payload["cwd"] = workdir
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/desk/run",
        data=body,
        headers={"content-type": "application/json; charset=utf-8"},
        method="POST",
    )
    # 长任务只等投递，别把 HTTP 超时设成任务时长
    http_timeout = 25 if background else timeout_ms / 1000 + 15
    try:
        with _no_proxy_opener().open(req, timeout=http_timeout) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        return {"ok": False, "error": f"DSH 返回 {exc.code}：{detail}"}
    except urllib.error.URLError as exc:
        return {"ok": False, "error": f"连不上 DSH：{exc.reason}"}
    except (OSError, ValueError) as exc:
        return {"ok": False, "error": f"调用失败：{type(exc).__name__}: {exc}"}


def check_result(session_id: str = "desk-bridge", preset: str | None = None) -> dict[str, Any]:
    """查某个会话当前是否跑完、有没有新回复（长任务轮询用）。"""
    port = find_bridge_port()
    if port is None:
        return {"ok": False, "error": "DSH 没在跑或插件没加载"}
    url = (
        f"http://127.0.0.1:{port}/desk/result"
        f"?sessionId={urllib.parse.quote(str(session_id or 'desk-bridge'))}"
    )
    if preset:
        url += f"&preset={urllib.parse.quote(str(preset))}"
    try:
        with _no_proxy_opener().open(url, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as exc:
        return {"ok": False, "error": f"DSH 返回 {exc.code}"}
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return {"ok": False, "error": f"查询失败：{type(exc).__name__}: {exc}"}
