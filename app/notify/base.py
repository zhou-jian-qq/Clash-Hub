"""通知 Provider 抽象基类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Literal

NotifyLevel = Literal["info", "warning", "critical"]


class Notifier(ABC):
    """所有通知渠道的基类。"""

    channel_id: str = ""

    @abstractmethod
    async def send(self, title: str, body: str, level: NotifyLevel = "info") -> bool:
        """发送通知。返回 True 表示成功，False 表示失败（不抛异常）。"""
        ...
