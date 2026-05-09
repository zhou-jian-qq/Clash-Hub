"""请求来源 IP 提取工具。"""

from __future__ import annotations

from fastapi import Request


def _normalize_ip_token(token: str) -> str | None:
    """规范化代理头中的 IP token；过滤空值/占位值。"""
    t = (token or "").strip().strip('"').strip("'")
    if not t:
        return None
    if t in {"-", "unknown", "null", "None"}:
        return None
    # RFC 7239 Forwarded 可能包含端口，如 [2001:db8::1]:443 或 1.2.3.4:1234
    if t.startswith("[") and "]" in t:
        t = t[1 : t.index("]")]
    elif ":" in t and t.count(":") == 1:
        # 仅处理 ipv4:port，避免误伤 IPv6
        host, _port = t.rsplit(":", 1)
        if host.replace(".", "").isdigit():
            t = host
    return t


def _extract_from_forwarded_header(forwarded: str) -> str | None:
    """
    从 RFC 7239 Forwarded 头提取 for= 值。
    例：Forwarded: for=1.2.3.4;proto=https, for=10.0.0.1
    """
    for part in forwarded.split(","):
        items = [x.strip() for x in part.split(";") if x.strip()]
        for item in items:
            if "=" not in item:
                continue
            k, v = item.split("=", 1)
            if k.strip().lower() == "for":
                ip = _normalize_ip_token(v)
                if ip:
                    return ip
    return None


def resolve_client_ip(request: Request) -> tuple[str, str | None]:
    """
    解析请求 IP，返回 (direct_ip, real_ip)。
    - direct_ip: 应用直接看到的来源（通常是反向代理/网关）
    - real_ip: 代理头中还原出的用户真实公网 IP（拿不到时为 None）
    """
    direct_ip = request.client.host if request.client else "unknown"

    xff = request.headers.get("X-Forwarded-For", "")
    if xff:
        for token in xff.split(","):
            ip = _normalize_ip_token(token)
            if ip:
                return direct_ip, ip

    xri = _normalize_ip_token(request.headers.get("X-Real-IP", ""))
    if xri:
        return direct_ip, xri

    fwd = request.headers.get("Forwarded", "")
    if fwd:
        fwd_ip = _extract_from_forwarded_header(fwd)
        if fwd_ip:
            return direct_ip, fwd_ip

    return direct_ip, None
