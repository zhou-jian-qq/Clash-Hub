"""通知调度器：从 Setting 读取启用渠道，并发发送。"""

from __future__ import annotations

import asyncio
import logging

from notify.base import Notifier, NotifyLevel

logger = logging.getLogger("notify.dispatcher")


async def build_notifiers_from_settings() -> list[Notifier]:
    """从数据库 Setting 读取通知配置，构造已启用渠道的 Notifier 实例列表。"""
    from database import async_session
    from models import Setting

    async with async_session() as session:

        async def _get(key: str, default: str = "") -> str:
            row = await session.get(Setting, key)
            return row.value if row and row.value else default

        channels_raw = await _get("notify_channels", "")
        channels = {c.strip() for c in channels_raw.split(",") if c.strip()}

    notifiers: list[Notifier] = []

    if "serverchan" in channels:
        from notify.serverchan import ServerChanNotifier
        async with async_session() as session:
            row = await session.get(Setting, "notify_serverchan_key")
            key = row.value if row else ""
        if key:
            notifiers.append(ServerChanNotifier(key))

    if "dingtalk" in channels:
        from notify.dingtalk import DingTalkNotifier
        async with async_session() as session:

            async def _g(k: str) -> str:
                r = await session.get(Setting, k)
                return r.value if r and r.value else ""

            url = await _g("notify_dingtalk_url")
            secret = await _g("notify_dingtalk_secret")
        if url:
            notifiers.append(DingTalkNotifier(url, secret))

    if "wecom" in channels:
        from notify.wecom import WeComNotifier
        async with async_session() as session:
            row = await session.get(Setting, "notify_wecom_url")
            url = row.value if row and row.value else ""
        if url:
            notifiers.append(WeComNotifier(url))

    if "bark" in channels:
        from notify.bark import BarkNotifier
        async with async_session() as session:

            async def _bg(k: str, d: str = "") -> str:
                r = await session.get(Setting, k)
                return r.value if r and r.value else d

            device_key = await _bg("notify_bark_key")
            server_url = await _bg("notify_bark_server", "https://api.day.app")
            group = await _bg("notify_bark_group", "Clash Hub")
        if device_key:
            notifiers.append(BarkNotifier(device_key, server_url, group))

    return notifiers


async def dispatch_notification(title: str, body: str, level: NotifyLevel = "info") -> dict:
    """并发向所有启用渠道发送通知，返回各渠道发送结果。"""
    notifiers = await build_notifiers_from_settings()
    if not notifiers:
        logger.debug("无已配置的通知渠道，跳过")
        return {}

    results = await asyncio.gather(
        *[n.send(title, body, level) for n in notifiers],
        return_exceptions=True,
    )
    return {
        n.channel_id: (r if not isinstance(r, Exception) else str(r))
        for n, r in zip(notifiers, results)
    }
