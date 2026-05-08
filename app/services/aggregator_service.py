"""聚合订阅业务逻辑：从多个源合并节点，生成 Clash 配置 YAML。"""

import yaml
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from services.config_cache import config_cache

from aggregator import (
    build_config,
    build_v2ray_subscription,
    fetch_all_subscriptions,
    parse_proxies,
    rename_proxies,
)
from deps import get_setting, parse_bool_text, parse_yaml_mapping_or_empty
from models import CustomTemplate, ImportBatch, ImportedNode, Subscription
from preset_templates import PRESETS
from aggregator import split_commas_and_newlines


def subscription_batch_prefix(base: str, index: int) -> str:
    """导入节点名称前缀：`批次名` 截断后加 `_序号`，整段长度不超过 50 字符。"""
    suffix = f"_{index}"
    max_base = 50 - len(suffix)
    if max_base < 1:
        return suffix[-50:]
    return f"{base[:max_base]}{suffix}"


def proxy_yaml_one_node(proxy: dict) -> str:
    """将单条 proxy dict 序列化为 Clash `proxies:` 单节点 YAML。"""
    return yaml.dump(
        {"proxies": [proxy]},
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
    )


def node_display_fields(proxy_yaml: str) -> dict:
    """从节点 YAML 解析首条 proxy，供列表展示名称与类型。"""
    ps = parse_proxies(proxy_yaml)
    p0 = ps[0] if ps else {}
    return {"display_name": str(p0.get("name", "")), "proxy_type": str(p0.get("type", ""))}


def parse_rules_tail(text: str) -> list[str]:
    """将多行文本拆成非空规则行（用于追加到 Clash rules 末尾）。"""
    raw = (text or "").strip()
    if not raw:
        return []
    return [ln.strip() for ln in raw.replace("\r", "\n").split("\n") if ln.strip()]


def parse_custom_template(yaml_text: str) -> dict | None:
    """将用户自定义 YAML 模板解析为与预设相同的结构。"""
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


def validate_custom_yaml_body(yaml_text: str) -> None:
    """校验自定义模板 YAML 至少为含 `proxy-groups` 的映射；否则抛 ValueError。"""
    try:
        parsed = yaml.safe_load(yaml_text)
        if not isinstance(parsed, dict) or "proxy-groups" not in parsed:
            raise ValueError("模板必须包含 proxy-groups")
    except yaml.YAMLError as e:
        raise ValueError(f"YAML 解析失败: {e}") from e


async def collect_imported_proxies(db: AsyncSession) -> list[dict]:
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
        prefix = subscription_batch_prefix(batch.name, idx)
        out.extend(rename_proxies(ps, prefix))
    return out


async def build_aggregated_config_yaml(
    db: AsyncSession,
    use_cache: bool = True,
    tag_filter: str | None = None,
) -> tuple[str, dict]:
    """
    生成与 /sub 一致的 YAML，并返回统计信息（供 /api/preview 和 /sub/{uuid}）。
    合并：启用的机场订阅 + 启用的导入节点。支持内存缓存（use_cache=True）。
    """
    result = await db.execute(select(Subscription).where(Subscription.enabled == True))  # noqa: E712
    all_subs = result.scalars().all()
    # tag_filter 过滤：仅输出含该标签的订阅
    if tag_filter:
        filtered_tag = tag_filter.strip()
        subs = [
            s.to_dict() for s in all_subs
            if filtered_tag in [t.strip() for t in (s.tags or "").split(",") if t.strip()]
        ]
    else:
        subs = [s.to_dict() for s in all_subs]
    imported_proxies = await collect_imported_proxies(db)

    if not subs and not imported_proxies:
        return "# 无启用的机场订阅或导入节点\n", {
            "proxy_count": 0,
            "group_count": 0,
            "rule_provider_count": 0,
            "empty_subscriptions": True,
            "proxy_names": [],
            "group_names": [],
        }

    active_tpl = await get_setting(db, "active_template", "标准版")

    # 缓存命中检查（仅当启用且有节点时）
    if use_cache and not tag_filter:
        sub_uuid = await get_setting(db, "sub_uuid", "")
        cache_key = (active_tpl, sub_uuid)
        cached = config_cache.get(cache_key)
        if cached is not None:
            return cached.yaml_text, cached.meta

    include_raw = await get_setting(db, "include_types", "")
    exclude_raw = await get_setting(db, "exclude_types", "")
    exclude_kw_raw = await get_setting(db, "exclude_keywords", "剩余流量,官网,重置,套餐到期,建议")
    timeout = int(await get_setting(db, "fetch_timeout", "30"))
    module_base_override_yaml = await get_setting(db, "module_base_override_yaml", "")
    module_tun_override_yaml = await get_setting(db, "module_tun_override_yaml", "")
    module_dns_override_yaml = await get_setting(db, "module_dns_override_yaml", "")
    corp_dns_enabled_raw = await get_setting(db, "corp_dns_enabled", "false")
    corp_dns_servers_raw = await get_setting(db, "corp_dns_servers", "")
    corp_domain_suffixes_raw = await get_setting(db, "corp_domain_suffixes", "")
    corp_ipcidrs_raw = await get_setting(db, "corp_ipcidrs", "")
    rules_tail_raw = await get_setting(db, "rules_tail", "")

    include_types = [t.strip() for t in include_raw.split(",") if t.strip()] or None
    exclude_types = [t.strip() for t in exclude_raw.split(",") if t.strip()] or None
    exclude_keywords = [k.strip() for k in exclude_kw_raw.split(",") if k.strip()]

    try:
        module_base_override = parse_yaml_mapping_or_empty("module_base_override_yaml", module_base_override_yaml)
        module_tun_override = parse_yaml_mapping_or_empty("module_tun_override_yaml", module_tun_override_yaml)
        module_dns_override = parse_yaml_mapping_or_empty("module_dns_override_yaml", module_dns_override_yaml)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    corp_dns = {
        "enabled": parse_bool_text(corp_dns_enabled_raw),
        "servers": split_commas_and_newlines(corp_dns_servers_raw),
        "domains": split_commas_and_newlines(corp_domain_suffixes_raw),
        "ipcidrs": split_commas_and_newlines(corp_ipcidrs_raw),
    }
    rules_tail = parse_rules_tail(rules_tail_raw)

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
                custom_template = parse_custom_template(row.yaml_body)
        if custom_template is not None:
            template_name = active_tpl
        else:
            template_name = "标准版"
    elif active_tpl == "自定义":
        custom_yaml = await get_setting(db, "custom_template", "")
        if custom_yaml:
            custom_template = parse_custom_template(custom_yaml)
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

    # 写入缓存（tag_filter 结果不缓存）
    if use_cache and not tag_filter:
        sub_uuid_for_cache = await get_setting(db, "sub_uuid", "")
        config_cache.set((active_tpl, sub_uuid_for_cache), config_yaml, meta)

    return config_yaml, meta
