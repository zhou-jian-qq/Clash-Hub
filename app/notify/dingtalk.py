"""钉钉群机器人通知（自定义机器人 + 加签）。"""

from __future__ import annotations

import hashlib
import hmac
import base64
import logging
import time
import urllib.parse

import httpx

from notify.base import Notifier, NotifyLevel

logger = logging.getLogger("notify.dingtalk")


class DingTalkNotifier(Notifier):
    channel_id = "dingtalk"

    def __init__(self, webhook_url: str, secret: str = "") -> None:
        self.webhook_url = webhook_url.strip()
        self.secret = secret.strip()

    def _sign(self) -> dict:
        """生成加签参数（timestamp + sign），未设置 secret 时返回空 dict。"""
        if not self.secret:
            return {}
        timestamp = str(round(time.time() * 1000))
        string_to_sign = f"{timestamp}\n{self.secret}"
        hmac_code = hmac.new(
            self.secret.encode("utf-8"),
            string_to_sign.encode("utf-8"),
            digestmod=hashlib.sha256,
        ).digest()
        sign = urllib.parse.quote_plus(base64.b64encode(hmac_code))
        return {"timestamp": timestamp, "sign": sign}

    async def send(self, title: str, body: str, level: NotifyLevel = "info") -> bool:
        if not self.webhook_url:
            logger.warning("钉钉 Webhook URL 未配置")
            return False
        extra = self.secret and self._sign()
        url = self.webhook_url
        if extra:
            url += f"&timestamp={extra['timestamp']}&sign={extra['sign']}"

        at_all = level == "critical"
        content = f"**{title}**\n\n{body}"
        payload: dict = {
            "msgtype": "markdown",
            "markdown": {"title": title, "text": content},
        }
        if at_all:
            payload["at"] = {"isAtAll": True}

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.post(url, json=payload)
                data = resp.json()
                if data.get("errcode") == 0:
                    return True
                logger.warning("钉钉发送失败: %s", data)
                return False
        except Exception as e:
            logger.error("钉钉请求异常: %s", e)
            return False
