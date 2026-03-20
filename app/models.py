from datetime import datetime, timezone
from sqlalchemy import String, Text, Boolean, BigInteger, Integer, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from database import Base


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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

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
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
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
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: datetime.now(timezone.utc)
    )

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }
