"""
核心聚合引擎
- 并发抓取多个订阅源
- YAML / Base64 解析
- subscription-userinfo 流量头解析
- 双向协议过滤 (白名单 AND 黑名单)
- 关键词排除
- 前缀重命名
- 模板注入 -> 生成完整 Clash 配置
"""

import base64
import copy
import logging
from datetime import datetime, timezone
from typing import Any

import httpx
import yaml

from preset_templates import BASE_CONFIG, PRESETS
from proxy_latency import format_probe_success_message, probe_single_proxy
from proxy_uri import is_remote_subscription_url, looks_like_proxy_uri_line, parse_single_proxy_uri
logger = logging.getLogger("aggregator")


def _normalize_pasted_yaml_whitespace(s: str) -> str:
    """将 NBSP 等 Unicode 空白替换为普通空格。从网页/IM 粘贴的缩进常用 NBSP，PyYAML 会报 ScannerError。"""
    if not s:
        return s
    s = s.replace("\ufeff", "")
    for ch in ("\u00a0", "\u202f", "\u2007", "\u3000"):
        s = s.replace(ch, " ")
    return s


DEFAULT_EXCLUDE_KEYWORDS = ["剩余流量", "官网", "重置", "套餐到期", "建议"]
SUPPORTED_TYPES = {"ss", "ssr", "vmess", "vless", "trojan", "hysteria", "hysteria2",
                   "tuic", "wireguard", "snell", "socks5", "http"}


# ─── 解析 subscription-userinfo ───
def parse_userinfo(header: str) -> dict:
    """解析 HTTP 响应头 `subscription-userinfo`（分号分隔的 key=value）。

    返回 used（upload+download）、total、expire（均为整数；缺省为 0）。
    """
    info = {}
    if not header:
        return info
    for part in header.split(";"):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            try:
                info[k.strip()] = int(v.strip())
            except ValueError:
                pass
    return {
        "used": info.get("upload", 0) + info.get("download", 0),
        "total": info.get("total", 0),
        "expire": info.get("expire", 0),
    }


# ─── Base64 解码 ───
def try_decode_base64(text: str) -> str | None:
    """对整段文本做标准 Base64 解码（含补齐 padding）。失败返回 None。

    用于订阅内容外层为 Base64 时的解码；与 proxy_uri 中带 urlsafe 尝试的解码策略不同。
    """
    text = text.strip()
    padded = text + "=" * (-len(text) % 4)
    try:
        return base64.b64decode(padded).decode("utf-8", errors="ignore")
    except Exception:
        return None


# ─── 解析订阅内容为 proxies 列表 ───
def parse_proxies(content: str) -> list[dict]:
    """将订阅正文解析为 Clash `proxies` 项列表。

    依次尝试：Clash YAML（含 proxies / 根列表 / 单节点 dict）、纯分享链接行、Base64 内嵌多行 URI。
    """
    content = _normalize_pasted_yaml_whitespace(content.strip())
    if not content:
        return []

    try:
        data = yaml.safe_load(content)
        if isinstance(data, dict) and "proxies" in data:
            pl = data.get("proxies")
            if isinstance(pl, list):
                return [p for p in pl if isinstance(p, dict)]
        if isinstance(data, list):
            out = [p for p in data if isinstance(p, dict) and "name" in p and "type" in p]
            if out:
                return out
        if (
            isinstance(data, dict)
            and "proxies" not in data
            and "name" in data
            and "type" in data
        ):
            return [data]
    except yaml.YAMLError:
        pass

    lines = [ln.strip() for ln in content.splitlines() if ln.strip()]
    uri_lines = [ln for ln in lines if looks_like_proxy_uri_line(ln)]
    if uri_lines and len(uri_lines) == len(lines):
        out = []
        for ln in lines:
            p = parse_single_proxy_uri(ln)
            if p:
                out.append(p)
        if out:
            return out

    if len(lines) == 1 and looks_like_proxy_uri_line(lines[0]):
        p = parse_single_proxy_uri(lines[0])
        if p:
            return [p]

    decoded = try_decode_base64(content)
    if decoded:
        return _parse_uri_lines(decoded)

    return _parse_uri_lines(content)


def extract_proxies_for_batch_import(text: str) -> list[dict] | None:
    """
    整段文本是否为 Clash 节点 YAML（proxies 列表 / 根列表 / 单节点 dict）。
    若可解析出至少一个含 name+type 的节点则返回列表，否则返回 None（由调用方回退为按行分享链接）。
    """
    raw = (text or "").strip()
    if not raw:
        return None
    text = _normalize_pasted_yaml_whitespace(raw)
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError:
        return None
    if data is None:
        return None
    if isinstance(data, dict) and "proxies" in data:
        pl = data.get("proxies")
        if not isinstance(pl, list):
            return None
        out = [
            p
            for p in pl
            if isinstance(p, dict) and "name" in p and "type" in p
        ]
        return out if out else None
    if isinstance(data, list):
        out = [p for p in data if isinstance(p, dict) and "name" in p and "type" in p]
        return out if out else None
    if isinstance(data, dict) and "name" in data and "type" in data:
        return [data]
    return None


def _parse_uri_lines(text: str) -> list[dict]:
    """逐行解析：分享链接 parse_single_proxy_uri，否则尝试单行 YAML 单节点 dict。"""
    proxies = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        p = parse_single_proxy_uri(line)
        if p:
            proxies.append(p)
            continue
        try:
            data = yaml.safe_load(line)
            if isinstance(data, dict) and "name" in data and "type" in data:
                proxies.append(data)
        except Exception:
            pass
    return proxies


# ─── 过滤器 ───
def filter_proxies(
    proxies: list[dict],
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
) -> list[dict]:
    """
    双向协议过滤 + 关键词排除
    逻辑: 节点保留条件 = (在白名单中 OR 白名单为空) AND (不在黑名单中) AND (名称不含排除关键词)
    """
    if exclude_keywords is None:
        exclude_keywords = DEFAULT_EXCLUDE_KEYWORDS

    result = []
    for p in proxies:
        ptype = p.get("type", "").lower()
        name = p.get("name", "")

        if include_types and ptype not in include_types:
            continue
        if exclude_types and ptype in exclude_types:
            continue
        if any(kw in name for kw in exclude_keywords):
            continue

        result.append(p)
    return result


def rename_proxies(proxies: list[dict], prefix: str) -> list[dict]:
    """为每条 proxy 的 name 添加 `[prefix] ` 前缀；prefix 为空则原样返回。"""
    if not prefix:
        return proxies
    for p in proxies:
        p["name"] = f"[{prefix}] {p['name']}"
    return proxies


# ─── 并发抓取 ───
async def fetch_subscription(url: str, timeout: int = 15) -> tuple[str, dict]:
    """HTTP(S) GET 拉取远程订阅正文，并解析 `subscription-userinfo` 头。

    返回 (响应体文本, userinfo_dict)；userinfo_dict 由 parse_userinfo 生成。
    """
    async with httpx.AsyncClient(
        follow_redirects=True,
        timeout=httpx.Timeout(timeout),
        verify=False,
    ) as client:
        resp = await client.get(url, headers={
            "User-Agent": "ClashHub/1.0 clash-verge/2.0",
        })
        resp.raise_for_status()
        userinfo_header = resp.headers.get("subscription-userinfo", "")
        userinfo = parse_userinfo(userinfo_header)
        return resp.text, userinfo


async def fetch_subscription_content(url: str, timeout: int = 15) -> tuple[str, dict]:
    """远程 http(s) 订阅抓取；否则把整段字符串当作本地节点/多行 URI 内容。"""
    u = (url or "").strip()
    if is_remote_subscription_url(u):
        return await fetch_subscription(u, timeout)
    return u, {}


def proxy_tcp_endpoint(proxy: dict) -> tuple[str, int] | None:
    """从 Clash 节点 dict 取 TCP 探测目标（多数协议为 server + port）。"""
    host = proxy.get("server")
    port = proxy.get("port")
    if not host or port is None:
        return None
    try:
        return str(host).strip(), int(port)
    except (TypeError, ValueError):
        return None


async def measure_tcp_latency(host: str, port: int, timeout: float = 5.0) -> tuple[bool, float | None, str | None]:
    """
    本机对 server:port 发起 TCP 连接，测量建连耗时（毫秒级）。
    通过只说明「到该地址的 TCP 能通」，不验证具体代理协议/加密是否可用。
    """
    import asyncio
    import time

    t0 = time.perf_counter()
    writer = None
    try:
        _reader, writer = await asyncio.wait_for(
            asyncio.open_connection(host, port),
            timeout=timeout,
        )
        t1 = time.perf_counter()
        return True, (t1 - t0) * 1000.0, None
    except asyncio.TimeoutError:
        return False, None, f"TCP 连接超时（{timeout:g}s）"
    except OSError as e:
        return False, None, str(e)
    except Exception as e:
        return False, None, str(e)
    finally:
        if writer is not None:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass


async def probe_imported_proxy_yaml(
    proxy_yaml: str,
    prefix: str,
    timeout: int = 15,
    mihomo_path: str = "",
) -> dict:
    """
    对已落库的节点 YAML 做延迟探测（「节点导入」测速专用）。

    与 check_subscription_availability 的区别：入参非 URL，而是 proxy_yaml 片段；
    仅对解析出的第一个节点调用 probe_single_proxy；若含多条则在 message 中提示。
    """
    base_extra = {"latency_ms": None, "tcp_tested": False, "probe_kind": "none"}
    try:
        proxies = parse_proxies(proxy_yaml or "")
        if not proxies:
            return {
                "ok": False,
                "node_count": 0,
                "message": "未解析到任何节点",
                "error": None,
                **base_extra,
            }
        n = len(proxies)
        suffix = f"（YAML 解析到 {n} 个节点，仅对第一个测速）" if n > 1 else ""
        p0 = rename_proxies([proxies[0]], prefix or "")[0]
        probe_budget = min(25.0, float(timeout))
        
        ok_p, ms, perr, kind = await probe_single_proxy(p0, probe_budget, mihomo_path)
        
        tested = kind != "none"
        if not ok_p:
            err_msg = str(perr) if perr else "未知错误"
            return {
                "ok": False,
                "node_count": n,
                "message": f"探测未通过：{err_msg}{suffix}",
                "error": err_msg,
                "latency_ms": None,
                "tcp_tested": tested,
                "probe_kind": kind,
            }
        msg = format_probe_success_message(kind, ms, suffix=suffix)
        return {
            "ok": True,
            "node_count": n,
            "message": msg,
            "error": None,
            "latency_ms": ms,
            "tcp_tested": tested,
            "probe_kind": kind,
        }
    except Exception as e:
        err = str(e)
        logger.warning("导入节点测速失败: %s", err)
        return {
            "ok": False,
            "node_count": 0,
            "message": f"不可用：{err}",
            "error": err,
            **base_extra,
        }


async def check_subscription_availability(url: str, prefix: str, timeout: int = 15, mihomo_path: str = "") -> dict:
    """
    订阅可用性检测（分层）：

    1. 拉取/解析：与刷新逻辑一致，至少 1 个节点才算「解析成功」。
    2. 单节点：按协议做延迟探测 — http/socks5 用 httpx 经代理访问测试 URL；ss/vmess/vless/trojan/hysteria2
       等需配置 Mihomo 可执行文件由其测延迟；否则退回 TCP 建连（见 message / probe_kind）。
    3. 多节点：仅做拉取+解析，不做逐节点探测。
    """
    base_extra = {"latency_ms": None, "tcp_tested": False, "probe_kind": "none"}

    try:
        content, _userinfo = await fetch_subscription_content(url, timeout)
        proxies = parse_proxies(content or "")
        if not proxies:
            return {
                "ok": False,
                "node_count": 0,
                "message": "拉取成功但未解析到任何节点",
                "error": None,
                **base_extra,
            }
        proxies = rename_proxies(proxies, prefix or "")
        n = len(proxies)
        probe_budget = min(25.0, float(timeout))

        if n == 1:
            p0 = proxies[0]
            ok_p, ms, perr, kind = await probe_single_proxy(p0, probe_budget, mihomo_path)
            tested = kind != "none"
            if not ok_p:
                return {
                    "ok": False,
                    "node_count": 1,
                    "message": f"解析到 1 个节点，但探测未通过：{perr or '未知错误'}",
                    "error": perr,
                    "latency_ms": None,
                    "tcp_tested": tested,
                    "probe_kind": kind,
                }
            msg = format_probe_success_message(kind, ms, single_subscription=True)
            return {
                "ok": True,
                "node_count": 1,
                "message": msg,
                "error": None,
                "latency_ms": ms,
                "tcp_tested": tested,
                "probe_kind": kind,
            }

        return {
            "ok": True,
            "node_count": n,
            "message": f"可用，共 {n} 个节点（多节点仅校验拉取与解析，未测延迟）",
            "error": None,
            **base_extra,
        }
    except Exception as e:
        err = str(e)
        logger.warning("订阅可用性检测失败: %s", err)
        return {
            "ok": False,
            "node_count": 0,
            "message": f"不可用：{err}",
            "error": err,
            **base_extra,
        }


async def fetch_all_subscriptions(subscriptions: list[dict], timeout: int = 15):
    """
    并发抓取所有**已启用**订阅。

    每个元素：`{sub_id, proxies, userinfo, error}`；`subscriptions` 为 Subscription.to_dict() 列表。
    """
    import asyncio

    async def _fetch_one(sub):
        """单条订阅：拉取、解析节点、加前缀；异常时写入 error。"""
        result = {"sub_id": sub["id"], "proxies": [], "userinfo": {}, "error": None}
        try:
            content, userinfo = await fetch_subscription_content(sub["url"], timeout)
            proxies = parse_proxies(content)
            proxies = rename_proxies(proxies, sub.get("prefix", ""))
            result["proxies"] = proxies
            result["userinfo"] = userinfo
        except Exception as e:
            result["error"] = str(e)
            logger.warning("抓取订阅 %s 失败: %s", sub["name"], e)
        return result

    tasks = [_fetch_one(s) for s in subscriptions if s.get("enabled")]
    return await asyncio.gather(*tasks)


# ─── 模板引擎: 组装完整 Clash 配置 ───
def _merge_dict(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """递归合并 dict；override 的同名键覆盖 base。"""
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge_dict(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def split_commas_and_newlines(text: Any) -> list[str]:
    """将配置中的「多行 / 逗号分隔」列表规范为字符串列表。

    先按换行再按逗号切分，去空；支持传入 str、list[str] 或 None（空列表）。
    用于企业 DNS 的 servers、domains、ipcidrs 等设置项。
    """
    if text is None:
        return []
    if isinstance(text, list):
        return [str(x).strip() for x in text if str(x).strip()]
    raw = str(text)
    if not raw.strip():
        return []
    raw = raw.replace("\r", "\n")
    out: list[str] = []
    for line in raw.split("\n"):
        for item in line.split(","):
            s = item.strip()
            if s:
                out.append(s)
    return out


def _dedup_keep_order(items: list[str]) -> list[str]:
    """去重并保持首次出现顺序。"""
    seen: set[str] = set()
    out: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        out.append(item)
    return out


def _ensure_corp_domain_pattern(domain: str) -> str:
    """将域名规范为 Clash fake-ip-filter / nameserver-policy 可用的 `+.` 前缀形式（若适用）。"""
    s = domain.strip()
    if not s:
        return s
    if s.startswith("+.") or s.startswith("*."):
        return s
    if s.startswith("."):
        return f"+{s}"
    if "." in s:
        return f"+.{s}"
    return s


def _apply_corp_dns_override(config: dict[str, Any], corp_dns: dict[str, Any] | None) -> None:
    """
    企业内网 DNS 覆盖：
    - 内网域名走 nameserver-policy -> 企业 DNS
    - 内网域名加入 fake-ip-filter，避免假 IP 导致 VPN 冲突
    - 可选将内网网段补到 fallback-filter.ipcidr
    """
    if not corp_dns or not corp_dns.get("enabled"):
        return

    dns = config.get("dns")
    if not isinstance(dns, dict):
        dns = {}
        config["dns"] = dns

    servers = split_commas_and_newlines(corp_dns.get("servers"))
    domains = [_ensure_corp_domain_pattern(x) for x in split_commas_and_newlines(corp_dns.get("domains"))]
    domains = [x for x in domains if x]
    ipcidrs = split_commas_and_newlines(corp_dns.get("ipcidrs"))

    if servers and domains:
        nsp = dns.get("nameserver-policy")
        if not isinstance(nsp, dict):
            nsp = {}
            dns["nameserver-policy"] = nsp
        for d in domains:
            nsp[d] = list(servers)

    if domains:
        ff = dns.get("fake-ip-filter")
        if not isinstance(ff, list):
            ff = []
            dns["fake-ip-filter"] = ff
        ff.extend(domains)
        dns["fake-ip-filter"] = _dedup_keep_order([str(x) for x in ff if str(x).strip()])

    if ipcidrs:
        fbf = dns.get("fallback-filter")
        if not isinstance(fbf, dict):
            fbf = {}
            dns["fallback-filter"] = fbf
        cur = fbf.get("ipcidr")
        if not isinstance(cur, list):
            cur = []
        cur.extend(ipcidrs)
        fbf["ipcidr"] = _dedup_keep_order([str(x) for x in cur if str(x).strip()])


def build_config(
    proxies: list[dict],
    template_name: str = "标准版",
    custom_template: dict | None = None,
    include_types: list[str] | None = None,
    exclude_types: list[str] | None = None,
    exclude_keywords: list[str] | None = None,
    module_base_override: dict[str, Any] | None = None,
    module_tun_override: dict[str, Any] | None = None,
    module_dns_override: dict[str, Any] | None = None,
    corp_dns: dict[str, Any] | None = None,
    rules_tail: list[str] | None = None,
) -> str:
    """
    组装最终 Clash 配置文件:
    1. base 模块：BASE_CONFIG + base override
    2. tun 模块：tun override
    3. dns 模块：dns override + 企业 DNS 覆盖
    4. template/runtime/rulesTail 模块注入
    返回 YAML 字符串
    """
    filtered = filter_proxies(proxies, include_types, exclude_types, exclude_keywords)

    if not filtered:
        filtered = [{
            "name": "无可用节点",
            "type": "ss",
            "server": "127.0.0.1",
            "port": 1080,
            "cipher": "aes-128-gcm",
            "password": "placeholder",
        }]

    proxy_names = [p["name"] for p in filtered]

    if custom_template:
        tpl = custom_template
    else:
        tpl = PRESETS.get(template_name, PRESETS["标准版"])

    config = copy.deepcopy(BASE_CONFIG)
    if module_base_override:
        _merge_dict(config, module_base_override)

    if module_tun_override:
        cur_tun = config.get("tun")
        if isinstance(cur_tun, dict):
            merged_tun = copy.deepcopy(cur_tun)
            _merge_dict(merged_tun, module_tun_override)
            config["tun"] = merged_tun
        else:
            config["tun"] = copy.deepcopy(module_tun_override)

    if module_dns_override:
        cur_dns = config.get("dns")
        if isinstance(cur_dns, dict):
            merged_dns = copy.deepcopy(cur_dns)
            _merge_dict(merged_dns, module_dns_override)
            config["dns"] = merged_dns
        else:
            config["dns"] = copy.deepcopy(module_dns_override)

    _apply_corp_dns_override(config, corp_dns)
    config["proxies"] = filtered

    proxy_groups = []
    for gdef in tpl["proxy_groups"]:
        group: dict[str, Any] = {"name": gdef["name"], "type": gdef["type"]}
        plist = list(gdef.get("prepend", []))
        if gdef.get("include_all", True):
            plist.extend(proxy_names)
        group["proxies"] = plist

        for k in ("url", "interval", "lazy", "tolerance", "strategy"):
            if k in gdef:
                group[k] = gdef[k]

        proxy_groups.append(group)

    config["proxy-groups"] = proxy_groups
    config["rule-providers"] = copy.deepcopy(tpl["rule_providers"])
    config["rules"] = list(tpl["rules"])
    if rules_tail:
        config["rules"].extend([r for r in rules_tail if str(r).strip()])

    return yaml.dump(config, allow_unicode=True, default_flow_style=False, sort_keys=False)


# ─── 流量汇总 ───
def aggregate_traffic(subscriptions: list[dict]) -> dict:
    """汇总订阅列表的总已用流量、总配额、最早过期时间与可读日期（仅统计 enabled 为真的行）。"""
    total_used = 0
    total_total = 0
    earliest_expire = 0

    for sub in subscriptions:
        if not sub.get("enabled"):
            continue
        total_used += sub.get("used", 0)
        total_total += sub.get("total", 0)
        exp = sub.get("expire", 0)
        if exp > 0:
            if earliest_expire == 0 or exp < earliest_expire:
                earliest_expire = exp

    return {
        "total_used": total_used,
        "total_total": total_total,
        "remaining": max(0, total_total - total_used),
        "earliest_expire": earliest_expire,
        "expire_date": (
            datetime.fromtimestamp(earliest_expire, tz=timezone.utc).strftime("%Y-%m-%d")
            if earliest_expire > 0 else None
        ),
    }
