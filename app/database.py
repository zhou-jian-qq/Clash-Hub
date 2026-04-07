"""SQLite 异步连接与会话；数据文件位于 app/data/clash_hub.db。"""

import os

from sqlalchemy import event
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DB_PATH = os.path.join(os.path.dirname(__file__), "data", "clash_hub.db")
DATABASE_URL = f"sqlite+aiosqlite:///{DB_PATH}"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


@event.listens_for(engine.sync_engine, "connect")
def _sqlite_enable_foreign_keys(dbapi_conn, _connection_record):
    """SQLite 连接时启用外键约束。"""
    if "sqlite" in DATABASE_URL:
        cursor = dbapi_conn.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()


class Base(DeclarativeBase):
    pass


async def init_db():
    """确保数据目录存在并创建 ORM 表（幂等）。"""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    async with engine.begin() as conn:
        from models import Subscription, Setting, CustomTemplate, ImportBatch, ImportedNode
        try:
            await conn.run_sync(Base.metadata.create_all)
        except Exception as e:
            import logging
            logging.getLogger("database").warning(f"Metadata create_all warning: {e}")


async def get_db() -> AsyncSession:
    """FastAPI 依赖：为每个请求提供异步 Session，退出时关闭。"""
    async with async_session() as session:
        yield session
