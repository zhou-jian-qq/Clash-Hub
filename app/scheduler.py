"""
定时任务模块
- 按设置的时间间隔自动刷新所有订阅流量数据（Setting: refresh_interval_hours，0 表示关闭）
- 自动禁用流量耗尽或已过期的订阅
- 流量/到期/失败时触发通知（Phase 3.1）
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
    """调度器独立会话内读取 Setting（不经过 FastAPI get_db）。"""
    async with async_session() as session:
        result = await session.get(Setting, key)
        return result.value if result else default


async def _should_notify(session, key: str) -> bool:
    """判断是否需要发送通知（防重：当天内同一 key 只发一次）。"""
    row = await session.get(Setting, key)
    if not row or not row.value:
        return True
    try:
        last_ts = int(row.value)
        return time.time() - last_ts > 86400
    except ValueError:
        return True


async def _mark_notified(session, key: str) -> None:
    row = await session.get(Setting, key)
    ts = str(int(time.time()))
    if row:
        row.value = ts
    else:
        session.add(Setting(key=key, value=ts))


async def refresh_subscriptions(source: str = "manual", progress_callback=None):
    """刷新所有启用订阅的流量数据和节点数，并按策略触发告警通知。"""
    logger.info("开始刷新订阅数据... (来源: %s)", source)
    try:
        from notify.dispatcher import dispatch_notification
        notify_available = True
    except ImportError:
        notify_available = False

    async with async_session() as session:
        result = await session.execute(
            select(Subscription).where(Subscription.enabled == True)  # noqa: E712
        )
        subs = result.scalars().all()

        auto_disable_expiry = (await get_setting("auto_disable_on_expiry", "true")) == "true"
        auto_disable_empty = (await get_setting("auto_disable_on_empty", "true")) == "true"
        timeout = int(await get_setting("fetch_timeout", "30"))
        mihomo_path = await get_setting("mihomo_path", "")

        for sub in subs:
            try:
                content, userinfo = await fetch_subscription_content(sub.url, timeout)
                check_result = await check_subscription_availability(sub.url, sub.prefix or "", timeout, mihomo_path=mihomo_path)

                sub.used = userinfo.get("used", sub.used)
                sub.total = userinfo.get("total", sub.total)
                sub.expire = userinfo.get("expire", sub.expire)
                sub.node_count = check_result["node_count"]
                sub.last_sync = datetime.now(timezone.utc)

                now_ts = int(time.time())

                if sub.auto_disable:
                    if not check_result["ok"]:
                        sub.enabled = False
                        err_detail = check_result.get("error") or check_result.get("message") or ""
                        logger.info("订阅 [%s] 节点获取失败或报错, 自动禁用", sub.name)
                        # 连续失败通知（critical）
                        if notify_available:
                            notify_key = f"notify_last_fail_{sub.id}"
                            if await _should_notify(session, notify_key):
                                await dispatch_notification(
                                    title=f"订阅失败：{sub.name}",
                                    body=f"订阅 [{sub.name}] 无法获取节点。\n错误：{err_detail}",
                                    level="critical",
                                )
                                await _mark_notified(session, notify_key)
                    elif auto_disable_expiry and sub.expire > 0 and sub.expire < now_ts:
                        sub.enabled = False
                        logger.info("订阅 [%s] 已过期, 自动禁用", sub.name)
                    elif auto_disable_empty and sub.total > 0 and sub.used >= sub.total:
                        sub.enabled = False
                        logger.info("订阅 [%s] 流量耗尽, 自动禁用", sub.name)

                # 流量告警（剩余 < 10%）
                if notify_available and sub.total > 0:
                    remaining_ratio = 1 - sub.used / sub.total
                    if remaining_ratio < 0.1:
                        notify_key = f"notify_last_traffic_{sub.id}"
                        if await _should_notify(session, notify_key):
                            pct = int(remaining_ratio * 100)
                            await dispatch_notification(
                                title=f"流量告警：{sub.name}",
                                body=f"订阅 [{sub.name}] 流量剩余仅 {pct}%，请注意续费。",
                                level="warning",
                            )
                            await _mark_notified(session, notify_key)

                # 到期告警（3 天内）
                if notify_available and sub.expire > 0:
                    days_left = (sub.expire - now_ts) / 86400
                    if 0 < days_left <= 3:
                        notify_key = f"notify_last_expire_{sub.id}"
                        if await _should_notify(session, notify_key):
                            await dispatch_notification(
                                title=f"到期提醒：{sub.name}",
                                body=f"订阅 [{sub.name}] 将在 {days_left:.1f} 天后到期，请及时续费。",
                                level="warning",
                            )
                            await _mark_notified(session, notify_key)

                logger.info("刷新 [%s] 成功: %d 节点", sub.name, check_result["node_count"])
                if progress_callback:
                    try:
                        await progress_callback({
                            "sub_id": sub.id,
                            "name": sub.name,
                            "ok": check_result["ok"],
                            "node_count": check_result["node_count"],
                            "latency_ms": check_result.get("latency_ms"),
                        })
                    except Exception:
                        pass
                # 写入探测历史
                try:
                    from services.probe_history_service import record_probe
                    await record_probe(
                        target_kind="sub",
                        target_id=sub.id,
                        ok=check_result["ok"],
                        latency_ms=check_result.get("latency_ms"),
                    )
                except Exception:
                    pass
            except Exception as e:
                logger.exception("刷新 [%s] 失败: %s", sub.name, e)
                if sub.auto_disable:
                    sub.enabled = False
                    logger.info(
                        "订阅 [%s] 获取异常, 自动禁用: %r (type=%s)",
                        sub.name, e, type(e).__name__,
                    )

        await session.commit()

        # 刷新后失效配置缓存
        try:
            from services.config_cache import config_cache
            config_cache.invalidate_all()
        except ImportError:
            pass

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


CLEANUP_HISTORY_JOB_ID = "cleanup_probe_history"


async def _cleanup_probe_history_job():
    """每日清理超期探测历史。"""
    try:
        from services.probe_history_service import cleanup_old_probe_history
        await cleanup_old_probe_history()
    except Exception as e:
        logger.warning("探测历史清理任务异常: %s", e)


async def start_scheduler():
    await apply_refresh_interval_job()
    if not scheduler.running:
        scheduler.start()
    # 每天凌晨 3 点清理历史
    if not scheduler.get_job(CLEANUP_HISTORY_JOB_ID):
        scheduler.add_job(
            _cleanup_probe_history_job,
            "cron",
            hour=3,
            id=CLEANUP_HISTORY_JOB_ID,
            replace_existing=True,
        )
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
