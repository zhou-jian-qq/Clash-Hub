"""Server酱通知（sctapi.ftqq.com）。"""

from __future__ import annotations

import logging

import httpx

from notify.base import Notifier, NotifyLevel

logger = logging.getLogger("notify.serverchan")


class ServerChanNotifier(Notifier):
    channel_id = "serverchan"

    def __init__(self, sckey: str) -> None:
        self.sckey = sckey.strip()

    async def send(self, title: str, body: str, level: NotifyLevel = "info") -> bool:
        if not self.sckey:
            logger.warning("Server酱 SCKEY 未配置")
            return False
        url = f"https://sctapi.ftqq.com/{self.sckey}.send"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, data={"title": title, "desp": body})
                data = resp.json()
                if data.get("code") == 0:
                    return True
                logger.warning("Server酱发送失败: %s", data)
                return False
        except Exception as e:
            logger.error("Server酱请求异常: %s", e)
            return False
