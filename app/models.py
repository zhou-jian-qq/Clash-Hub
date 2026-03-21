from datetime import datetime, timezone
from sqlalchemy import String, Text, Boolean, BigInteger, Integer, DateTime, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column, relationship
from database import Base


def _utc_now():
    return datetime.now(timezone.utc)


def _iso_utc_api(dt: datetime | None) -> str | None:
    """
    供 JSON 返回：SQLite 通过 SQLAlchemy 读回的 DateTime 常为 naive，但库内保存的即是 UTC 时刻。
    naive 的 .isoformat() 无 Z / 偏移，ECMAScript 会把「无后缀」的 ISO 字符串按**本地时区**解析，
    与「实为 UTC」不一致，再按 Asia/Shanghai 展示会偏 8 小时等问题。
    此处统一标成 UTC（aware）再 isoformat，前端即可正确换算东八区。
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


class Subscription(Base):
    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    prefix: Mapped[str] = mapped_column(String(50), default="")
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    auto_disable: Mapped[bool] = mapped_column(Boolean, default=True)
    total: Mapped[int] = mapped_column(BigInteger, default=0)
    used: Mapped[int] = mapped_column(BigInteger, default=0)
    expire: Mapped[int] = mapped_column(BigInteger, default=0)
    node_count: Mapped[int] = mapped_column(Integer, default=0)
    last_sync: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "url": self.url,
            "prefix": self.prefix,
            "enabled": self.enabled,
            "auto_disable": self.auto_disable,
            "total": self.total,
            "used": self.used,
            "expire": self.expire,
            "node_count": self.node_count,
            "last_sync": _iso_utc_api(self.last_sync),
            "created_at": _iso_utc_api(self.created_at),
            "updated_at": _iso_utc_api(self.updated_at),
        }


class ImportBatch(Base):
    """一次批量导入对应一个批次（树状父节点）。"""

    __tablename__ = "import_batches"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    nodes: Mapped[list["ImportedNode"]] = relationship(
        "ImportedNode",
        back_populates="batch",
        cascade="all, delete-orphan",
        order_by="ImportedNode.sort_order",
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": _iso_utc_api(self.created_at),
            "updated_at": _iso_utc_api(self.updated_at),
        }


class ImportedNode(Base):
    """批次下的单条 Clash 节点（proxy_yaml 为含单个 proxy 的可解析片段）。"""

    __tablename__ = "imported_nodes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("import_batches.id", ondelete="CASCADE"), nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    proxy_yaml: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now, onupdate=_utc_now)

    batch: Mapped["ImportBatch"] = relationship("ImportBatch", back_populates="nodes")

    def to_dict(self):
        return {
            "id": self.id,
            "batch_id": self.batch_id,
            "sort_order": self.sort_order,
            "enabled": self.enabled,
            "proxy_yaml": self.proxy_yaml,
            "created_at": _iso_utc_api(self.created_at),
            "updated_at": _iso_utc_api(self.updated_at),
        }


class Setting(Base):
    __tablename__ = "settings"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")


class CustomTemplate(Base):
    """用户自定义 Clash 模板（可多条、可命名）"""

    __tablename__ = "custom_templates"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    yaml_body: Mapped[str] = mapped_column(Text, nullable=False, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_utc_now)

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": _iso_utc_api(self.created_at),
        }
