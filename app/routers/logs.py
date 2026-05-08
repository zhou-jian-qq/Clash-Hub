"""访问日志路由（订阅访问日志、审计日志、探测历史）。"""

from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select, text, and_
from sqlalchemy.ext.asyncio import AsyncSession

from aggregator import aggregate_traffic
from auth import require_admin
from database import get_db
from models import AuditLog, SubAccessLog, Subscription
from services.probe_history_service import get_probe_history

router = APIRouter()


@router.get("/api/traffic")
async def get_traffic(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Subscription))
    subs = [s.to_dict() for s in result.scalars().all()]
    return aggregate_traffic(subs)


@router.get("/api/sub-access-logs")
async def list_sub_access_logs(
    page: int = 1,
    page_size: int = 50,
    ip: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    conditions = []
    if ip and ip.strip():
        pat = f"%{ip.strip()}%"
        conditions.append((SubAccessLog.ip.like(pat)) | (SubAccessLog.real_ip.like(pat)))
    if date_from and date_from.strip():
        try:
            dt_from = datetime.strptime(date_from.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            conditions.append(SubAccessLog.accessed_at >= dt_from)
        except ValueError:
            pass
    if date_to and date_to.strip():
        try:
            dt_to = datetime.strptime(date_to.strip(), "%Y-%m-%d").replace(tzinfo=timezone.utc)
            dt_to = dt_to + timedelta(days=1)
            conditions.append(SubAccessLog.accessed_at < dt_to)
        except ValueError:
            pass

    base_q = select(SubAccessLog)
    count_q = select(func.count()).select_from(SubAccessLog)
    if conditions:
        combined = and_(*conditions)
        base_q = base_q.where(combined)
        count_q = count_q.where(combined)

    total_result = await db.execute(count_q)
    total = total_result.scalar() or 0
    rows_result = await db.execute(
        base_q.order_by(SubAccessLog.id.desc()).offset(offset).limit(page_size)
    )
    rows = rows_result.scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [r.to_dict() for r in rows],
    }


@router.delete("/api/sub-access-logs")
async def clear_sub_access_logs(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    await db.execute(text("DELETE FROM sub_access_logs"))
    await db.commit()
    return {"ok": True}


@router.get("/api/audit-logs")
async def list_audit_logs(
    page: int = 1,
    page_size: int = 50,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """查询操作审计日志（分页，最新在前）。"""
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size
    total_result = await db.execute(select(func.count()).select_from(AuditLog))
    total = total_result.scalar() or 0
    rows_result = await db.execute(
        select(AuditLog).order_by(AuditLog.id.desc()).offset(offset).limit(page_size)
    )
    rows = rows_result.scalars().all()
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "items": [r.to_dict() for r in rows],
    }


@router.delete("/api/audit-logs")
async def clear_audit_logs(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    await db.execute(text("DELETE FROM audit_logs"))
    await db.commit()
    return {"ok": True}


@router.get("/api/probe-history")
async def list_probe_history(
    target: str = Query(..., description="格式：sub:1 或 node:3"),
    days: int = Query(7, ge=1, le=30),
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """查询节点或订阅的近 N 天可用性历史。"""
    from fastapi import HTTPException
    parts = target.split(":", 1)
    if len(parts) != 2 or parts[0] not in ("sub", "node"):
        raise HTTPException(400, "target 格式应为 sub:1 或 node:3")
    kind = parts[0]
    try:
        tid = int(parts[1])
    except ValueError:
        raise HTTPException(400, "target ID 须为整数")
    records = await get_probe_history(db, kind, tid, days)  # type: ignore[arg-type]
    return {"target": target, "days": days, "records": records}
