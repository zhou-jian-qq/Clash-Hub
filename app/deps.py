"""公共依赖：Setting 读写、辅助函数（供各路由模块共享）。"""

import yaml
from sqlalchemy.ext.asyncio import AsyncSession

from models import Setting
from scheduler import parse_refresh_interval_hours


async def get_setting(db: AsyncSession, key: str, default: str = "") -> str:
    """读取 Setting 表单行；不存在则返回 default。"""
    s = await db.get(Setting, key)
    return s.value if s else default


async def set_setting(db: AsyncSession, key: str, value: str) -> None:
    """写入或插入 Setting 行。"""
    s = await db.get(Setting, key)
    if s:
        s.value = value
    else:
        db.add(Setting(key=key, value=value))


def normalize_bool_text(v) -> str:
    """将表单/JSON 中的布尔语义规范为小写 true/false 字符串。"""
    s = str(v).strip().lower()
    return "true" if s in {"1", "true", "yes", "on"} else "false"


def parse_bool_text(v: str) -> bool:
    """解析 Setting 中存储的布尔字符串。"""
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


def parse_yaml_mapping_or_empty(key: str, text: str) -> dict:
    """解析 YAML 映射文本；空串返回 {}；非映射或解析失败抛 ValueError。"""
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


async def apply_settings_body(db: AsyncSession, body: dict) -> bool:
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
            parse_yaml_mapping_or_empty(k, txt)
            v = txt
        elif k in bool_keys:
            v = normalize_bool_text(v)
        else:
            v = str(v)
        await set_setting(db, k, v)
    return touch_refresh
