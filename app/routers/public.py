"""公开订阅端点：/sub/{uuid}（Clash YAML）和 /sub/{uuid}/v2ray（Base64）。"""

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import PlainTextResponse, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aggregator import (
    aggregate_traffic,
    build_v2ray_subscription,
    fetch_all_subscriptions,
)
from database import get_db
from deps import get_setting
from models import SubAccessLog, Subscription
from rate_limit import sub_rate_limiter
from request_ip import resolve_client_ip
from services.aggregator_service import build_aggregated_config_yaml, collect_imported_proxies
from services.config_cache import config_cache
from models import SubProfile

router = APIRouter()

SUBSCRIPTION_PROFILE_NAME = "clash_hub"
SUBSCRIPTION_PROFILE_FILENAME = f"{SUBSCRIPTION_PROFILE_NAME}.yaml"


@router.get("/sub/{sub_uuid}")
async def get_aggregated_sub(
    sub_uuid: str,
    request: Request,
    tag: str | None = None,
    db: AsyncSession = Depends(get_db),
):
    sub_rate_limiter.check_request(request)

    # 优先匹配 SubProfile UUID，其次回落到全局 sub_uuid
    profile_tag: str | None = tag
    profile_result = await db.execute(select(SubProfile).where(SubProfile.uuid == sub_uuid))
    profile = profile_result.scalars().first()

    if profile:
        effective_tag = profile.tag_filter.strip() or profile_tag
    else:
        stored_uuid = await get_setting(db, "sub_uuid", "")
        if sub_uuid != stored_uuid:
            raise HTTPException(403, "无效的订阅链接")
        effective_tag = profile_tag

    direct_ip, real_ip = resolve_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    db.add(SubAccessLog(ip=direct_ip, real_ip=real_ip, user_agent=user_agent))
    await db.commit()

    config_yaml, _meta = await build_aggregated_config_yaml(db, tag_filter=effective_tag)
    if config_yaml.strip().startswith("# 无启用的机场订阅或导入节点"):
        return PlainTextResponse(
            config_yaml,
            media_type="text/yaml",
            headers={"Content-Disposition": f"attachment; filename={SUBSCRIPTION_PROFILE_FILENAME}"},
        )

    # ETag / 304 支持
    etag = config_cache.get((await get_setting(db, "active_template", "标准版"), sub_uuid))
    etag_value = etag.etag if etag else None
    if etag_value and request.headers.get("If-None-Match") == etag_value:
        return Response(status_code=304)

    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]
    t = aggregate_traffic(subs)
    userinfo_hdr = (
        f"upload=0; download={int(t['total_used'])}; "
        f"total={int(t['total_total'])}; expire={int(t['earliest_expire'])}"
    )
    resp_headers = {
        "Subscription-Userinfo": userinfo_hdr,
        "Content-Disposition": f"attachment; filename={SUBSCRIPTION_PROFILE_FILENAME}",
    }
    if etag_value:
        resp_headers["ETag"] = etag_value
    return Response(
        content=config_yaml.encode("utf-8"),
        media_type="text/yaml; charset=utf-8",
        headers=resp_headers,
    )


@router.get("/sub/{sub_uuid}/v2ray")
async def get_v2ray_sub(sub_uuid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """V2Ray 格式订阅端点（供 V2rayNG / v2rayN 等客户端使用）。"""
    sub_rate_limiter.check_request(request)
    stored_uuid = await get_setting(db, "sub_uuid", "")
    if sub_uuid != stored_uuid:
        raise HTTPException(403, "无效的订阅链接")

    direct_ip, real_ip = resolve_client_ip(request)
    user_agent = request.headers.get("User-Agent")
    db.add(SubAccessLog(ip=direct_ip, real_ip=real_ip, user_agent=user_agent))
    await db.commit()

    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]
    imported_proxies = await collect_imported_proxies(db)

    if not subs and not imported_proxies:
        return PlainTextResponse("", media_type="text/plain; charset=utf-8")

    include_raw = await get_setting(db, "include_types", "")
    exclude_raw = await get_setting(db, "exclude_types", "")
    exclude_kw_raw = await get_setting(db, "exclude_keywords", "剩余流量,官网,重置,套餐到期,建议")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))

    include_types = [t.strip() for t in include_raw.split(",") if t.strip()] or None
    exclude_types = [t.strip() for t in exclude_raw.split(",") if t.strip()] or None
    exclude_keywords = [k.strip() for k in exclude_kw_raw.split(",") if k.strip()]

    fetch_results = await fetch_all_subscriptions(subs, timeout) if subs else []
    all_proxies: list[dict] = []
    for fr in fetch_results:
        all_proxies.extend(fr["proxies"])
    all_proxies.extend(imported_proxies)

    v2ray_content = build_v2ray_subscription(all_proxies, include_types, exclude_types, exclude_keywords)

    return Response(
        content=v2ray_content.encode("ascii"),
        media_type="text/plain; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=v2ray_sub.txt"},
    )
