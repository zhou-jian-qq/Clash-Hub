"""启动时一次性迁移（SQLite schema / 数据）。

规范：
  - 函数命名：migrate_phaseX_Y_<动词>_<对象>
  - 必须幂等：列变更用 try/except OperationalError；建表用 IF NOT EXISTS；
    数据迁移在 Setting 表写标记 migration_<name>_done 防重复
  - 所有迁移函数注册到 MIGRATIONS 列表，lifespan 顺序调用
  - 单条失败仅记录 WARNING，不阻断启动（除非 strict=True）
  - 新表依赖 SQLAlchemy create_all 自动建出；只有列扩展和数据搬迁需要写迁移函数
"""

import logging
from typing import Callable, Coroutine, Any

import yaml
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from database import async_session, engine
from models import ImportBatch, ImportedNode, Setting, Subscription

logger = logging.getLogger("migrations")


# ─── 迁移函数 ─────────────────────────────────────────────────────────────────

async def migrate_phase0_1_add_subscription_updated_at() -> None:
    """旧库 subscriptions 表无 updated_at 时补充列。"""
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE subscriptions ADD COLUMN updated_at DATETIME"))
            logger.info("已为 subscriptions 添加 updated_at 列")
        except OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise
        await conn.execute(
            text("UPDATE subscriptions SET updated_at = COALESCE(updated_at, created_at)")
        )


async def migrate_phase0_2_create_sub_access_logs() -> None:
    """确保 sub_access_logs 表存在（旧库升级时建表）。"""
    async with engine.begin() as conn:
        await conn.execute(text(
            """
            CREATE TABLE IF NOT EXISTS sub_access_logs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                ip          VARCHAR(64) NOT NULL,
                real_ip     VARCHAR(64),
                user_agent  TEXT,
                accessed_at DATETIME NOT NULL DEFAULT (datetime('now'))
            )
            """
        ))
        logger.info("sub_access_logs 表已就绪")


async def migrate_phase0_3_inline_subscriptions_to_import_nodes() -> None:
    """将非 http(s) 的旧订阅行迁入 import_batches + imported_nodes 后删除原行。
    仅执行一次（Setting migration_v2_import_nodes_done）。
    """
    from proxy_uri import is_remote_subscription_url
    from aggregator import parse_proxies

    async with async_session() as session:
        flag = await session.get(Setting, "migration_v2_import_nodes_done")
        if flag and flag.value == "1":
            return

        r = await session.execute(select(Subscription))
        subs = list(r.scalars().all())
        to_move = [s for s in subs if not is_remote_subscription_url((s.url or "").strip())]
        if not to_move:
            session.add(Setting(key="migration_v2_import_nodes_done", value="1"))
            await session.commit()
            logger.info("迁移：无非机场订阅行，已标记 migration_v2_import_nodes_done")
            return

        for sub in to_move:
            batch = ImportBatch(name=(sub.name or "迁移")[:100])
            session.add(batch)
            await session.flush()

            content = (sub.url or "").strip()
            proxies = parse_proxies(content)
            if proxies:
                p = proxies[0]
                inline = yaml.dump(
                    {"proxies": [p]},
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )
            else:
                inline = yaml.dump(
                    {
                        "proxies": [
                            {
                                "name": "迁移占位",
                                "type": "ss",
                                "server": "127.0.0.1",
                                "port": 1080,
                                "cipher": "aes-128-gcm",
                                "password": "0",
                            }
                        ]
                    },
                    allow_unicode=True,
                    default_flow_style=False,
                    sort_keys=False,
                )

            node = ImportedNode(
                batch_id=batch.id,
                sort_order=0,
                enabled=sub.enabled,
                proxy_yaml=inline,
            )
            session.add(node)
            await session.delete(sub)

        session.add(Setting(key="migration_v2_import_nodes_done", value="1"))
        await session.commit()
        logger.info("迁移：已将 %d 条非机场订阅迁入节点导入", len(to_move))


# ─── 注册表 ───────────────────────────────────────────────────────────────────

async def migrate_phase3_4_add_subscription_tags() -> None:
    """Phase 3.4：为 subscriptions 表添加 tags 列。"""
    async with engine.begin() as conn:
        try:
            await conn.execute(text("ALTER TABLE subscriptions ADD COLUMN tags VARCHAR(200) DEFAULT ''"))
            logger.info("已为 subscriptions 添加 tags 列")
        except OperationalError as e:
            if "duplicate column" not in str(e).lower():
                raise


MIGRATIONS: list[Callable[[], Coroutine[Any, Any, None]]] = [
    migrate_phase0_1_add_subscription_updated_at,
    migrate_phase0_2_create_sub_access_logs,
    migrate_phase0_3_inline_subscriptions_to_import_nodes,
    migrate_phase3_4_add_subscription_tags,
    # Phase 3+ 的新迁移函数追加在此处
]


async def run_all_migrations() -> None:
    """按顺序执行所有迁移；单条失败仅记录 WARNING，不阻断启动。"""
    for fn in MIGRATIONS:
        try:
            await fn()
        except Exception as exc:
            logger.warning("迁移 %s 失败（不阻断启动）: %s", fn.__name__, exc)


# ─── 向后兼容的旧入口（供 main.py lifespan 直接调用，过渡期保留） ──────────────

async def ensure_subscription_updated_at_column() -> None:
    await migrate_phase0_1_add_subscription_updated_at()


async def ensure_sub_access_logs_table() -> None:
    await migrate_phase0_2_create_sub_access_logs()


async def migrate_inline_subscriptions_to_import_nodes() -> None:
    await migrate_phase0_3_inline_subscriptions_to_import_nodes()
