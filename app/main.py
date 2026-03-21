import asyncio
import json
import logging
import os
import uuid as uuid_mod
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import yaml
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, Response, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_db
from models import Subscription, Setting, CustomTemplate
from auth import verify_password, create_access_token, require_admin
from aggregator import (
    fetch_subscription_content,
    parse_proxies,
    build_config,
    aggregate_traffic,
    fetch_all_subscriptions,
    check_subscription_availability,
)
from preset_templates import PRESETS, get_preset_names
from proxy_uri import looks_like_proxy_uri_line, parse_single_proxy_uri
from scheduler import (
    start_scheduler,
    stop_scheduler,
    refresh_subscriptions,
    reschedule_refresh_job,
    parse_refresh_interval_hours,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    await _ensure_defaults()
    await _migrate_legacy_custom_template()
    await start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Clash Hub", lifespan=lifespan)

_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")


async def _ensure_defaults():
    """首次启动时写入默认 settings"""
    from database import async_session
    async with async_session() as session:
        defaults = {
            "sub_uuid": str(uuid_mod.uuid4()),
            "active_template": "标准版",
            "custom_template": "",
            "include_types": "",
            "exclude_types": "",
            "exclude_keywords": "剩余流量,官网,重置,套餐到期,建议",
            "fetch_timeout": "15",
            "mihomo_path": "",
            "auto_disable_on_expiry": "true",
            "auto_disable_on_empty": "true",
            "refresh_interval_hours": "6",
        }
        for k, v in defaults.items():
            existing = await session.get(Setting, k)
            if not existing:
                session.add(Setting(key=k, value=v))
        await session.commit()


async def _migrate_legacy_custom_template():
    """将旧版 settings.custom_template 单条内容迁入 custom_templates 表"""
    from database import async_session

    async with async_session() as session:
        legacy = await session.get(Setting, "custom_template")
        legacy_v = (legacy.value if legacy else "").strip()
        r = await session.execute(select(CustomTemplate).limit(1))
        if r.scalars().first():
            return
        if legacy_v:
            ct = CustomTemplate(name="默认模板", yaml_body=legacy_v)
            session.add(ct)
            await session.flush()
            act = await session.get(Setting, "active_template")
            if act and act.value == "自定义":
                act.value = f"custom:{ct.id}"
            await session.commit()
            logger.info("已迁移旧版自定义模板 -> custom_templates id=%s", ct.id)


# ═══════════════════ helpers ═══════════════════

async def _get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    s = await db.get(Setting, key)
    return s.value if s else default


async def _set_setting(db: AsyncSession, key: str, value: str):
    s = await db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))


async def _apply_settings_body(db: AsyncSession, body: dict) -> bool:
    """根据请求体写入 settings。返回是否包含 refresh_interval_hours（需重载定时任务）。"""
    touch_refresh = "refresh_interval_hours" in body
    for k, v in body.items():
        if k == "sub_uuid":
            continue
        if k == "refresh_interval_hours":
            v = str(parse_refresh_interval_hours(str(v)))
        else:
            v = str(v)
        await _set_setting(db, k, v)
    return touch_refresh


def _subscription_batch_prefix(base: str, index: int) -> str:
    """节点前缀：名称_自增序号，总长不超过 Subscription.prefix 限制。"""
    suffix = f"_{index}"
    max_base = 50 - len(suffix)
    if max_base < 1:
        return suffix[-50:]
    return f"{base[:max_base]}{suffix}"


async def _hydrate_subscription_fetch(sub: Subscription, db: AsyncSession) -> None:
    """首次拉取并填充流量与节点数（失败时仅打日志，仍保留订阅）。"""
    timeout = int(await _get_setting(db, "fetch_timeout", "15"))
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


# ═══════════════════ Auth ═══════════════════

@app.post("/api/login")
async def login(req: Request):
    body = await req.json()
    password = body.get("password", "")
    if not verify_password(password):
        raise HTTPException(status_code=401, detail="密码错误")
    token = create_access_token({"role": "admin"})
    return {"token": token}


# ═══════════════════ Subscriptions CRUD ═══════════════════

@app.get("/api/subscriptions")
async def list_subscriptions(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Subscription).order_by(Subscription.id))
    subs = result.scalars().all()
    return [s.to_dict() for s in subs]


@app.post("/api/subscriptions")
async def create_subscription(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    sub = Subscription(
        name=body["name"],
        url=body["url"],
        prefix=body.get("prefix", ""),
        enabled=body.get("enabled", True),
        auto_disable=body.get("auto_disable", True),
    )

    await _hydrate_subscription_fetch(sub, db)

    db.add(sub)
    await db.commit()
    await db.refresh(sub)
    return sub.to_dict()


@app.post("/api/subscriptions/batch-import")
async def batch_import_subscriptions(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """多行分享链接批量创建：同一次导入内订阅名称均为「名称」；节点前缀 = 名称_序号。"""
    body = await req.json()
    name_base = (body.get("name") or body.get("name_prefix") or "").strip()
    text = body.get("text") or ""
    if not name_base:
        raise HTTPException(400, "名称不能为空")
    display_name = name_base[:100]

    raw_lines: list[tuple[int, str]] = []
    for line_num, raw in enumerate(text.splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        raw_lines.append((line_num, line))

    validated: list[tuple[int, str]] = []
    skipped_lines: list[dict] = []
    for line_num, line in raw_lines:
        if not looks_like_proxy_uri_line(line) or not parse_single_proxy_uri(line):
            skipped_lines.append({"line": line_num, "reason": "无法解析为分享链接"})
            continue
        validated.append((line_num, line))

    if not validated:
        raise HTTPException(400, "没有可导入的有效分享链接")

    created_ids: list[int] = []
    for idx, (_line_num, uri) in enumerate(validated, start=1):
        sub = Subscription(
            name=display_name,
            url=uri,
            prefix=_subscription_batch_prefix(name_base, idx),
            enabled=True,
            auto_disable=True,
        )
        await _hydrate_subscription_fetch(sub, db)
        db.add(sub)
        await db.flush()
        created_ids.append(sub.id)

    await db.commit()
    return {
        "ok": True,
        "created": len(created_ids),
        "skipped": len(skipped_lines),
        "details": {
            "created_ids": created_ids,
            "skipped_lines": skipped_lines,
        },
    }


@app.put("/api/subscriptions/{sub_id}")
async def update_subscription(sub_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    body = await req.json()
    for field in ("name", "url", "prefix", "enabled", "auto_disable"):
        if field in body:
            setattr(sub, field, body[field])
    await db.commit()
    return sub.to_dict()


@app.delete("/api/subscriptions/{sub_id}")
async def delete_subscription(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    await db.delete(sub)
    await db.commit()
    return {"ok": True}


@app.post("/api/subscriptions/{sub_id}/refresh")
async def refresh_single(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    timeout = int(await _get_setting(db, "fetch_timeout", "15"))
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


@app.post("/api/subscriptions/{sub_id}/check")
async def check_one_subscription(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """检测单条订阅是否可解析出节点；不修改启用状态，由用户自行开关。"""
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    timeout = int(await _get_setting(db, "fetch_timeout", "15"))
    mihomo_path = await _get_setting(db, "mihomo_path", "")
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


@app.post("/api/subscriptions/batch-enabled")
async def batch_set_subscription_enabled(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """批量设置启用/禁用。"""
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


@app.post("/api/subscriptions/batch-delete")
async def batch_delete_subscriptions(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """批量删除订阅。"""
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


@app.post("/api/subscriptions/batch-check")
async def batch_check_subscriptions(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """检测订阅；请求体可含 ids: [1,2,3] 仅检测选中项，省略 ids 则检测全部（兼容旧客户端）。"""
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
    timeout = int(await _get_setting(db, "fetch_timeout", "15"))
    mihomo_path = await _get_setting(db, "mihomo_path", "")

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


@app.post("/api/subscriptions/refresh-all")
async def refresh_all(_=Depends(require_admin)):
    await refresh_subscriptions()
    return {"ok": True}


# ═══════════════════ Settings ═══════════════════

@app.get("/api/settings")
async def get_settings(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Setting))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}


@app.put("/api/settings")
async def update_settings(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    touch_refresh = await _apply_settings_body(db, body)
    await db.commit()
    if touch_refresh:
        await reschedule_refresh_job()
    return {"ok": True}


@app.post("/api/settings/reset-uuid")
async def reset_sub_uuid(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """轮换订阅路径 UUID，旧链接立即失效。"""
    new_uuid = str(uuid_mod.uuid4())
    await _set_setting(db, "sub_uuid", new_uuid)
    await db.commit()
    return {"ok": True, "sub_uuid": new_uuid}


@app.get("/api/preview")
async def preview_aggregated_config(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """与 /sub/{uuid} 相同聚合逻辑，供后台实时预览（需登录）。"""
    yaml_text, meta = await _build_aggregated_config_yaml(db)
    return JSONResponse({"yaml": yaml_text, **meta})


# ═══════════════════ Templates ═══════════════════

@app.get("/api/templates")
async def list_templates(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    active = await _get_setting(db, "active_template", "标准版")
    r = await db.execute(select(CustomTemplate).order_by(CustomTemplate.id))
    customs = [x.to_dict() for x in r.scalars().all()]
    return {
        "presets": get_preset_names(),
        "active": active,
        "custom_templates": customs,
    }


@app.get("/api/templates/preset-preview/{preset_name}")
async def preview_preset_template(preset_name: str, _=Depends(require_admin)):
    """返回该预设按当前聚合规则生成的示例 YAML（无订阅时为占位节点）。"""
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


@app.post("/api/templates/select")
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
        await _set_setting(db, "active_template", f"custom:{cid}")
    elif name in PRESETS:
        await _set_setting(db, "active_template", name)
    else:
        raise HTTPException(400, "无效的模板选择")
    await db.commit()
    new_active = await _get_setting(db, "active_template", "标准版")
    return {"ok": True, "active": new_active}


@app.get("/api/templates/custom-items/{item_id}")
async def get_custom_template_item(item_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    row = await db.get(CustomTemplate, item_id)
    if not row:
        raise HTTPException(404, "自定义模板不存在")
    return {"id": row.id, "name": row.name, "yaml": row.yaml_body}


@app.post("/api/templates/custom-items")
async def create_custom_template_item(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    yaml_text = body.get("yaml", "")
    if not name:
        raise HTTPException(400, "模板名称不能为空")
    try:
        _validate_custom_yaml_body(yaml_text)
    except ValueError as e:
        raise HTTPException(400, str(e))
    row = CustomTemplate(name=name, yaml_body=yaml_text)
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return {"ok": True, **row.to_dict()}


@app.put("/api/templates/custom-items/{item_id}")
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
            _validate_custom_yaml_body(body["yaml"])
        except ValueError as e:
            raise HTTPException(400, str(e))
        row.yaml_body = body["yaml"]
    await db.commit()
    return {"ok": True, **row.to_dict()}


@app.delete("/api/templates/custom-items/{item_id}")
async def delete_custom_template_item(item_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    row = await db.get(CustomTemplate, item_id)
    if not row:
        raise HTTPException(404, "自定义模板不存在")
    active = await _get_setting(db, "active_template", "")
    if active == f"custom:{item_id}":
        await _set_setting(db, "active_template", "标准版")
    await db.delete(row)
    await db.commit()
    return {"ok": True}


# ═══════════════════ Traffic ═══════════════════

@app.get("/api/traffic")
async def get_traffic(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Subscription))
    subs = [s.to_dict() for s in result.scalars().all()]
    return aggregate_traffic(subs)


# ═══════════════════ 聚合订阅输出 (无需鉴权) ═══════════════════

async def _build_aggregated_config_yaml(db: AsyncSession) -> tuple[str, dict]:
    """
    生成与 /sub 一致的 YAML，并返回统计信息（供 /api/preview）。
    """
    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]

    if not subs:
        return "# 无启用的订阅源\n", {
            "proxy_count": 0,
            "group_count": 0,
            "rule_provider_count": 0,
            "empty_subscriptions": True,
            "proxy_names": [],
            "group_names": [],
        }

    active_tpl = await _get_setting(db, "active_template", "标准版")
    include_raw = await _get_setting(db, "include_types", "")
    exclude_raw = await _get_setting(db, "exclude_types", "")
    exclude_kw_raw = await _get_setting(db, "exclude_keywords", "剩余流量,官网,重置,套餐到期,建议")
    timeout = int(await _get_setting(db, "fetch_timeout", "15"))

    include_types = [t.strip() for t in include_raw.split(",") if t.strip()] or None
    exclude_types = [t.strip() for t in exclude_raw.split(",") if t.strip()] or None
    exclude_keywords = [k.strip() for k in exclude_kw_raw.split(",") if k.strip()]

    custom_template = None
    template_name = "标准版"
    if active_tpl.startswith("custom:"):
        try:
            cid = int(active_tpl.split(":", 1)[1])
        except ValueError:
            cid = 0
        if cid:
            row = await db.get(CustomTemplate, cid)
            if row and row.yaml_body.strip():
                custom_template = _parse_custom_template(row.yaml_body)
        if custom_template is not None:
            template_name = active_tpl
        else:
            template_name = "标准版"
    elif active_tpl == "自定义":
        custom_yaml = await _get_setting(db, "custom_template", "")
        if custom_yaml:
            custom_template = _parse_custom_template(custom_yaml)
        template_name = "标准版" if custom_template is None else "自定义"
    elif active_tpl in PRESETS:
        template_name = active_tpl

    fetch_results = await fetch_all_subscriptions(subs, timeout)
    all_proxies = []
    for fr in fetch_results:
        all_proxies.extend(fr["proxies"])

    config_yaml = build_config(
        proxies=all_proxies,
        template_name=template_name,
        custom_template=custom_template,
        include_types=include_types,
        exclude_types=exclude_types,
        exclude_keywords=exclude_keywords,
    )

    try:
        parsed = yaml.safe_load(config_yaml)
        if not isinstance(parsed, dict):
            parsed = {}
    except yaml.YAMLError:
        parsed = {}
    rp = parsed.get("rule-providers") or {}
    if not isinstance(rp, dict):
        rp = {}
    proxies_list = parsed.get("proxies") or []
    if not isinstance(proxies_list, list):
        proxies_list = []
    groups_list = parsed.get("proxy-groups") or []
    if not isinstance(groups_list, list):
        groups_list = []
    proxy_names = [str(p.get("name", "")) for p in proxies_list if isinstance(p, dict) and p.get("name")]
    group_names = [str(g.get("name", "")) for g in groups_list if isinstance(g, dict) and g.get("name")]
    meta = {
        "proxy_count": len(proxies_list),
        "group_count": len(groups_list),
        "rule_provider_count": len(rp),
        "empty_subscriptions": False,
        "proxy_names": proxy_names,
        "group_names": group_names,
    }
    return config_yaml, meta


@app.get("/sub/{sub_uuid}")
async def get_aggregated_sub(sub_uuid: str, db: AsyncSession = Depends(get_db)):
    stored_uuid = await _get_setting(db, "sub_uuid", "")
    if sub_uuid != stored_uuid:
        raise HTTPException(403, "无效的订阅链接")

    config_yaml, _meta = await _build_aggregated_config_yaml(db)
    if config_yaml.strip().startswith("# 无启用的订阅源"):
        return PlainTextResponse(config_yaml, media_type="text/yaml")

    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]
    t = aggregate_traffic(subs)
    userinfo_hdr = (
        f"upload=0; download={int(t['total_used'])}; "
        f"total={int(t['total_total'])}; expire={int(t['earliest_expire'])}"
    )
    return Response(
        content=config_yaml.encode("utf-8"),
        media_type="text/yaml; charset=utf-8",
        headers={"Subscription-Userinfo": userinfo_hdr},
    )


def _validate_custom_yaml_body(yaml_text: str) -> None:
    try:
        parsed = yaml.safe_load(yaml_text)
        if not isinstance(parsed, dict) or "proxy-groups" not in parsed:
            raise ValueError("模板必须包含 proxy-groups")
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 解析失败: {e}") from e


def _parse_custom_template(yaml_text: str) -> dict | None:
    """将用户自定义 YAML 模板解析为与预设相同的结构"""
    try:
        data = yaml.safe_load(yaml_text)
        if not isinstance(data, dict):
            return None

        groups = []
        group_names = {g["name"] for g in data.get("proxy-groups", []) if "name" in g}
        builtin = {"DIRECT", "REJECT"} | group_names

        for g in data.get("proxy-groups", []):
            prepend = [p for p in g.get("proxies", []) if p in builtin]
            has_non_builtin = any(p not in builtin for p in g.get("proxies", []))
            groups.append({
                "name": g["name"],
                "type": g.get("type", "select"),
                "prepend": prepend,
                "include_all": has_non_builtin,
                **{k: g[k] for k in ("url", "interval", "lazy", "tolerance", "strategy") if k in g},
            })

        return {
            "proxy_groups": groups,
            "rule_providers": data.get("rule-providers", {}),
            "rules": data.get("rules", []),
        }
    except Exception:
        return None


# ═══════════════════ 前端 SPA ═══════════════════

@app.get("/favicon.ico", include_in_schema=False)
async def favicon_ico():
    """浏览器默认请求；返回 SVG 以消除 404。"""
    path = os.path.join(_STATIC_DIR, "favicon.svg")
    if os.path.isfile(path):
        return FileResponse(path, media_type="image/svg+xml")
    return Response(status_code=204)


@app.get("/.well-known/appspecific/com.chrome.devtools.json", include_in_schema=False)
async def chrome_devtools_wellknown():
    """Chrome DevTools 探测；空对象即可，消除 404 日志。"""
    return JSONResponse({})


@app.get("/", response_class=HTMLResponse)
async def index():
    html_path = os.path.join(os.path.dirname(__file__), "templates", "index.html")
    if os.path.exists(html_path):
        with open(html_path, "r", encoding="utf-8") as f:
            return HTMLResponse(f.read())
    return HTMLResponse("<h1>Clash Hub</h1><p>模板文件缺失</p>")
