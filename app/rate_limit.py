"""滑动窗口 IP 限流器，用于公开订阅端点 /sub/{uuid}。"""

import time
from collections import deque

from fastapi import HTTPException, Request


class SlidingWindowRateLimiter:
    """基于内存字典的滑动窗口限流器（单进程适用）。"""

    def __init__(self, max_requests: int = 30, window_seconds: int = 60) -> None:
        self._windows: dict[str, deque[float]] = {}
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def update_limits(self, max_requests: int, window_seconds: int = 60) -> None:
        """运行时动态调整限流参数（保存设置后调用）。"""
        self.max_requests = max_requests
        self.window_seconds = window_seconds

    def check(self, ip: str) -> None:
        """
        检查 ip 是否超出限流阈值。
        超出时抛出 HTTP 429；否则记录本次请求时间戳。
        """
        now = time.monotonic()
        window = self._windows.setdefault(ip, deque())
        cutoff = now - self.window_seconds
        while window and window[0] < cutoff:
            window.popleft()
        if len(window) >= self.max_requests:
            raise HTTPException(
                status_code=429,
                detail="请求过于频繁，请稍后再试",
                headers={"Retry-After": str(self.window_seconds)},
            )
        window.append(now)

    def _get_client_ip(self, request: Request) -> str:
        """优先取 X-Forwarded-For 第一项，其次取直连 IP。"""
        xff = request.headers.get("X-Forwarded-For", "").strip()
        if xff:
            return xff.split(",")[0].strip()
        xri = request.headers.get("X-Real-IP", "").strip()
        if xri:
            return xri
        return request.client.host if request.client else "unknown"

    def check_request(self, request: Request) -> None:
        """从 Request 自动提取 IP 并执行限流检查。"""
        ip = self._get_client_ip(request)
        self.check(ip)


# 全局单例：公开订阅端点限流器，默认 60 秒内最多 30 次
sub_rate_limiter = SlidingWindowRateLimiter(max_requests=30, window_seconds=60)
