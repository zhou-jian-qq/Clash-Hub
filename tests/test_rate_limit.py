"""
测试 rate_limit.py 的滑动窗口限流逻辑。
"""
import sys
import time
from pathlib import Path

_APP = Path(__file__).resolve().parent.parent / "app"
if str(_APP) not in sys.path:
    sys.path.insert(0, str(_APP))

import pytest
from fastapi import HTTPException

from rate_limit import SlidingWindowRateLimiter


class TestSlidingWindowRateLimiter:
    def test_allows_under_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=5, window_seconds=60)
        for _ in range(5):
            limiter.check("1.2.3.4")  # should not raise

    def test_blocks_over_limit(self):
        limiter = SlidingWindowRateLimiter(max_requests=3, window_seconds=60)
        for _ in range(3):
            limiter.check("1.2.3.4")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("1.2.3.4")
        assert exc_info.value.status_code == 429

    def test_different_ips_independent(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        limiter.check("1.1.1.1")
        limiter.check("1.1.1.1")
        with pytest.raises(HTTPException):
            limiter.check("1.1.1.1")
        # Different IP should still be allowed
        limiter.check("2.2.2.2")

    def test_window_expiry(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=1)
        limiter.check("1.2.3.4")
        limiter.check("1.2.3.4")
        with pytest.raises(HTTPException):
            limiter.check("1.2.3.4")
        time.sleep(1.1)
        # After window expires, should be allowed again
        limiter.check("1.2.3.4")

    def test_update_limits(self):
        limiter = SlidingWindowRateLimiter(max_requests=2, window_seconds=60)
        limiter.check("1.2.3.4")
        limiter.check("1.2.3.4")
        with pytest.raises(HTTPException):
            limiter.check("1.2.3.4")

        # Reset and increase limit
        new_limiter = SlidingWindowRateLimiter(max_requests=10, window_seconds=60)
        new_limiter.update_limits(max_requests=20)
        assert new_limiter.max_requests == 20

    def test_429_has_retry_after_header(self):
        limiter = SlidingWindowRateLimiter(max_requests=1, window_seconds=30)
        limiter.check("1.2.3.4")
        with pytest.raises(HTTPException) as exc_info:
            limiter.check("1.2.3.4")
        assert "Retry-After" in exc_info.value.headers
