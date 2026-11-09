"""NapCat 掉线看门狗：超过阈值报警，恢复后报告。

为什么需要它：NapCat 注入后 QQ 不显示登录窗口。一旦 QQ 掉登录，
服务会安静地每 30 秒重试，谁都不知道——今早 8:58 的消息就是这么丢的。

报警走本地（没有邮件配置）：
1. 写 `data/qq/ALERT.json`，机器可读；
2. 弹一个 Windows 托盘气泡；
3. 恢复后在日志里记一行「掉了 X 分钟」，以后聊天时能提。
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

DEFAULT_THRESHOLD_MIN = 10.0
ALERT_NAME = "ALERT.json"


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
    """跟踪「一直连不上」的时长，只在跨过阈值那一刻报一次。"""

    def __init__(
        self,
        path: Path,
        *,
        threshold_min: float = DEFAULT_THRESHOLD_MIN,
        notify: Any = None,
    ) -> None:
        self.path = path
        self.threshold = max(0.0, float(threshold_min)) * 60
        self._notify = notify if notify is not None else _balloon
        self.down_since: float | None = None
        self.alerted = False

    @property
    def down_seconds(self) -> float:
        if self.down_since is None:
            return 0.0
        return max(0.0, time.time() - self.down_since)

    def mark_down(self, *, now: float | None = None) -> bool:
        """记一次失败。**刚跨过阈值**时返回 True（只返回一次）。"""
        moment = time.time() if now is None else now
        if self.down_since is None:
            self.down_since = moment
            return False
        if self.alerted:
            return False
        if moment - self.down_since < self.threshold:
            return False

        self.alerted = True
        minutes = (moment - self.down_since) / 60
        self._write(
            {
                "since": self.down_since,
                "alerted_at": moment,
                "minutes": round(minutes, 1),
                "reason": "连不上 NapCat：QQ 可能掉登录，需要人工点一下",
            }
        )
        try:
            self._notify(
                "QQ 掉线了",
                f"已经 {minutes:.0f} 分钟连不上 NapCat。大概率是 QQ 掉登录，"
                "去点一下登录就好。",
            )
        except Exception:
            pass
        return True

    def mark_up(self) -> float:
        """恢复。返回这轮掉了多少秒（0 表示本来就好的）。"""
        if self.down_since is None:
            # 上个进程留下的报警文件：这轮掉线发生在进程重启之前，新进程
            # 没有 down_since，不主动清就会一直挂着（2026-10-30 踩过）。
            try:
                self.path.unlink(missing_ok=True)
            except OSError:
                pass
            return 0.0
        gap = max(0.0, time.time() - self.down_since)
        self.down_since = None
        self.alerted = False
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


__all__ = ["ALERT_NAME", "DEFAULT_THRESHOLD_MIN", "Watchdog", "alert_path"]
