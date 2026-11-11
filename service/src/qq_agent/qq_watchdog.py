"""NapCat 掉线看门狗：报警，可选地自动拉起一次 NapCat。

背景：NapCat 注入后 QQ 不显示登录窗口。掉登录时服务会安静地每 30 秒重试，
没人知道。更麻烦的是 NapCat 自己重启时可能走二维码登录；如果快速登录缓存
还在，外部执行一次 launcher 反而能恢复。所以这里提供 **可选** 的自动重启。

默认只报警，不自动重启。自动重启需要同时配置：
- ``[qq].auto_restart_napcat = true``
- ``[qq].napcat_home`` 指向含 ``launcher-user.bat`` 的目录

报警走本地：写 ``ALERT.json`` + Windows 托盘气泡；恢复后清掉。
邮件报警由 ``qq_dm._alert_email()`` 负责，配置在 ``[alert]``。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD_MIN = 10.0
ALERT_NAME = "ALERT.json"
AUTO_RESTART_MIN = 2.0
DEFAULT_LAUNCHER = "launcher-user.bat"
CREATE_NO_WINDOW = 0x08000000


def alert_path(db_path: str | Path) -> Path:
    return Path(db_path).expanduser().resolve().parent / ALERT_NAME


def _balloon(title: str, body: str) -> bool:
    """弹托盘气泡。失败返回 False——报警不能影响主流程。"""
    try:
        import subprocess

        script = (
            "Add-Type -AssemblyName System.Windows.Forms;"
            "$n = New-Object System.Windows.Forms.NotifyIcon;"
            "$n.Icon = [System.Drawing.SystemIcons]::Warning;"
            f"$n.BalloonTipTitle = '{title}';"
            f"$n.BalloonTipText = '{body}';"
            "$n.Visible = $true;"
            "$n.ShowBalloonTip(15000);"
            "Start-Sleep -Seconds 16;"
            "$n.Dispose()"
        )
        subprocess.Popen(
            ["powershell", "-NoProfile", "-WindowStyle", "Hidden", "-Command", script],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return True
    except Exception:
        return False


class Watchdog:
    """跟踪「一直连不上」的时长。

    - 掉线 ``restart_after_min`` 分钟后，尝试自动拉起 NapCat **一次**；
    - 跨过 ``threshold_min`` 后报警一次；
    - 恢复后复位。
    """

    def __init__(
        self,
        path: Path,
        *,
        threshold_min: float = DEFAULT_THRESHOLD_MIN,
        notify: Any = None,
        auto_restart: bool = False,
        restart_after_min: float = AUTO_RESTART_MIN,
        qq_number: str = "",
        napcat_home: str = "",
        launcher: str = DEFAULT_LAUNCHER,
    ) -> None:
        self.path = path
        self.threshold = max(0.0, float(threshold_min)) * 60
        self._notify = notify if notify is not None else _balloon
        self.down_since: float | None = None
        self.alerted = False
        self._auto_restart = bool(auto_restart)
        self._restart_after = max(0.0, float(restart_after_min)) * 60
        self._restart_attempted = False
        self._restarted = False
        self._qq_number = str(qq_number or "")
        self._napcat_home = str(napcat_home or "")
        self._launcher = str(launcher or DEFAULT_LAUNCHER)

    @property
    def down_seconds(self) -> float:
        if self.down_since is None:
            return 0.0
        return max(0.0, time.time() - self.down_since)

    @property
    def restarted(self) -> bool:
        return self._restarted

    def _restart_napcat(self) -> bool:
        """拉起 NapCat；失败不影响主流程。

        用 ``cwd=`` 传工作目录，不拼 ``cmd /c cd ...``——后者的引号嵌套
        会让 cmd 把路径截断，而且 ``Popen`` 是异步的，返回值根本不代表
        命令执行成功。
        """
        if not self._qq_number or not self._napcat_home:
            return False
        try:
            import subprocess

            home = Path(self._napcat_home).expanduser()
            if not home.is_dir():
                return False
            if not (home / self._launcher).is_file():
                return False
            subprocess.Popen(
                ["cmd.exe", "/c", self._launcher, self._qq_number],
                cwd=str(home),
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            return True
        except Exception:
            return False

    def mark_down(self, *, now: float | None = None) -> bool:
        """记一次失败。**刚跨过阈值**时返回 True（只返回一次）。"""
        moment = time.time() if now is None else now
        if self.down_since is None:
            self.down_since = moment
            return False
        age = moment - self.down_since

        # 自动拉一把：只尝试一次，避免反复打断扫码流程。
        if (
            self._auto_restart
            and not self._restart_attempted
            and age >= self._restart_after
        ):
            self._restart_attempted = True
            if self._restart_napcat():
                self._restarted = True

        if self.alerted:
            return False
        if age < self.threshold:
            return False

        self.alerted = True
        minutes = age / 60
        self._write(
            {
                "since": self.down_since,
                "alerted_at": moment,
                "minutes": round(minutes, 1),
                "auto_restarted": self._restarted,
                "reason": "连不上 NapCat：QQ 可能掉登录，需要人工处理",
            }
        )
        try:
            extra = "（已尝试自动重启 NapCat）" if self._restarted else ""
            self._notify(
                "QQ 掉线了",
                f"已经 {minutes:.0f} 分钟连不上 NapCat。{extra}"
                "大概率是 QQ 掉登录，去点一下登录就好。",
            )
        except Exception:
            pass
        return True

    def mark_up(self) -> float:
        """恢复。返回这轮掉了多少秒（0 表示本来就好的）。"""
        if self.down_since is None:
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            return 0.0
        gap = max(0.0, time.time() - self.down_since)
        self.down_since = None
        self.alerted = False
        self._restart_attempted = False
        self._restarted = False
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass
        return gap

    def _write(self, payload: dict[str, Any]) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(
                json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8"
            )
        except OSError:
            pass


__all__ = [
    "ALERT_NAME",
    "AUTO_RESTART_MIN",
    "DEFAULT_LAUNCHER",
    "DEFAULT_THRESHOLD_MIN",
    "Watchdog",
    "alert_path",
]
