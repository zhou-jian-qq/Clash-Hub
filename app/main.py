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

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select

from database import async_session, init_db, get_db
from models import CustomTemplate, Setting
from auth import (
    get_or_create_secret_key,
    init_secret_key,
    load_password_hash,
    init_password_hash,
    is_weak_default_password,
)
from rate_limit import sub_rate_limiter
from migrations import (
    ensure_subscription_updated_at_column,
    migrate_inline_subscriptions_to_import_nodes,
    ensure_sub_access_logs_table,
    run_all_migrations,
)
from scheduler import start_scheduler, stop_scheduler

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(name)s] %(levelname)s: %(message)s")
logger = logging.getLogger("main")


# ─── Startup / Shutdown ───────────────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动：建库、迁移、默认设置、调度器；关闭时停止调度器。"""
    await init_db()
    await run_all_migrations()
    await _ensure_defaults()
    await _migrate_legacy_custom_template()

    # Phase 1.1：持久化 SECRET_KEY（重启后 token 不再失效）
    secret_key = await get_or_create_secret_key()
    init_secret_key(secret_key)

    # Phase 1.2：预热 bcrypt 密码哈希缓存
    pw_hash = await load_password_hash()
    init_password_hash(pw_hash)

    # Phase 1.3：从设置中加载限流参数
    async with async_session() as _sess:
        _rlimit_row = await _sess.get(Setting, "sub_rate_limit_per_minute")
        _rlimit = int(_rlimit_row.value) if _rlimit_row and _rlimit_row.value.isdigit() else 30
        sub_rate_limiter.update_limits(max_requests=_rlimit)

    # Phase 1.4：弱口令启动告警
    if is_weak_default_password():
        logger.warning(
            "安全警告：当前使用默认弱口令（admin888）！"
            "请在「设置」页面修改管理员密码，或通过环境变量 ADMIN_PASSWORD 设置强密码。"
        )

    await start_scheduler()
    yield
    stop_scheduler()


async def _ensure_defaults():
    """首次启动时写入默认 settings。"""
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
            "sub_rate_limit_per_minute": "30",
            # Phase 3.1：通知渠道配置
            "notify_channels": "",
            "notify_serverchan_key": "",
            "notify_dingtalk_url": "",
            "notify_dingtalk_secret": "",
            "notify_wecom_url": "",
            "notify_bark_server": "https://api.day.app",
            "notify_bark_key": "",
            "notify_bark_group": "Clash Hub",
        }
        for k, v in defaults.items():
            existing = await session.get(Setting, k)
            if not existing:
                session.add(Setting(key=k, value=v))
        await session.commit()


async def _migrate_legacy_custom_template():
    """将旧版 settings.custom_template 单条内容迁入 custom_templates 表。"""
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


# ─── App 组装 ────────────────────────────────────────────────────────────────

app = FastAPI(title="Clash Hub", lifespan=lifespan)


@app.middleware("http")
async def _no_store_api_responses(request: Request, call_next):
    """管理 API 勿被浏览器/CDN 缓存，否则 PUT 批量改 enabled 后 GET 仍可能拿到旧 JSON。"""
    response = await call_next(request)
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.middleware("http")
async def _audit_log_middleware(request: Request, call_next):
    """Phase 3.5：写操作审计中间件（POST/PUT/PATCH/DELETE /api/*）。"""
    response = await call_next(request)
    method = request.method.upper()
    path = request.url.path
    if method in ("POST", "PUT", "PATCH", "DELETE") and path.startswith("/api/"):
        # 跳过登录/登出（避免记录密码）
        if path in ("/api/login", "/api/logout"):
            return response
        direct_ip = request.client.host if request.client else "unknown"
        try:
            from database import async_session as _sess_factory
            from models import AuditLog
            async with _sess_factory() as _sess:
                _sess.add(AuditLog(
                    action=method,
                    path=path,
                    payload_json=None,
                    ip=direct_ip,
                ))
                await _sess.commit()
        except Exception:
            pass
    return response


_STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.isdir(_STATIC_DIR):
    app.mount("/static", StaticFiles(directory=_STATIC_DIR), name="static")

# 注册所有路由模块
from routers import auth, subscriptions, imports, settings, logs, public, pages, backup, profiles  # noqa: E402

app.include_router(auth.router)
app.include_router(subscriptions.router)
app.include_router(imports.router)
app.include_router(settings.router)
app.include_router(logs.router)
app.include_router(public.router)
app.include_router(backup.router)
app.include_router(profiles.router)
app.include_router(pages.router)
