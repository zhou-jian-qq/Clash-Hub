"""Bark iOS 推送通知。

API 文档: https://bark.day.app/#/tutorial
"""

from __future__ import annotations

import logging
import urllib.parse

import httpx

from notify.base import Notifier, NotifyLevel

logger = logging.getLogger("notify.bark")

_LEVEL_MAP: dict[NotifyLevel, str] = {
    "info": "passive",
    "warning": "active",
    "critical": "critical",
}


class BarkNotifier(Notifier):
    channel_id = "bark"

    def __init__(
        self,
        device_key: str,
        server_url: str = "https://api.day.app",
        group: str = "Clash Hub",
    ) -> None:
        self.device_key = device_key.strip()
        self.server_url = server_url.rstrip("/")
        self.group = group

    async def send(self, title: str, body: str, level: NotifyLevel = "info") -> bool:
        if not self.device_key:
            logger.warning("Bark device key 未配置")
            return False

        bark_level = _LEVEL_MAP.get(level, "active")
        payload: dict = {
            "title": title,
            "body": body,
            "level": bark_level,
            "group": self.group,
        }
        if level == "critical":
            payload["sound"] = "alarm"

        url = f"{self.server_url}/{urllib.parse.quote(self.device_key, safe='')}"
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("code") == 200:
                    return True
                logger.warning("Bark 发送失败: %s", data)
                return False
        except Exception as e:
            logger.error("Bark 请求异常: %s", e)
            return False
