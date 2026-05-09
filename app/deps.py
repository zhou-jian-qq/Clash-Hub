"""公共依赖：Setting 读写、辅助函数（供各路由模块共享）。"""

import yaml
from urllib.parse import urljoin, urlparse
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


def parse_bark_url(url: str) -> tuple[str, str]:
    """
    从前端填入的 Bark 地址解析出 device_key 与服务器根 URL。
    常见形式：https://api.day.app/YourDeviceKey 或自建 https://example.com/longKey
    返回：(device_key, server_base)，无效则 ("", "").
    """
    raw = (url or "").strip()
    if not raw:
        return "", ""
    if not raw.startswith(("http://", "https://")):
        raw = "https://" + raw
    parsed = urlparse(raw)
    if not parsed.netloc:
        return "", ""
    scheme = parsed.scheme if parsed.scheme in ("http", "https") else "https"
    base = f"{scheme}://{parsed.netloc}".rstrip("/")
    path = (parsed.path or "").strip("/")
    if not path:
        return "", base
    parts = [p for p in path.split("/") if p]
    if not parts:
        return "", base
    noise = {"push", "notifications", "notification"}
    candidates = [p for p in parts if p.lower() not in noise]
    if not candidates:
        return "", base
    key = candidates[-1].strip()
    if not key:
        return "", base
    return key, base


def build_bark_url_for_frontend(device_key: str, server_raw: str) -> str:
    """供 GET /api/settings 返回 bark_url（与占位输入框同源）。"""
    k = (device_key or "").strip()
    if not k:
        return ""
    base = ((server_raw or "").strip()).rstrip("/") or "https://api.day.app"
    return urljoin(base + "/", k)


def merge_notify_channels_csv(csv: str, channel: str, enabled: bool) -> str:
    """在逗号分隔的 notify_channels 中启用或移除某渠道，保留原有顺序。"""
    parts = [p.strip() for p in (csv or "").split(",") if p.strip()]
    out = []
    for p in parts:
        if p == channel:
            if enabled:
                out.append(p)
            # enabled False：跳过即移除
        else:
            out.append(p)
    if enabled and channel not in out:
        out.append(channel)
    return ",".join(out)


async def sync_bark_url_field(db: AsyncSession, bark_url_front: str) -> None:
    """
    将设置页传来的 bark_url 同步为 notify_bark_key / notify_bark_server，
    并更新 notify_channels；清空遗留的 bark_url 键以免与调度逻辑不一致。
    """
    raw = bark_url_front if bark_url_front is None else str(bark_url_front).strip()
    key, srv = parse_bark_url(raw)
    await set_setting(db, "notify_bark_key", key)
    if srv:
        await set_setting(db, "notify_bark_server", srv)
    merged = merge_notify_channels_csv(await get_setting(db, "notify_channels", ""), "bark", bool(key))
    await set_setting(db, "notify_channels", merged)
    await set_setting(db, "bark_url", "")  # 前端占位键不写库


async def apply_settings_body(db: AsyncSession, body: dict) -> bool:
    """根据请求体写入 settings。返回是否包含 refresh_interval_hours（需重载定时任务）。"""
    touch_refresh = "refresh_interval_hours" in body
    body = dict(body)
    if "bark_url" in body:
        await sync_bark_url_field(db, body.pop("bark_url"))
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
