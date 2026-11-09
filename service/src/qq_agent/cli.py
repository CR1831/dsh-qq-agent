"""Command line entry point for the QQ-only agent."""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path
from typing import Any

from . import __version__
from .adapters import build_adapters
from .adapters.qq_onebot import QQOneBotAdapter
from .agent import QQAgent
from .config import Config, ensure_dirs, load_config
from .persona_card import PersonaError, load_persona
from .qq_dm import QQReplier
from .status import heartbeat, initial_status, status_path, write_status
from .store import Store


def _print_status(cfg: Config, card: Any | None = None) -> None:
    print(f"qq-agent {__version__}")
    print(f"home      : {cfg.home}")
    print(f"config    : {Path(cfg.home) / 'config.toml'}")
    print(f"persona   : {cfg.qq.persona_file}")
    print(f"data      : {cfg.qq.data_dir}")
    print(f"QQ 接入   : {'已启用' if cfg.qq.enabled else '未启用（[qq].enabled = false）'}")
    print(f"dry_run   : {'开（只出草稿，不发送）' if cfg.qq.dry_run else '关（会真的发送）'}")
    print(f"白名单    : 好友 {len(cfg.qq.friend_whitelist)} 人 / 群 {len(cfg.qq.group_whitelist)} 个")
    print(f"主动发送  : {'允许' if cfg.qq.proactive else '不允许（只被动回复）'}")
    if card is not None:
        print(f"人格卡    : 已加载 {card.name}（{card.format}，{card.chars} 字）")


def _redirect_log(path: str) -> None:
    target = Path(path).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True)
    handle = target.open("a", encoding="utf-8", buffering=1)
    sys.stdout = handle
    sys.stderr = handle


def _load_card(cfg: Config, allow_missing: bool) -> Any | None:
    try:
        return load_persona(cfg)
    except PersonaError as exc:
        if allow_missing:
            print(f"[warn] {exc}（已用 --allow-no-persona 继续，仅用于测试）", file=sys.stderr)
            return None
        print(f"人格卡错误：{exc}", file=sys.stderr)
        print(
            "把人格卡放到 "
            f"{cfg.qq.persona_file}，或修改 config.toml 里的 [qq].persona_file。",
            file=sys.stderr,
        )
        raise SystemExit(2)


async def _listen(cfg: Config, *, timeout: float | None, allow_missing: bool) -> int:
    ensure_dirs(cfg)
    if not cfg.qq.enabled:
        print("QQ 未启用：把 config.toml 里的 [qq].enabled 改成 true", file=sys.stderr)
        return 2
    if not cfg.qq.friend_whitelist:
        print("[warn] friend_whitelist 为空：不会回复任何私聊", file=sys.stderr)
    if not cfg.qq.owner_uid:
        print("[warn] owner_uid 未配置：号主专属工具不可用", file=sys.stderr)
    if cfg.qq.owner_uid and cfg.qq.owner_uid == cfg.qq.self_id:
        print("[warn] owner_uid 和 self_id 相同：号主校验会失效", file=sys.stderr)

    card = _load_card(cfg, allow_missing)
    adapters = build_adapters(cfg)
    store = Store(cfg.server.db_path)
    agent = QQAgent(cfg, store, adapters)
    replier = QQReplier(cfg, store, agent, adapters)

    path = status_path(cfg.home)
    state = initial_status(cfg, card)
    state["dryRun"] = bool(cfg.qq.dry_run)
    write_status(path, state)

    stop = asyncio.Event()
    hb_task = asyncio.create_task(heartbeat(path, state, stop=stop))

    def on_state(connected: bool) -> None:
        state["ws"] = bool(connected)
        try:
            write_status(path, state)
        except OSError:
            pass

    code = 0
    try:
        print(
            f"[qq] 启动：dry_run={cfg.qq.dry_run}，"
            f"好友白名单 {len(cfg.qq.friend_whitelist)} 人，"
            f"人格卡 {card.name if card else '（无）'}"
        )
        await replier.listen(timeout=timeout, on_state=on_state)
    except KeyboardInterrupt:
        code = 0
    except Exception as exc:
        state["lastError"] = f"{type(exc).__name__}: {exc}"
        print(f"[qq] 监听失败：{type(exc).__name__}: {exc}", file=sys.stderr)
        code = 1
    finally:
        stop.set()
        hb_task.cancel()
        try:
            await hb_task
        except (asyncio.CancelledError, Exception):
            pass
        state["ws"] = False
        try:
            write_status(path, state)
        except OSError:
            pass
        await agent.close()
        for adapter in adapters.values():
            close = getattr(adapter, "close", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass
        store.close()
    return code


async def _health(cfg: Config) -> int:
    if not cfg.qq.enabled:
        print("QQ 未启用（[qq].enabled = false）")
        return 2
    adapter = QQOneBotAdapter(cfg.qq)
    try:
        info = await adapter.api("get_login_info")
        friends = await adapter.api("get_friend_list")
        print("QQ 健康检查")
        print(f"  登录      : {info.get('user_id')} / {info.get('nickname')}")
        print(f"  好友      : {len(friends or [])} 个")
        owner = str(cfg.qq.owner_uid or "")
        if owner:
            present = any(str(f.get("user_id")) == owner for f in friends or [])
            print(f"  号主在好友: {'是' if present else '否（检查 owner_uid）'}")
            if not present:
                return 1
        else:
            print("  号主      : 未配置 owner_uid")
        return 0
    except Exception as exc:
        print(f"QQ 健康检查失败：{type(exc).__name__}: {exc}")
        return 1
    finally:
        await adapter.close()


async def _doctor(cfg: Config) -> int:
    checks: list[tuple[str, bool, str]] = []

    def add(name: str, ok: bool, detail: str = "") -> None:
        checks.append((name, ok, detail))

    add("config.toml", (Path(cfg.home) / "config.toml").is_file(), str(Path(cfg.home) / "config.toml"))
    add("QQ enabled", bool(cfg.qq.enabled), "[qq].enabled")
    add("owner_uid", bool(cfg.qq.owner_uid), "必须填号主 QQ 号")
    if cfg.qq.owner_uid and cfg.qq.self_id:
        add("owner != self", cfg.qq.owner_uid != cfg.qq.self_id, "owner_uid 不能等于 self_id")
    add("friend_whitelist", bool(cfg.qq.friend_whitelist), "空 = 不回复任何私聊")
    if cfg.qq.owner_uid:
        add(
            "owner in whitelist",
            int(cfg.qq.owner_uid) in {int(x) for x in cfg.qq.friend_whitelist},
            "号主也要放进 friend_whitelist",
        )

    try:
        card = load_persona(cfg)
        add("persona", True, f"{card.name} / {card.format} / {card.chars} 字")
    except PersonaError as exc:
        add("persona", False, str(exc))

    try:
        import websockets  # noqa: F401

        add("websockets", True, "已安装")
    except ImportError:
        add("websockets", False, "pip install websockets")

    adapter = QQOneBotAdapter(cfg.qq)
    try:
        info = await adapter.api("get_login_info")
        add("NapCat HTTP", True, f"{info.get('user_id')} / {info.get('nickname')}")
    except Exception as exc:
        add("NapCat HTTP", False, f"{type(exc).__name__}: {exc}")

    try:
        import websockets

        async with websockets.connect(cfg.qq.ws_url, open_timeout=5) as ws:
            await ws.close()
        add("NapCat WS", True, cfg.qq.ws_url)
    except Exception as exc:
        add("NapCat WS", False, f"{type(exc).__name__}: {exc}")
    finally:
        await adapter.close()

    print(f"qq-agent doctor（{cfg.home}）")
    failed = 0
    for name, ok, detail in checks:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            failed += 1
        print(f"  [{mark}] {name:<20} {detail}")
    print(f"\n{len(checks) - failed}/{len(checks)} 项通过")
    return 0 if failed == 0 else 1


async def _profiles(cfg: Config, count: int) -> int:
    from . import qq_profile

    adapters = build_adapters(cfg)
    adapter = adapters.get("qq")
    if adapter is None:
        print("QQ 适配器未启用")
        return 1
    store = Store(cfg.server.db_path)
    agent = QQAgent(cfg, store, adapters)
    path = qq_profile.profile_path(cfg.server.db_path)
    profiles = qq_profile.load(path)
    self_id = str(cfg.qq.self_id or "")
    owner = str(cfg.qq.owner_uid or "")
    count = max(10, min(int(count), 200))

    try:
        friends = await adapter.api("get_friend_list") or []
        targets = [f for f in friends if str(f.get("user_id")) != self_id]
        print(f"共 {len(targets)} 个好友，逐个拉取最近 {count} 条…")
        for friend in targets:
            uid = str(friend.get("user_id"))
            name = str(friend.get("remark") or friend.get("nickname") or uid)
            channel = f"private:{uid}"
            try:
                payload = await adapter.api("get_friend_msg_history", user_id=int(uid), count=count)
            except Exception as exc:
                print(f"  {name:<12} 拉历史失败：{type(exc).__name__}")
                continue
            messages = (payload.get("messages") if isinstance(payload, dict) else payload) or []
            history: list[dict[str, Any]] = []
            for item in messages:
                if not isinstance(item, dict):
                    continue
                sender_id = str(item.get("user_id") or item.get("sender_id") or "")
                text = ""
                for seg in item.get("message") or []:
                    if isinstance(seg, dict) and seg.get("type") == "text":
                        text += str((seg.get("data") or {}).get("text") or "")
                if text.strip():
                    history.append(
                        {
                            "role": "assistant" if sender_id == self_id else "user",
                            "content": text.strip(),
                            "ts": float(item.get("time") or 0),
                        }
                    )
            stats = qq_profile.stats_from(history)
            if not stats.get("msgs"):
                print(f"  {name:<12} 没有可用的文字记录，跳过")
                continue
            entry = profiles.setdefault(channel, {})
            entry.update(
                {
                    "name": name,
                    "nickname": str(friend.get("nickname") or ""),
                    "remark": str(friend.get("remark") or ""),
                    "uid": uid,
                    "is_owner": uid == owner,
                    "stats": stats,
                }
            )
            if cfg.qq.use_profile_facts:
                try:
                    info = await adapter.api("get_stranger_info", user_id=int(uid)) or {}
                except Exception:
                    info = {}
                for key in ("age", "sex", "college", "province", "city",
                            "birthday_year", "birthday_month", "birthday_day"):
                    value = info.get(key) if isinstance(info, dict) else None
                    if value not in (None, "", 0):
                        entry[key] = value
            impression = ""
            if stats.get("msgs", 0) >= 3:
                impression = await qq_profile.build_impression(history, agent.llm)
            if impression:
                entry["impression"] = impression
                entry["updated"] = time.time()
            print(f"  {name:<12} {stats.get('msgs')} 句 · 平均 {stats.get('avg_len')} 字")
        qq_profile.save(path, profiles)
        print(f"已写入 {path}")
        return 0
    finally:
        await agent.close()
        for adapter in adapters.values():
            close = getattr(adapter, "close", None)
            if close:
                try:
                    await close()
                except Exception:
                    pass
        store.close()


async def _stickers(cfg: Config) -> int:
    from . import qq_sticker
    from .vision import Vision

    adapter = build_adapters(cfg).get("qq")
    if adapter is None:
        print("QQ 适配器未启用")
        return 1
    vision = Vision(cfg.vision, cfg.agent)
    path = qq_sticker.store_path(cfg.server.db_path)
    print("正在同步收藏表情（第一次会逐张读图，稍慢）…")
    result = await qq_sticker.sync(adapter, vision, path)
    await adapter.close()
    if not result.get("ok"):
        print(f"同步失败：{result.get('error')}")
        return 1
    print(f"新增 {result.get('added')} 张 · 跳过 {result.get('skipped')} 张")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="qq-agent")
    parser.add_argument("--config", default="", help="config.toml 路径（默认 <home>/config.toml）")
    parser.add_argument("--version", action="version", version=f"qq-agent {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    qq = sub.add_parser("qq", help="QQ 服务")
    qq.add_argument("--listen", action="store_true", help="常驻监听（默认动作）")
    qq.add_argument("--health", action="store_true", help="连 NapCat 做一次健康检查")
    qq.add_argument("--profiles", action="store_true", help="刷新好友画像")
    qq.add_argument("--stickers", action="store_true", help="同步收藏表情")
    qq.add_argument("--timeout", type=float, default=None, help="监听多少秒后退出（默认一直跑）")
    qq.add_argument("--count", type=int, default=60, help="画像拉取历史条数")
    qq.add_argument("--log-file", default="", help="把 stdout/stderr 写到这个文件")
    qq.add_argument("--allow-no-persona", action="store_true", help="仅测试：允许没有人格卡")
    qq.add_argument("--no-dry-run", action="store_true", help="覆盖配置：真的发送")

    persona = sub.add_parser("persona", help="人格卡")
    persona.add_argument("--check", action="store_true", help="只校验，不打印卡内容")

    sub.add_parser("doctor", help="环境体检")

    args = parser.parse_args(argv)
    cfg = load_config(args.config or None)
    if args.command == "qq" and getattr(args, "no_dry_run", False):
        cfg.qq.dry_run = False

    if args.command == "persona":
        try:
            card = load_persona(cfg)
        except PersonaError as exc:
            print(f"FAIL：{exc}")
            return 2
        print(f"OK：{card.name} / {card.format} / {card.chars} 字 / {card.source}")
        for warning in card.warnings:
            print(f"警告：{warning}")
        return 0

    if args.command == "doctor":
        return asyncio.run(_doctor(cfg))

    if args.command != "qq":
        parser.print_help()
        return 2

    if getattr(args, "log_file", ""):
        _redirect_log(args.log_file)
    ensure_dirs(cfg)

    if args.health:
        return asyncio.run(_health(cfg))
    if args.profiles:
        return asyncio.run(_profiles(cfg, args.count))
    if args.stickers:
        return asyncio.run(_stickers(cfg))

    if not args.listen:
        try:
            card = load_persona(cfg)
        except PersonaError:
            card = None
        _print_status(cfg, card)
        return 0

    return asyncio.run(
        _listen(cfg, timeout=args.timeout, allow_missing=args.allow_no_persona)
    )


if __name__ == "__main__":
    raise SystemExit(main())
