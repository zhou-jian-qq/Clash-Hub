"""
定时任务模块
- 每 6 小时自动刷新所有订阅流量数据
- 自动禁用流量耗尽或已过期的订阅
"""

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import async_session
from models import Subscription, Setting
from aggregator import fetch_subscription_content, parse_proxies

logger = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()


async def get_setting(key: str, default: str = "") -> str:
    async with async_session() as session:
        result = await session.get(Setting, key)
        return result.value if result else default


async def refresh_subscriptions():
    """刷新所有启用订阅的流量数据和节点数"""
    logger.info("开始刷新订阅数据...")
    async with async_session() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.enabled == True)  # noqa: E712
        )
        subs = result.scalars().all()

        auto_disable_expiry = (await get_setting("auto_disable_on_expiry", "true")) == "true"
        auto_disable_empty = (await get_setting("auto_disable_on_empty", "true")) == "true"
        timeout = int(await get_setting("fetch_timeout", "15"))

        for sub in subs:
            try:
                content, userinfo = await fetch_subscription_content(sub.url, timeout)
                proxies = parse_proxies(content)

                sub.used = userinfo.get("used", sub.used)
                sub.total = userinfo.get("total", sub.total)
                sub.expire = userinfo.get("expire", sub.expire)
                sub.node_count = len(proxies)
                sub.last_sync = datetime.now(timezone.utc)

                if sub.auto_disable:
                    now_ts = int(time.time())
                    if auto_disable_expiry and sub.expire > 0 and sub.expire < now_ts:
                        sub.enabled = False
                        logger.info("订阅 [%s] 已过期, 自动禁用", sub.name)
                    elif auto_disable_empty and sub.total > 0 and sub.used >= sub.total:
                        sub.enabled = False
                        logger.info("订阅 [%s] 流量耗尽, 自动禁用", sub.name)

                logger.info("刷新 [%s] 成功: %d 节点", sub.name, len(proxies))
            except Exception as e:
                logger.warning("刷新 [%s] 失败: %s", sub.name, e)

        await session.commit()
    logger.info("订阅数据刷新完成")


def start_scheduler():
    scheduler.add_job(refresh_subscriptions, "interval", hours=6, id="refresh_subs",
                      replace_existing=True, next_run_time=None)
    scheduler.start()
    logger.info("定时任务已启动 (每6小时刷新)")


def stop_scheduler():
    scheduler.shutdown(wait=False)
