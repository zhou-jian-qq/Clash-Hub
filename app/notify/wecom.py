"""企业微信群机器人通知。"""

from __future__ import annotations

import logging

import httpx

from notify.base import Notifier, NotifyLevel

logger = logging.getLogger("notify.wecom")


class WeComNotifier(Notifier):
    channel_id = "wecom"

    def __init__(self, webhook_url: str) -> None:
        self.webhook_url = webhook_url.strip()

    async def send(self, title: str, body: str, level: NotifyLevel = "info") -> bool:
        if not self.webhook_url:
            logger.warning("企业微信 Webhook URL 未配置")
            return False

        at_all = level == "critical"
        content = f"**{title}**\n{body}"
        if at_all:
            content += "\n<@all>"

        payload = {
            "msgtype": "markdown",
            "markdown": {"content": content},
        }
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(self.webhook_url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    return True
                logger.warning("企业微信发送失败: %s", data)
                return False
        except Exception as e:
            logger.error("企业微信请求异常: %s", e)
            return False
