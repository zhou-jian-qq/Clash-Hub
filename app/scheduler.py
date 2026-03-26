"""
定时任务模块
- 按设置的时间间隔自动刷新所有订阅流量数据（Setting: refresh_interval_hours，0 表示关闭）
- 自动禁用流量耗尽或已过期的订阅
"""

import logging
import time
from datetime import datetime, timezone

from sqlalchemy import select
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from database import async_session
from models import Subscription, Setting
from aggregator import fetch_subscription_content, parse_proxies, check_subscription_availability

logger = logging.getLogger("scheduler")
scheduler = AsyncIOScheduler()

REFRESH_JOB_ID = "refresh_subs"
DEFAULT_INTERVAL_HOURS = 6
MAX_INTERVAL_HOURS = 720


def parse_refresh_interval_hours(raw: str) -> int:
    """解析并限制在 0..720；无法解析时回退为 DEFAULT_INTERVAL_HOURS。"""
    try:
        v = int(str(raw).strip())
    except (TypeError, ValueError):
        return DEFAULT_INTERVAL_HOURS
    return max(0, min(MAX_INTERVAL_HOURS, v))


async def get_setting(key: str, default: str = "") -> str:
    async with async_session() as session:
        result = await session.get(Setting, key)
        return result.value if result else default


async def refresh_subscriptions(source: str = "manual"):
    """刷新所有启用订阅的流量数据和节点数"""
    logger.info("开始刷新订阅数据... (来源: %s)", source)
    async with async_session() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.enabled == True)  # noqa: E712
        )
        subs = result.scalars().all()

        auto_disable_expiry = (await get_setting("auto_disable_on_expiry", "true")) == "true"
        auto_disable_empty = (await get_setting("auto_disable_on_empty", "true")) == "true"
        timeout = int(await get_setting("fetch_timeout", "15"))
        mihomo_path = await get_setting("mihomo_path", "")

        for sub in subs:
            try:
                # 获取流量信息
                content, userinfo = await fetch_subscription_content(sub.url, timeout)
                
                # 检查可用性
                check_result = await check_subscription_availability(sub.url, sub.prefix or "", timeout, mihomo_path=mihomo_path)
                
                sub.used = userinfo.get("used", sub.used)
                sub.total = userinfo.get("total", sub.total)
                sub.expire = userinfo.get("expire", sub.expire)
                sub.node_count = check_result["node_count"]
                sub.last_sync = datetime.now(timezone.utc)

                if sub.auto_disable:
                    now_ts = int(time.time())
                    if not check_result["ok"]:
                        sub.enabled = False
                        logger.info("订阅 [%s] 节点获取失败或报错, 自动禁用: %s", sub.name, check_result["message"])
                    elif auto_disable_expiry and sub.expire > 0 and sub.expire < now_ts:
                        sub.enabled = False
                        logger.info("订阅 [%s] 已过期, 自动禁用", sub.name)
                    elif auto_disable_empty and sub.total > 0 and sub.used >= sub.total:
                        sub.enabled = False
                        logger.info("订阅 [%s] 流量耗尽, 自动禁用", sub.name)

                logger.info("刷新 [%s] 成功: %d 节点", sub.name, check_result["node_count"])
            except Exception as e:
                logger.warning("刷新 [%s] 失败: %s", sub.name, e)
                if sub.auto_disable:
                    sub.enabled = False
                    logger.info("订阅 [%s] 获取异常, 自动禁用: %s", sub.name, e)

        await session.commit()
    logger.info("订阅数据刷新完成")


async def apply_refresh_interval_job():
    """根据数据库中的 refresh_interval_hours 注册、移除或重调度定时任务。"""
    raw = await get_setting("refresh_interval_hours", str(DEFAULT_INTERVAL_HOURS))
    hours = parse_refresh_interval_hours(raw)
    job = scheduler.get_job(REFRESH_JOB_ID)

    if hours <= 0:
        if job:
            scheduler.remove_job(REFRESH_JOB_ID)
            logger.info("已关闭定时刷新订阅")
        return

    if job is None:
        scheduler.add_job(
            refresh_subscriptions,
            "interval",
            hours=hours,
            id=REFRESH_JOB_ID,
            replace_existing=True,
            next_run_time=datetime.now(timezone.utc),
            kwargs={"source": "cron_job"}
        )
        logger.info("定时任务已注册 (每 %d 小时)", hours)
    else:
        scheduler.reschedule_job(REFRESH_JOB_ID, trigger="interval", hours=hours)
        logger.info("定时任务已重调度 (每 %d 小时)", hours)


async def start_scheduler():
    await apply_refresh_interval_job()
    if not scheduler.running:
        scheduler.start()
    raw = await get_setting("refresh_interval_hours", str(DEFAULT_INTERVAL_HOURS))
    h = parse_refresh_interval_hours(raw)
    if h > 0:
        logger.info("调度器已启动 (订阅自动刷新每 %d 小时)", h)
    else:
        logger.info("调度器已启动 (订阅自动刷新已关闭)")


async def reschedule_refresh_job():
    """保存设置后调用，使新间隔立即生效。"""
    await apply_refresh_interval_job()


def stop_scheduler():
    scheduler.shutdown(wait=False)
