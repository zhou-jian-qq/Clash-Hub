"""启动时一次性迁移（SQLite schema / 数据）。"""

import logging
from datetime import datetime, timezone

import yaml
from sqlalchemy import select, text
from sqlalchemy.exc import OperationalError

from database import async_session, engine
from models import ImportBatch, ImportedNode, Setting, Subscription

logger = logging.getLogger("migrations")


async def ensure_subscription_updated_at_column() -> None:
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


async def migrate_inline_subscriptions_to_import_nodes() -> None:
    """
    将非 http(s) 的旧订阅行迁入 import_batches + imported_nodes 后删除原行。
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


async def ensure_sub_access_logs_table() -> None:
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
