"""聚合配置内存缓存：LRU + TTL + ETag 支持。

键为 (active_template, sub_uuid)，值含生成的 YAML 文本与 ETag。
任何写操作（订阅/模板/设置变更）都应调用 invalidate_all() 使缓存失效。
"""

import hashlib
import time
from dataclasses import dataclass, field


@dataclass
class _CacheEntry:
    yaml_text: str
    meta: dict
    generated_at: float = field(default_factory=time.monotonic)
    etag: str = ""

    def __post_init__(self):
        if not self.etag:
            self.etag = hashlib.md5(self.yaml_text.encode("utf-8")).hexdigest()


class ConfigCache:
    """简单 TTL 缓存；同时保留最近 N 个条目（LRU 通过 dict 插入顺序近似实现）。"""

    def __init__(self, max_entries: int = 20, ttl_seconds: int = 3600) -> None:
        self._store: dict[tuple, _CacheEntry] = {}
        self.max_entries = max_entries
        self.ttl_seconds = ttl_seconds
        self.enabled = True

    def _evict_expired(self) -> None:
        now = time.monotonic()
        expired = [k for k, v in self._store.items() if now - v.generated_at > self.ttl_seconds]
        for k in expired:
            del self._store[k]

    def get(self, key: tuple) -> _CacheEntry | None:
        if not self.enabled:
            return None
        entry = self._store.get(key)
        if entry is None:
            return None
        if time.monotonic() - entry.generated_at > self.ttl_seconds:
            del self._store[key]
            return None
        return entry

    def set(self, key: tuple, yaml_text: str, meta: dict) -> _CacheEntry:
        self._evict_expired()
        if len(self._store) >= self.max_entries and key not in self._store:
            oldest = next(iter(self._store))
            del self._store[oldest]
        entry = _CacheEntry(yaml_text=yaml_text, meta=meta)
        self._store[key] = entry
        return entry

    def invalidate_all(self) -> None:
        """写操作触发：清空全部缓存条目。"""
        self._store.clear()

    def update_ttl(self, ttl_seconds: int) -> None:
        self.ttl_seconds = max(0, ttl_seconds)


# 全局单例
config_cache = ConfigCache()
