"""探测历史落库与查询服务。"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Literal

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import async_session
from models import ProbeHistory

logger = logging.getLogger("probe_history")

TargetKind = Literal["sub", "node"]

RETENTION_DAYS = 30


async def record_probe(
    target_kind: TargetKind,
    target_id: int,
    ok: bool,
    latency_ms: float | None,
) -> None:
    """将一次探测结果异步落库（忽略失败，不影响主流程）。"""
    try:
        async with async_session() as session:
            session.add(ProbeHistory(
                target_kind=target_kind,
                target_id=target_id,
                ok=ok,
                latency_ms=latency_ms,
            ))
            await session.commit()
    except Exception as e:
        logger.warning("写入探测历史失败: %s", e)


async def get_probe_history(
    db: AsyncSession,
    target_kind: TargetKind,
    target_id: int,
    days: int = 7,
) -> list[dict]:
    """查询近 N 天的探测历史，按时间升序返回。"""
    since = datetime.now(timezone.utc) - timedelta(days=days)
    result = await db.execute(
        select(ProbeHistory)
        .where(
            ProbeHistory.target_kind == target_kind,
            ProbeHistory.target_id == target_id,
            ProbeHistory.checked_at >= since,
        )
        .order_by(ProbeHistory.checked_at.asc())
    )
    return [r.to_dict() for r in result.scalars().all()]


async def cleanup_old_probe_history() -> int:
    """删除超过 RETENTION_DAYS 天的历史记录，返回删除条数。"""
    cutoff = datetime.now(timezone.utc) - timedelta(days=RETENTION_DAYS)
    try:
        async with async_session() as session:
            result = await session.execute(
                delete(ProbeHistory).where(ProbeHistory.checked_at < cutoff)
            )
            await session.commit()
            deleted = result.rowcount or 0
            if deleted:
                logger.info("已清理 %d 条过期探测历史", deleted)
            return deleted
    except Exception as e:
        logger.warning("清理探测历史失败: %s", e)
        return 0
