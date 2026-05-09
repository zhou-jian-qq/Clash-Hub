"""设置与模板路由。"""

import uuid as uuid_mod

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from aggregator import build_config
from auth import require_admin
from database import get_db
from deps import apply_settings_body, build_bark_url_for_frontend, get_setting, set_setting
from models import CustomTemplate, Setting
from preset_templates import PRESETS, get_preset_names
from proxy_latency import check_mihomo_executable
from scheduler import reschedule_refresh_job
from services.aggregator_service import (
    build_aggregated_config_yaml,
    parse_custom_template,
    validate_custom_yaml_body,
)
from services.config_cache import config_cache

router = APIRouter()


@router.get("/api/system/health")
async def system_health(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """系统健康状态：数据库、调度器、Mihomo、SECRET_KEY 来源。"""
    from auth import _SECRET_KEY, _password_hash
    from scheduler import scheduler

    # DB 可用性
    try:
        await db.execute(__import__("sqlalchemy").text("SELECT 1"))
        db_ok = True
        db_error = None
    except Exception as e:
        db_ok = False
        db_error = str(e)

    # Mihomo
    mihomo_path = await get_setting(db, "mihomo_path", "")
    mihomo_info = await check_mihomo_executable(mihomo_path)

    # SECRET_KEY 来源
    import os
    if os.environ.get("CLASH_HUB_SECRET_KEY", "").strip():
        sk_source = "env"
    elif _SECRET_KEY:
        sk_source = "db"
    else:
        sk_source = "uninitialized"

    return {
        "db": {"ok": db_ok, "error": db_error},
        "scheduler": {"running": scheduler.running},
        "mihomo": mihomo_info,
        "secret_key_source": sk_source,
        "password_hash_set": bool(_password_hash),
    }


@router.get("/api/settings")
async def get_settings(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Setting))
    out = {s.key: s.value for s in result.scalars().all()}
    out["bark_url"] = build_bark_url_for_frontend(
        out.get("notify_bark_key") or "",
        out.get("notify_bark_server") or "",
    )
    return out


@router.put("/api/settings")
async def update_settings(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    try:
        touch_refresh = await apply_settings_body(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    await db.commit()
    config_cache.invalidate_all()
    if touch_refresh:
        await reschedule_refresh_job()
    return {"ok": True}


@router.post("/api/settings/reset-uuid")
async def reset_sub_uuid(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """轮换订阅路径 UUID，旧链接立即失效。"""
    new_uuid = str(uuid_mod.uuid4())
    await set_setting(db, "sub_uuid", new_uuid)
    await db.commit()
    return {"ok": True, "sub_uuid": new_uuid}


@router.get("/api/preview")
async def preview_aggregated_config(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """与 /sub/{uuid} 相同聚合逻辑，供后台实时预览（跳过缓存，保证最新）。"""
    yaml_text, meta = await build_aggregated_config_yaml(db, use_cache=False)
    return JSONResponse({"yaml": yaml_text, **meta})


# ─── Templates ────────────────────────────────────────────────────────────────

@router.get("/api/templates")
async def list_templates(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    active = await get_setting(db, "active_template", "标准版")
    r = await db.execute(select(CustomTemplate).order_by(CustomTemplate.id))
    customs = [x.to_dict() for x in r.scalars().all()]
    return {
        "presets": get_preset_names(),
        "active": active,
        "custom_templates": customs,
    }


@router.get("/api/templates/preset-preview/{preset_name}")
async def preview_preset_template(preset_name: str, _=Depends(require_admin)):
    if preset_name not in PRESETS:
        raise HTTPException(status_code=404, detail="未知预设模板")
    yaml_text = build_config(
        proxies=[],
        template_name=preset_name,
        custom_template=None,
        include_types=None,
        exclude_types=None,
        exclude_keywords=None,
    )
    return JSONResponse({"yaml": yaml_text})


@router.post("/api/templates/select")
async def select_template(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    custom_id = body.get("custom_id")
    name = (body.get("name") or "").strip()
    if custom_id is not None:
        try:
            cid = int(custom_id)
        except (TypeError, ValueError):
            raise HTTPException(400, "无效的自定义模板 id")
        row = await db.get(CustomTemplate, cid)
        if not row:
            raise HTTPException(400, "自定义模板不存在")
        await set_setting(db, "active_template", f"custom:{cid}")
    elif name in PRESETS:
        await set_setting(db, "active_template", name)
    else:
        raise HTTPException(400, "无效的模板选择")
    await db.commit()
    config_cache.invalidate_all()
    new_active = await get_setting(db, "active_template", "标准版")
    return {"ok": True, "active": new_active}


@router.get("/api/templates/custom-items/{item_id}")
async def get_custom_template_item(item_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    row = await db.get(CustomTemplate, item_id)
    if not row:
        raise HTTPException(404, "自定义模板不存在")
    return {"id": row.id, "name": row.name, "yaml": row.yaml_body}


@router.post("/api/templates/custom-items")
async def create_custom_template_item(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    yaml_text = body.get("yaml", "")
    if not name:
        raise HTTPException(400, "模板名称不能为空")
    try:
        validate_custom_yaml_body(yaml_text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    row = CustomTemplate(name=name, yaml_body=yaml_text)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"ok": True, **row.to_dict()}


@router.put("/api/templates/custom-items/{item_id}")
async def update_custom_template_item(item_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    row = await db.get(CustomTemplate, item_id)
    if not row:
        raise HTTPException(404, "自定义模板不存在")
    body = await req.json()
    if "name" in body:
        n = (body.get("name") or "").strip()
        if not n:
            raise HTTPException(400, "名称不能为空")
        row.name = n
    if "yaml" in body:
        try:
            validate_custom_yaml_body(body["yaml"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        row.yaml_body = body["yaml"]
    await db.commit()
    return {"ok": True, **row.to_dict()}


@router.post("/api/notify/test")
async def test_notification(_=Depends(require_admin)):
    """向所有已配置渠道发送一条测试通知。"""
    from notify.dispatcher import dispatch_notification
    results = await dispatch_notification(
        title="Clash Hub 通知测试",
        body="这是一条来自 Clash Hub 的测试通知，说明通知渠道配置正确。",
        level="info",
    )
    if not results:
        return {"ok": False, "message": "未配置任何通知渠道", "results": {}}
    return {"ok": True, "results": results}


@router.delete("/api/templates/custom-items/{item_id}")
async def delete_custom_template_item(item_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    row = await db.get(CustomTemplate, item_id)
    if not row:
        raise HTTPException(404, "自定义模板不存在")
    active = await get_setting(db, "active_template", "")
    if active == f"custom:{item_id}":
        await set_setting(db, "active_template", "标准版")
    await db.delete(row)
    await db.commit()
    return {"ok": True}
