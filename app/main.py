"""Clash Hub FastAPI 应用入口。"""
import sys
import asyncio

# 必须在所有 imports 前最早执行这个操作
if sys.platform == "win32":
    # uvicorn reloader will spawn subprocesses which lose the policy if not set at module level
    asyncio.set_event_loop_policy(asyncio.WindowsProactorEventLoopPolicy())

import logging
import os
import uuid as uuid_mod
from contextlib import asynccontextmanager
from datetime import datetime, timezone

import yaml
from fastapi import FastAPI, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse, JSONResponse, Response, FileResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, func, text
from sqlalchemy.ext.asyncio import AsyncSession

from database import init_db, get_db
from models import Subscription, Setting, CustomTemplate, ImportBatch, ImportedNode, SubAccessLog
from auth import verify_password, create_access_token, require_admin, get_current_user_optional
from aggregator import (
    fetch_subscription_content,
    parse_proxies,
    extract_proxies_for_batch_import,
    build_config,
    build_v2ray_subscription,
    aggregate_traffic,
    fetch_all_subscriptions,
    check_subscription_availability,
    probe_imported_proxy_yaml,
    rename_proxies,
    split_commas_and_newlines,
)
from preset_templates import PRESETS, get_preset_names
from proxy_uri import looks_like_proxy_uri_line, parse_single_proxy_uri, is_remote_subscription_url
from proxy_latency import format_probe_success_message, probe_single_proxy
from migrations import ensure_subscription_updated_at_column, migrate_inline_subscriptions_to_import_nodes, ensure_sub_access_logs_table
from scheduler import (
    start_scheduler,
    stop_scheduler,
    refresh_subscriptions,
    reschedule_refresh_job,
    parse_refresh_interval_hours,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")

# 客户端订阅展示名（与前端 SUB_CLIENT_NAME 保持一致）
SUBSCRIPTION_PROFILE_NAME = "clash_hub"
SUBSCRIPTION_PROFILE_FILENAME = f"{SUBSCRIPTION_PROFILE_NAME}.yaml"


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动：建库、迁移、默认设置、调度器；关闭时停止调度器。"""
    await init_db()
    await ensure_subscription_updated_at_column()
    await ensure_sub_access_logs_table()
    await _ensure_defaults()
    await _migrate_legacy_custom_template()
    await migrate_inline_subscriptions_to_import_nodes()
    await start_scheduler()
    yield
    stop_scheduler()


app = FastAPI(title="Clash Hub", lifespan=lifespan)


@app.middleware("http")
async def _no_store_api_responses(request: Request, call_next):
    """管理 API 勿被浏览器/CDN 缓存，否则 PUT 批量改 enabled 后 GET 仍可能拿到旧 JSON。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

_TEMPLATES_DIR = os.path.join(os.path.dirname(__file__), "templates")
templates = Jinja2Templates(directory=_TEMPLATES_DIR)


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
            "fetch_timeout": "30",
            "mihomo_path": "",
            "auto_disable_on_expiry": "true",
            "auto_disable_on_empty": "true",
            "refresh_interval_hours": "6",
            "module_base_override_yaml": "",
            "module_tun_override_yaml": "",
            "module_dns_override_yaml": "",
            "corp_dns_enabled": "false",
            "corp_dns_servers": "",
            "corp_domain_suffixes": "",
            "corp_ipcidrs": "",
            "rules_tail": "",
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
    """读取 Setting 表单行；不存在则返回 default。"""
    s = await db.get(Setting, key)
    return s.value if s else default


async def _set_setting(db: AsyncSession, key: str, value: str):
    """写入或插入 Setting 行。"""
    s = await db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))


async def _apply_settings_body(db: AsyncSession, body: dict) -> bool:
    """根据请求体写入 settings。返回是否包含 refresh_interval_hours（需重载定时任务）。"""
    touch_refresh = "refresh_interval_hours" in body
    yaml_override_keys = {
        "module_base_override_yaml",
        "module_tun_override_yaml",
        "module_dns_override_yaml",
    }
    bool_keys = {
        "auto_disable_on_expiry",
        "auto_disable_on_empty",
        "corp_dns_enabled",
    }
    for k, v in body.items():
        if k == "sub_uuid":
            continue
        if k == "refresh_interval_hours":
            v = str(parse_refresh_interval_hours(str(v)))
        elif k in yaml_override_keys:
            txt = "" if v is None else str(v)
            _parse_yaml_mapping_or_empty(k, txt)
            v = txt
        elif k in bool_keys:
            v = _normalize_bool_text(v)
        else:
            v = str(v)
        await _set_setting(db, k, v)
    return touch_refresh


def _normalize_bool_text(v) -> str:
    """将表单/JSON 中的布尔语义规范为小写 true/false 字符串（供 Setting 存储）。"""
    s = str(v).strip().lower()
    return "true" if s in {"1", "true", "yes", "on"} else "false"


def _parse_bool_text(v: str) -> bool:
    """解析 Setting 中存储的布尔字符串。"""
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def _parse_yaml_mapping_or_empty(key: str, text: str) -> dict:
    raw = (text or "").strip()
    if not raw:
        return {}
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ValueError(f"{key} YAML 解析失败: {e}") from e
    if not isinstance(data, dict):
        raise ValueError(f"{key} 必须是 YAML 映射（对象）")
    return data


def _parse_rules_tail(text: str) -> list[str]:
    """将多行文本拆成非空规则行（用于追加到 Clash rules 末尾）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    return [ln.strip() for ln in raw.replace("\r", "\n").split("\n") if ln.strip()]


def _subscription_batch_prefix(base: str, index: int) -> str:
    """导入节点名称前缀：`批次名` 截断后加 `_序号`，整段长度不超过 50 字符。"""
    suffix = f"_{index}"
    max_base = 50 - len(suffix)
    if max_base < 1:
        return suffix[-50:]
    return f"{base[:max_base]}{suffix}"


def _require_airport_subscription_url(url: str) -> None:
    """若非 http(s) 订阅 URL 则抛 400（强制机场订阅与节点导入分流）。"""
    if not is_remote_subscription_url((url or "").strip()):
        raise HTTPException(
            400,
            "机场订阅仅支持 http(s) 订阅链接；单节点、分享链接或 Clash proxies 请使用「节点导入」页面",
        )


async def _collect_imported_proxies(db: AsyncSession) -> list[dict]:
    """启用中的导入节点，按批次与顺序加前缀后合并为 proxy 列表。"""
    r = await db.execute(
        select(ImportedNode, ImportBatch)
        .join(ImportBatch, ImportedNode.batch_id == ImportBatch.id)
        .where(ImportedNode.enabled == True)  # noqa: E712
        .order_by(ImportBatch.id, ImportedNode.sort_order)
    )
    rows = r.all()
    per_batch_idx: dict[int, int] = {}
    out: list[dict] = []
    for node, batch in rows:
        per_batch_idx[batch.id] = per_batch_idx.get(batch.id, 0) + 1
        idx = per_batch_idx[batch.id]
        ps = parse_proxies(node.proxy_yaml)
        if not ps:
            continue
        prefix = _subscription_batch_prefix(batch.name, idx)
        out.extend(rename_proxies(ps, prefix))
    return out


def _proxy_yaml_one_node(proxy: dict) -> str:
    """将单条 proxy dict 序列化为 Clash `proxies:` 单节点 YAML。"""
    return yaml.dump(
        {"proxies": [proxy]},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def _node_display_fields(proxy_yaml: str) -> dict:
    """从节点 YAML 解析首条 proxy，供列表展示名称与类型。"""
    ps = parse_proxies(proxy_yaml)
    p0 = ps[0] if ps else {}
    return {"display_name": str(p0.get("name", "")), "proxy_type": str(p0.get("type", ""))}


async def _touch_batch_updated(batch_id: int, db: AsyncSession) -> None:
    """更新导入批次的 updated_at（节点变更后调用）。"""
    b = await db.get(ImportBatch, batch_id)
    if b:
        b.updated_at = datetime.now(timezone.utc)


async def _set_all_imported_nodes_enabled(db: AsyncSession, batch_id: int, enabled: bool) -> int:
    """将该批次下全部节点设为同一 enabled，返回更新条数。"""
    r = await db.execute(select(ImportedNode).where(ImportedNode.batch_id == batch_id))
    n = 0
    for node in r.scalars().all():
        node.enabled = enabled
        n += 1
    return n


async def _hydrate_subscription_fetch(sub: Subscription, db: AsyncSession) -> None:
    """首次拉取并填充流量与节点数（失败时仅打日志，仍保留订阅）。"""
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
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
async def login(req: Request, response: Response):
    body = await req.json()
    password = body.get("password", "")
    if not verify_password(password):
        raise HTTPException(status_code=401, detail="密码错误")
    token = create_access_token({"role": "admin"})
    response.set_cookie(key="ch_token", value=token, httponly=True, max_age=86400)
    return {"token": token}


@app.post("/api/logout")
async def logout(response: Response):
    response.delete_cookie("ch_token")
    return {"message": "已登出"}


# ═══════════════════ Subscriptions CRUD ═══════════════════

@app.get("/api/subscriptions")
async def list_subscriptions(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Subscription).order_by(Subscription.id))
    subs = result.scalars().all()
    return [s.to_dict() for s in subs]


@app.post("/api/subscriptions")
async def create_subscription(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    _require_airport_subscription_url(body.get("url", ""))
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


@app.put("/api/subscriptions/{sub_id}")
async def update_subscription(sub_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    body = await req.json()
    if "url" in body:
        _require_airport_subscription_url(body["url"])
    for field in ("name", "url", "prefix", "enabled", "auto_disable"):
        if field in body:
            setattr(sub, field, body[field])
    sub.updated_at = datetime.now(timezone.utc)
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
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
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
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
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

@app.get("/api/subscriptions/{sub_id}/nodes")
async def get_subscription_nodes(sub_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """获取单条订阅下的所有节点明细（无状态，实时拉取或从上次内容中解析）"""
    sub = await db.get(Subscription, sub_id)
    if not sub:
        raise HTTPException(404, "订阅不存在")
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
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
                "proxy_yaml": yaml.dump([p], allow_unicode=True, default_flow_style=False, sort_keys=False)
            })
        return {"ok": True, "nodes": nodes}
    except Exception as e:
        raise HTTPException(500, f"获取节点失败: {e}")

@app.post("/api/proxies/check")
async def probe_proxy_yaml(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """无状态代理测速：请求体传 `proxy_yaml`，解析首条节点并探测延迟（不落库）。"""
    body = await req.json()
    proxy_yaml = body.get("proxy_yaml")
    if not proxy_yaml:
        raise HTTPException(400, "缺少 proxy_yaml 参数")
    
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
    mihomo_path = await _get_setting(db, "mihomo_path", "")
    
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
    except Exception as e:
        logger.warning(f"无状态测速异常: {e}")
        return {
            "available": False,
            "message": f"不可用：{e}",
            "error": str(e),
            "latency_ms": None,
            "tcp_tested": False,
            "probe_kind": "none"
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
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
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


# ═══════════════════ Import batches / imported nodes ═══════════════════


@app.get("/api/import-batches")
async def list_import_batches(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    r = await db.execute(select(ImportBatch).order_by(ImportBatch.id.desc()))
    batches = list(r.scalars().all())
    out: list[dict] = []
    for b in batches:
        nr = await db.execute(
            select(ImportedNode)
            .where(ImportedNode.batch_id == b.id)
            .order_by(ImportedNode.sort_order)
        )
        nodes: list[dict] = []
        for n in nr.scalars().all():
            d = n.to_dict()
            d.update(_node_display_fields(n.proxy_yaml))
            nodes.append(d)
        bd = b.to_dict()
        bd["nodes"] = nodes
        out.append(bd)
    return out


@app.post("/api/import-batches")
async def create_import_batch(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    name = (body.get("name") or "").strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    b = ImportBatch(name=name[:100])
    db.add(b)
    await db.commit()
    await db.refresh(b)
    return b.to_dict()


@app.put("/api/import-batches/{batch_id}")
async def update_import_batch(batch_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """更新批次名称，或批量启用/禁用该批次下全部节点（set_all_nodes_enabled）。"""
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    body = await req.json()
    if "name" in body:
        n = (body.get("name") or "").strip()
        if not n:
            raise HTTPException(400, "名称不能为空")
        b.name = n[:100]
    if "set_all_nodes_enabled" in body:
        await _set_all_imported_nodes_enabled(db, batch_id, bool(body.get("set_all_nodes_enabled")))
    b.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(b)
    return b.to_dict()


@app.delete("/api/import-batches/{batch_id}")
async def delete_import_batch(batch_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    await db.delete(b)
    await db.commit()
    return {"ok": True}


@app.post("/api/import-batches/{batch_id}/set-all-nodes-enabled")
async def set_all_import_batch_nodes_enabled(
    batch_id: int,
    req: Request,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    """
    批量启用/禁用某导入批次下的全部节点（聚合配置中仅 enabled 的节点会参与输出）。
    body: { "enabled": true | false }
    """
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    body = await req.json()
    enabled = bool(body.get("enabled", True))
    cnt = await _set_all_imported_nodes_enabled(db, batch_id, enabled)
    b.updated_at = datetime.now(timezone.utc)
    await db.commit()
    return {"ok": True, "updated": cnt, "enabled": enabled}


@app.post("/api/import-batches/import")
async def import_batches_bulk_import(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """多行分享链接或 Clash proxies YAML：创建一批次及多条节点。"""
    body = await req.json()
    name_base = (body.get("name") or body.get("name_prefix") or "").strip()
    text = body.get("text") or ""
    if not name_base:
        raise HTTPException(400, "名称不能为空")
    display_name = name_base[:100]

    nodes: list[dict] = []
    skipped_lines: list[dict] = []
    mode = ""

    yaml_proxies = extract_proxies_for_batch_import(text)
    if yaml_proxies:
        nodes = yaml_proxies
        mode = "proxies_yaml"
    else:
        raw_lines: list[tuple[int, str]] = []
        for line_num, raw in enumerate(text.splitlines(), start=1):
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            raw_lines.append((line_num, line))
        for line_num, line in raw_lines:
            if not looks_like_proxy_uri_line(line):
                skipped_lines.append({"line": line_num, "reason": "无法解析为分享链接"})
                continue
            p = parse_single_proxy_uri(line)
            if not p:
                skipped_lines.append({"line": line_num, "reason": "无法解析为分享链接"})
                continue
            nodes.append(p)
        mode = "uri_lines"

    if not nodes:
        raise HTTPException(400, "没有可导入的有效分享链接或 proxies 配置")

    batch = ImportBatch(name=display_name)
    db.add(batch)
    await db.flush()

    created_ids: list[int] = []
    for idx, proxy in enumerate(nodes, start=1):
        node = ImportedNode(
            batch_id=batch.id,
            sort_order=idx,
            enabled=True,
            proxy_yaml=_proxy_yaml_one_node(proxy),
        )
        db.add(node)
        await db.flush()
        created_ids.append(node.id)

    batch.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(batch)
    return {
        "ok": True,
        "batch_id": batch.id,
        "created": len(created_ids),
        "skipped": len(skipped_lines),
        "details": {
            "created_node_ids": created_ids,
            "skipped_lines": skipped_lines,
            "mode": mode,
        },
    }


@app.post("/api/import-batches/{batch_id}/nodes")
async def add_imported_node(batch_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    b = await db.get(ImportBatch, batch_id)
    if not b:
        raise HTTPException(404, "批次不存在")
    body = await req.json()
    raw = (body.get("proxy_yaml") or body.get("text") or "").strip()
    if not raw:
        raise HTTPException(400, "内容不能为空")

    ps = parse_proxies(raw)
    if not ps:
        line = raw.splitlines()[0].strip() if raw.splitlines() else ""
        if line and looks_like_proxy_uri_line(line):
            p = parse_single_proxy_uri(line)
            if p:
                ps = [p]
    if not ps:
        raise HTTPException(400, "无法解析为有效节点")

    max_row = await db.execute(
        select(func.coalesce(func.max(ImportedNode.sort_order), 0)).where(ImportedNode.batch_id == batch_id)
    )
    max_order = int(max_row.scalar_one() or 0)

    node = ImportedNode(
        batch_id=batch_id,
        sort_order=max_order + 1,
        enabled=True,
        proxy_yaml=_proxy_yaml_one_node(ps[0]),
    )
    db.add(node)
    b.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(node)
    d = node.to_dict()
    d.update(_node_display_fields(node.proxy_yaml))
    return d


@app.put("/api/imported-nodes/{node_id}")
async def update_imported_node(node_id: int, req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")
    body = await req.json()
    if "proxy_yaml" in body:
        raw = (body.get("proxy_yaml") or "").strip()
        if not raw:
            raise HTTPException(400, "proxy_yaml 不能为空")
        ps = parse_proxies(raw)
        if not ps:
            raise HTTPException(400, "无法解析节点")
        n.proxy_yaml = _proxy_yaml_one_node(ps[0])
    if "enabled" in body:
        n.enabled = bool(body["enabled"])
    if "sort_order" in body:
        n.sort_order = int(body["sort_order"])
    n.updated_at = datetime.now(timezone.utc)
    batch = await db.get(ImportBatch, n.batch_id)
    if batch:
        batch.updated_at = datetime.now(timezone.utc)
    await db.commit()
    await db.refresh(n)
    d = n.to_dict()
    d.update(_node_display_fields(n.proxy_yaml))
    return d


@app.delete("/api/imported-nodes/{node_id}")
async def delete_imported_node(node_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")
    bid = n.batch_id
    await db.delete(n)
    await _touch_batch_updated(bid, db)
    await db.commit()
    return {"ok": True}


@app.post("/api/imported-nodes/{node_id}/check")
async def check_imported_node(node_id: int, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    """对导入节点 YAML 首条 proxy 做延迟探测，并更新 last_check_at / last_latency_ms。"""
    n = await db.get(ImportedNode, node_id)
    if not n:
        raise HTTPException(404, "节点不存在")
    batch = await db.get(ImportBatch, n.batch_id)
    if not batch:
        raise HTTPException(404, "批次不存在")
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
    mihomo_path = await _get_setting(db, "mihomo_path", "")
    prefix = _subscription_batch_prefix(batch.name, n.sort_order)
    # 使用专用测速逻辑：始终对单节点 YAML 的第一条 proxy 探测，见 aggregator.probe_imported_proxy_yaml
    r = await probe_imported_proxy_yaml(n.proxy_yaml, prefix, timeout, mihomo_path=mihomo_path)
    
    # 存入数据库
    n.last_check_at = datetime.now(timezone.utc)
    if r.get("ok"):
        n.last_latency_ms = int(r.get("latency_ms")) if r.get("latency_ms") is not None else -1
    else:
        n.last_latency_ms = -1
    await db.commit()
    
    return {
        "available": r["ok"],
        "node_count": r["node_count"],
        "message": r["message"],
        "error": r.get("error"),
        "latency_ms": r.get("latency_ms"),
        "tcp_tested": r.get("tcp_tested", False),
        "probe_kind": r.get("probe_kind", "none"),
        "display_name": _node_display_fields(n.proxy_yaml)["display_name"],
        "enabled": n.enabled,
    }


# ═══════════════════ Settings ═══════════════════

@app.get("/api/settings")
async def get_settings(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    result = await db.execute(select(Setting))
    settings = result.scalars().all()
    return {s.key: s.value for s in settings}


@app.put("/api/settings")
async def update_settings(req: Request, db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    body = await req.json()
    try:
        touch_refresh = await _apply_settings_body(db, body)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
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


def _extract_real_ip(request: Request) -> tuple[str, str | None]:
    """
    返回 (直连IP, 真实IP)。
    直连IP = request.client.host（TCP 连接来源）。
    真实IP = X-Forwarded-For 第一项 或 X-Real-IP（反向代理透传），未设置时为 None。
    """
    direct_ip = request.client.host if request.client else "unknown"
    xff = request.headers.get("X-Forwarded-For", "").strip()
    if xff:
        real_ip = xff.split(",")[0].strip()
        return direct_ip, real_ip
    xri = request.headers.get("X-Real-IP", "").strip()
    if xri:
        return direct_ip, xri
    return direct_ip, None


async def _build_aggregated_config_yaml(db: AsyncSession) -> tuple[str, dict]:
    """
    生成与 /sub 一致的 YAML，并返回统计信息（供 /api/preview）。
    合并：启用的机场订阅 + 启用的导入节点。
    """
    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]
    imported_proxies = await _collect_imported_proxies(db)

    if not subs and not imported_proxies:
        return "# 无启用的机场订阅或导入节点\n", {
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
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))
    module_base_override_yaml = await _get_setting(db, "module_base_override_yaml", "")
    module_tun_override_yaml = await _get_setting(db, "module_tun_override_yaml", "")
    module_dns_override_yaml = await _get_setting(db, "module_dns_override_yaml", "")
    corp_dns_enabled_raw = await _get_setting(db, "corp_dns_enabled", "false")
    corp_dns_servers_raw = await _get_setting(db, "corp_dns_servers", "")
    corp_domain_suffixes_raw = await _get_setting(db, "corp_domain_suffixes", "")
    corp_ipcidrs_raw = await _get_setting(db, "corp_ipcidrs", "")
    rules_tail_raw = await _get_setting(db, "rules_tail", "")

    include_types = [t.strip() for t in include_raw.split(",") if t.strip()] or None
    exclude_types = [t.strip() for t in exclude_raw.split(",") if t.strip()] or None
    exclude_keywords = [k.strip() for k in exclude_kw_raw.split(",") if k.strip()]
    try:
        module_base_override = _parse_yaml_mapping_or_empty("module_base_override_yaml", module_base_override_yaml)
        module_tun_override = _parse_yaml_mapping_or_empty("module_tun_override_yaml", module_tun_override_yaml)
        module_dns_override = _parse_yaml_mapping_or_empty("module_dns_override_yaml", module_dns_override_yaml)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    corp_dns = {
        "enabled": _parse_bool_text(corp_dns_enabled_raw),
        "servers": split_commas_and_newlines(corp_dns_servers_raw),
        "domains": split_commas_and_newlines(corp_domain_suffixes_raw),
        "ipcidrs": split_commas_and_newlines(corp_ipcidrs_raw),
    }
    rules_tail = _parse_rules_tail(rules_tail_raw)

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

    fetch_results = await fetch_all_subscriptions(subs, timeout) if subs else []
    all_proxies: list[dict] = []
    for fr in fetch_results:
        all_proxies.extend(fr["proxies"])
    all_proxies.extend(imported_proxies)

    config_yaml = build_config(
        proxies=all_proxies,
        template_name=template_name,
        custom_template=custom_template,
        include_types=include_types,
        exclude_types=exclude_types,
        exclude_keywords=exclude_keywords,
        module_base_override=module_base_override,
        module_tun_override=module_tun_override,
        module_dns_override=module_dns_override,
        corp_dns=corp_dns,
        rules_tail=rules_tail,
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
        "module_flags": {
            "base_override": bool(module_base_override),
            "tun_override": bool(module_tun_override),
            "dns_override": bool(module_dns_override),
            "corp_dns_enabled": bool(corp_dns.get("enabled")),
            "rules_tail_count": len(rules_tail),
        },
    }
    return config_yaml, meta


@app.get("/sub/{sub_uuid}")
async def get_aggregated_sub(sub_uuid: str, request: Request, db: AsyncSession = Depends(get_db)):
    stored_uuid = await _get_setting(db, "sub_uuid", "")
    if sub_uuid != stored_uuid:
        raise HTTPException(403, "无效的订阅链接")

    direct_ip, real_ip = _extract_real_ip(request)
    user_agent = request.headers.get("User-Agent")
    log_entry = SubAccessLog(
        ip=direct_ip,
        real_ip=real_ip,
        user_agent=user_agent,
    )
    db.add(log_entry)
    await db.commit()

    config_yaml, _meta = await _build_aggregated_config_yaml(db)
    if config_yaml.strip().startswith("# 无启用的机场订阅或导入节点"):
        return PlainTextResponse(
            config_yaml,
            media_type="text/yaml",
            headers={
                # 勿用 filename="..." 引号形式：部分客户端会把引号一并当作展示名（出现 \"...\"）
                "Content-Disposition": f"attachment; filename={SUBSCRIPTION_PROFILE_FILENAME}",
            },
        )

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
        headers={
            "Subscription-Userinfo": userinfo_hdr,
            "Content-Disposition": f"attachment; filename={SUBSCRIPTION_PROFILE_FILENAME}",
        },
    )


@app.get("/sub/{sub_uuid}/v2ray")
async def get_v2ray_sub(sub_uuid: str, request: Request, db: AsyncSession = Depends(get_db)):
    """V2Ray 格式订阅端点（供 V2rayNG / v2rayN 等客户端使用）。

    返回 Base64 编码的代理 URI 列表，每行一个分享链接（vmess://、vless://、ss:// 等）。
    """
    stored_uuid = await _get_setting(db, "sub_uuid", "")
    if sub_uuid != stored_uuid:
        raise HTTPException(403, "无效的订阅链接")

    direct_ip, real_ip = _extract_real_ip(request)
    user_agent = request.headers.get("User-Agent")
    log_entry = SubAccessLog(
        ip=direct_ip,
        real_ip=real_ip,
        user_agent=user_agent,
    )
    db.add(log_entry)
    await db.commit()

    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    subs = [s.to_dict() for s in result.scalars().all()]
    imported_proxies = await _collect_imported_proxies(db)

    if not subs and not imported_proxies:
        return PlainTextResponse("", media_type="text/plain; charset=utf-8")

    include_raw = await _get_setting(db, "include_types", "")
    exclude_raw = await _get_setting(db, "exclude_types", "")
    exclude_kw_raw = await _get_setting(db, "exclude_keywords", "剩余流量,官网,重置,套餐到期,建议")
    timeout = int(await _get_setting(db, "fetch_timeout", "30"))

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
        headers={
            "Content-Disposition": "attachment; filename=v2ray_sub.txt",
        },
    )


def _validate_custom_yaml_body(yaml_text: str) -> None:
    """校验自定义模板 YAML 至少为含 `proxy-groups` 的映射；否则抛 ValueError。"""
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


@app.get("/login", response_class=HTMLResponse)
async def login_page(request: Request, user=Depends(get_current_user_optional)):
    if user:
        return RedirectResponse(url="/", status_code=302)
    login_path = os.path.join(_TEMPLATES_DIR, "login_page.html")
    if os.path.exists(login_path):
        return templates.TemplateResponse(request=request, name="login_page.html", context={"request": request})
    return HTMLResponse("<h1>Clash Hub</h1><p>模板文件缺失</p>")


@app.get("/api/sub-access-logs")
async def list_sub_access_logs(
    page: int = 1,
    page_size: int = 50,
    ip: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    db: AsyncSession = Depends(get_db),
    _=Depends(require_admin),
):
    from datetime import date as _date, timedelta
    page = max(1, page)
    page_size = min(max(1, page_size), 200)
    offset = (page - 1) * page_size

    conditions = []
    if ip and ip.strip():
        pat = f"%{ip.strip()}%"
        conditions.append(
            (SubAccessLog.ip.like(pat)) | (SubAccessLog.real_ip.like(pat))
        )
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
        from sqlalchemy import and_
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


@app.delete("/api/sub-access-logs")
async def clear_sub_access_logs(db: AsyncSession = Depends(get_db), _=Depends(require_admin)):
    await db.execute(text("DELETE FROM sub_access_logs"))
    await db.commit()
    return {"ok": True}


@app.get("/", response_class=HTMLResponse)
@app.get("/overview", response_class=HTMLResponse)
@app.get("/subs", response_class=HTMLResponse)
@app.get("/imports", response_class=HTMLResponse)
@app.get("/config", response_class=HTMLResponse)
@app.get("/logs", response_class=HTMLResponse)
async def app_root(request: Request, user=Depends(get_current_user_optional)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    app_path = os.path.join(_TEMPLATES_DIR, "app_page.html")
    if os.path.exists(app_path):
        return templates.TemplateResponse(request=request, name="app_page.html", context={"request": request})
    return HTMLResponse("<h1>Clash Hub</h1><p>模板文件缺失</p>")
