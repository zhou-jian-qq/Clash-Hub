"""订阅管理路由：CRUD、批量操作、刷新、检测。"""

import asyncio
import json
from datetime import datetime, timezone
from typing import AsyncGenerator

import yaml
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aggregator import (
    check_subscription_availability,
    dedupe_proxies,
    fetch_subscription_content,
    fetch_all_subscriptions,
    parse_proxies,
    rename_proxies,
)
from auth import require_admin
from database import get_db
from deps import get_setting
from models import Subscription
from proxy_latency import format_probe_success_message, probe_single_proxy
from proxy_uri import is_remote_subscription_url, proxy_dict_to_uri
from scheduler import refresh_subscriptions
from services.config_cache import config_cache

router = APIRouter()


def _require_airport_subscription_url(url: str) -> None:
    """若非 http(s) 订阅 URL 则抛 400。"""
    if not is_remote_subscription_url((url or "").strip()):
        raise HTTPException(
            400,
            "机场订阅仅支持 http(s) 订阅链接；单节点、分享链接或 Clash proxies 请使用「节点导入」页面",
        )


async def _hydrate_subscription_fetch(sub: Subscription, db: AsyncSession) -> None:
    """首次拉取并填充流量与节点数（失败时仅打日志，仍保留订阅）。"""
    import logging
    logger = logging.getLogger("routers.subscriptions")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    try:
        content, userinfo = await fetch_subscription_content(sub.url, timeout)
        proxies = parse_proxies(content)
        sub.used = userinfo.get("used", 0)
        sub.total = userinfo.get("total", 0)
        sub.expire = userinfo.get("expire", 0)
        sub.node_count = len(proxies)
        sub.last_sync = datetime.now(timezone.utc)
    except Exception as e:
        logger.warning("首次抓取订阅失败: %s", e)


@router.get("/api/subscriptions")
async def list_subscriptions(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Subscription).order_by(Subscription.id))
    return [s.to_dict() for s in result.scalars().all()]


@router.post("/api/subscriptions")
async def create_subscription(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    _require_airport_subscription_url(body.get("url", ""))
    sub = Subscription(
        name=body["name"],
        url=body["url"],
        prefix=body.get("prefix", ""),
        enabled=body.get("enabled", True),
        auto_disable=body.get("auto_disable", True),
        tags=body.get("tags", ""),
    )
    await _hydrate_subscription_fetch(sub, db)
    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    config_cache.invalidate_all()
    return sub.to_dict()


@router.put("/api/subscriptions/{sub_id}")
async def update_subscription(sub_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    body = await req.json()
    if "url" in body:
        _require_airport_subscription_url(body["url"])
    for field in ("name", "url", "prefix", "enabled", "auto_disable", "tags"):
        if field in body:
            setattr(sub, field, body[field])
    sub.updated_at = datetime.now(timezone.utc)
    await db.commit()
    config_cache.invalidate_all()
    return sub.to_dict()


@router.delete("/api/subscriptions/{sub_id}")
async def delete_subscription(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    await db.delete(sub)
    await db.commit()
    config_cache.invalidate_all()
    return {"ok": True}


@router.post("/api/subscriptions/{sub_id}/refresh")
async def refresh_single(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    try:
        content, userinfo = await fetch_subscription_content(sub.url, timeout)
        proxies = parse_proxies(content)
        sub.used = userinfo.get("used", 0)
        sub.total = userinfo.get("total", 0)
        sub.expire = userinfo.get("expire", 0)
        sub.node_count = len(proxies)
        sub.last_sync = datetime.now(timezone.utc)
        await db.commit()
        return sub.to_dict()
    except Exception as e:
        raise HTTPException(500, f"刷新失败: {e}")


@router.post("/api/subscriptions/{sub_id}/check")
async def check_one_subscription(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """检测单条订阅是否可解析出节点；不修改启用状态。"""
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    mihomo_path = await get_setting(db, "mihomo_path", "")
    r = await check_subscription_availability(sub.url, sub.prefix or "", timeout, mihomo_path=mihomo_path)
    return {
        "available": r["ok"],
        "node_count": r["node_count"],
        "message": r["message"],
        "error": r.get("error"),
        "latency_ms": r.get("latency_ms"),
        "tcp_tested": r.get("tcp_tested", False),
        "probe_kind": r.get("probe_kind", "none"),
        "name": sub.name,
        "enabled": sub.enabled,
    }


@router.get("/api/subscriptions/{sub_id}/nodes")
async def get_subscription_nodes(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """获取单条订阅下的所有节点明细（实时拉取）。"""
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    try:
        content, _ = await fetch_subscription_content(sub.url, timeout)
        proxies = parse_proxies(content or "")
        if not proxies:
            return {"ok": True, "nodes": []}
        proxies = rename_proxies(proxies, sub.prefix or "")
        nodes = []
        for i, p in enumerate(proxies):
            nodes.append({
                "id": i,
                "name": p.get("name", ""),
                "type": p.get("type", ""),
                "proxy_yaml": yaml.dump([p], allow_unicode=True, default_flow_style=False, sort_keys=False),
            })
        return {"ok": True, "nodes": nodes}
    except Exception as e:
        raise HTTPException(500, f"获取节点失败: {e}")


@router.post("/api/proxies/to-v2ray-uri")
async def proxy_yaml_to_v2ray_uri(req: Request, _=Depends(require_admin)):
    """将 Clash proxy YAML / 分享链接转换为 V2Ray 分享链接 URI。"""
    body = await req.json()
    proxy_yaml = body.get("proxy_yaml", "")
    if not proxy_yaml.strip():
        raise HTTPException(400, "缺少 proxy_yaml 参数")
    proxies = parse_proxies(proxy_yaml)
    if not proxies:
        raise HTTPException(400, "无法解析节点配置")
    uri = proxy_dict_to_uri(proxies[0])
    if uri is None:
        ptype = proxies[0].get("type", "未知")
        raise HTTPException(422, f"该节点协议（{ptype}）暂不支持转换为 V2Ray 分享链接")
    return {"uri": uri}


@router.post("/api/proxies/check")
async def probe_proxy_yaml(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """无状态代理测速：请求体传 proxy_yaml，解析首条节点并探测延迟（不落库）。"""
    import logging
    logger = logging.getLogger("routers.subscriptions")
    body = await req.json()
    proxy_yaml = body.get("proxy_yaml")
    if not proxy_yaml:
        raise HTTPException(400, "缺少 proxy_yaml 参数")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    mihomo_path = await get_setting(db, "mihomo_path", "")
    try:
        proxies = parse_proxies(proxy_yaml)
        if not proxies:
            raise HTTPException(400, "无法解析节点配置")
        p0 = proxies[0]
        probe_budget = min(25.0, float(timeout))
        ok_p, ms, perr, kind = await probe_single_proxy(p0, probe_budget, mihomo_path)
        tested = kind != "none"
        if not ok_p:
            err_msg = str(perr) if perr else "未知错误"
            return {
                "available": False,
                "message": f"探测未通过：{err_msg}",
                "error": err_msg,
                "latency_ms": None,
                "tcp_tested": tested,
                "probe_kind": kind,
            }
        msg = format_probe_success_message(kind, ms)
        return {
            "available": True,
            "message": msg,
            "error": None,
            "latency_ms": ms,
            "tcp_tested": tested,
            "probe_kind": kind,
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.warning("无状态测速异常: %s", e)
        return {
            "available": False,
            "message": f"不可用：{e}",
            "error": str(e),
            "latency_ms": None,
            "tcp_tested": False,
            "probe_kind": "none",
        }


@router.post("/api/subscriptions/batch-enabled")
async def batch_set_subscription_enabled(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "请提供非空的 ids 数组")
    enabled = bool(body.get("enabled", True))
    result = await db.execute(select(Subscription).where(Subscription.id.in_(ids)))
    subs = list(result.scalars().all())
    for s in subs:
        s.enabled = enabled
    await db.commit()
    return {"ok": True, "updated": len(subs)}


@router.post("/api/subscriptions/batch-delete")
async def batch_delete_subscriptions(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    ids = body.get("ids")
    if not isinstance(ids, list) or not ids:
        raise HTTPException(400, "请提供非空的 ids 数组")
    result = await db.execute(select(Subscription).where(Subscription.id.in_(ids)))
    subs = list(result.scalars().all())
    for s in subs:
        await db.delete(s)
    await db.commit()
    return {"ok": True, "deleted": len(subs)}


@router.post("/api/subscriptions/batch-check")
async def batch_check_subscriptions(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """批量检测订阅可用性；ids 省略则检测全部。"""
    try:
        body = await req.json()
    except Exception:
        body = {}
    if not isinstance(body, dict):
        body = {}
    ids_filter = body.get("ids")
    if ids_filter is not None:
        if not isinstance(ids_filter, list):
            raise HTTPException(400, "ids 必须为数组")
        if not ids_filter:
            raise HTTPException(400, "请至少选择一条订阅")
        result = await db.execute(
            select(Subscription).where(Subscription.id.in_(ids_filter)).order_by(Subscription.id)
        )
    else:
        result = await db.execute(select(Subscription).order_by(Subscription.id))
    subs = list(result.scalars().all())
    if ids_filter is not None and not subs:
        raise HTTPException(404, "未找到所选订阅")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    mihomo_path = await get_setting(db, "mihomo_path", "")

    async def _check_row(s: Subscription):
        r = await check_subscription_availability(s.url, s.prefix or "", timeout, mihomo_path=mihomo_path)
        return s, r

    pairs = await asyncio.gather(*[_check_row(s) for s in subs])
    disabled_names: list[str] = []
    details: list[dict] = []
    for s, r in pairs:
        details.append({
            "id": s.id,
            "name": s.name,
            "available": r["ok"],
            "node_count": r["node_count"],
            "message": r["message"],
            "latency_ms": r.get("latency_ms"),
            "tcp_tested": r.get("tcp_tested", False),
            "probe_kind": r.get("probe_kind", "none"),
        })
        if not r["ok"] and s.enabled:
            s.enabled = False
            disabled_names.append(s.name)
    await db.commit()
    return {
        "ok": True,
        "checked": len(subs),
        "auto_disabled": len(disabled_names),
        "disabled_names": disabled_names,
        "results": details,
    }


@router.post("/api/subscriptions/refresh-all")
async def refresh_all(_=Depends(require_admin)):
    await refresh_subscriptions()
    return {"ok": True}


@router.get("/api/subscriptions/refresh-stream")
async def refresh_stream(_=Depends(require_admin)):
    """SSE 端点：边刷新边推送每条订阅的进度事件。

    事件格式：`data: {"sub_id": 1, "name": "...", "ok": true, "node_count": 10}\n\n`
    完成后推送 `event: done`。
    """
    queue: asyncio.Queue = asyncio.Queue()

    async def _callback(event: dict) -> None:
        await queue.put(event)

    async def _event_generator() -> AsyncGenerator[str, None]:
        task = asyncio.create_task(refresh_subscriptions(source="sse", progress_callback=_callback))
        try:
            while True:
                try:
                    event = await asyncio.wait_for(queue.get(), timeout=1.0)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    if task.done():
                        break
                    yield ": heartbeat\n\n"
        finally:
            if not task.done():
                task.cancel()
        yield "event: done\ndata: {}\n\n"

    return StreamingResponse(
        _event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


@router.get("/api/subscriptions/dedupe-preview")
async def dedupe_preview(
    aggressive: bool = False,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """预览去重结果：返回将被保留的节点数与重复对（不修改数据库）。"""
    from models import Subscription
    from services.aggregator_service import collect_imported_proxies

    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]
    timeout = int(await get_setting(db, "fetch_timeout", "30"))

    all_proxies: list[dict] = []
    fetch_results = await fetch_all_subscriptions(subs, timeout) if subs else []
    for fr in fetch_results:
        all_proxies.extend(fr["proxies"])
    imported = await collect_imported_proxies(db)
    all_proxies.extend(imported)

    deduped, duplicates = dedupe_proxies(all_proxies, aggressive=aggressive)
    return {
        "total_before": len(all_proxies),
        "total_after": len(deduped),
        "duplicate_count": len(duplicates),
        "duplicates": [
            {
                "kept": {"name": kept.get("name"), "type": kept.get("type"), "server": kept.get("server")},
                "removed": {"name": removed.get("name"), "type": removed.get("type"), "server": removed.get("server")},
            }
            for kept, removed in duplicates[:50]  # 最多返回 50 对
        ],
    }
