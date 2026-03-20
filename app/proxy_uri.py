"""
单节点分享链接解析 -> Clash proxy dict
支持: ss://, ssr://, vmess://, vless://, trojan://, hysteria2://
"""

from __future__ import annotations

import base64
import json
import urllib.parse
from typing import Any

_URI_PREFIXES = (
    "ss://",
    "ssr://",
    "vmess://",
    "vless://",
    "trojan://",
    "hysteria2://",
    "hysteria://",
    "hy2://",
)


def is_remote_subscription_url(url: str) -> bool:
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def looks_like_proxy_uri_line(line: str) -> bool:
    s = (line or "").strip().lower()
    return any(s.startswith(p) for p in _URI_PREFIXES)


def _b64_decode(s: str) -> str:
    s = s.strip()
    pad = s + "=" * (-len(s) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(pad).decode("utf-8", errors="ignore")
        except Exception:
            continue
    return ""


def parse_single_proxy_uri(uri: str) -> dict[str, Any] | None:
    uri = (uri or "").strip()
    if not uri:
        return None
    low = uri.lower()
    if low.startswith("ss://"):
        return _parse_ss(uri)
    if low.startswith("vmess://"):
        return _parse_vmess(uri)
    if low.startswith("vless://"):
        return _parse_vless(uri)
    if low.startswith("trojan://"):
        return _parse_trojan(uri)
    if low.startswith("hysteria2://") or low.startswith("hy2://"):
        return _parse_hysteria2(uri)
    return None


def _name_from_fragment(uri: str, default: str) -> str:
    if "#" not in uri:
        return default
    return urllib.parse.unquote(uri.split("#", 1)[1].strip() or default)


def _parse_ss(uri: str) -> dict[str, Any] | None:
    try:
        rest = uri[5:]
        name = _name_from_fragment(uri, "SS")
        if "#" in rest:
            rest = rest.split("#", 1)[0]
        # ss://BASE64(method:password@host:port)
        body = rest.split("?", 1)[0]
        decoded = _b64_decode(body)
        if "@" in decoded and ":" in decoded:
            mp, hostport = decoded.rsplit("@", 1)
            if ":" not in hostport:
                return None
            host, port_s = hostport.rsplit(":", 1)
            if ":" not in mp:
                return None
            idx = mp.index(":")
            method, password = mp[:idx], mp[idx + 1 :]
            port = int(port_s)
            return {
                "name": name,
                "type": "ss",
                "server": host.strip("[]"),
                "port": port,
                "cipher": method,
                "password": password,
            }
    except Exception:
        pass
    return None


def _parse_ssr_main_segments(main: str) -> tuple[str, int, str, str, str, str] | None:
    """解析 SSR 主体 host:port:protocol:method:obfs:password_b64（password 段可含冒号则合并）。"""
    main = main.strip("/")
    if main.startswith("["):
        end = main.find("]")
        if end == -1:
            return None
        server = main[1:end]
        tail = main[end + 1 :]
        if not tail.startswith(":"):
            return None
        rest = tail[1:]
    else:
        idx = main.find(":")
        if idx == -1:
            return None
        server = main[:idx]
        rest = main[idx + 1 :]
    bits = rest.split(":")
    if len(bits) < 5:
        return None
    port_s, protocol, method, obfs = bits[0], bits[1], bits[2], bits[3]
    password_b64 = ":".join(bits[4:])
    try:
        port = int(port_s)
    except ValueError:
        return None
    return server, port, protocol, method, obfs, password_b64


def _parse_ssr(uri: str) -> dict[str, Any] | None:
    """
    ssr:// + Base64(server:port:protocol:method:obfs:password_b64?obfsparam=...&protoparam=...)
    password 段为 Base64(真实密码)；查询参数常为 Base64 或 URL 编码。
    """
    try:
        rest = uri[6:]
        if "#" in rest:
            rest = rest.split("#", 1)[0]
        raw = _b64_decode(rest)
        if not raw:
            return None
        raw = raw.strip()
        if "?" in raw:
            main, query = raw.split("?", 1)
        else:
            main, query = raw, ""
        parts = _parse_ssr_main_segments(main)
        if not parts:
            return None
        server, port, protocol, method, obfs, password_b64 = parts
        pwd_dec = _b64_decode(password_b64)
        if pwd_dec and pwd_dec.strip():
            password = pwd_dec.strip()
        else:
            password = password_b64 or ""
        name = _name_from_fragment(uri, "SSR")
        out: dict[str, Any] = {
            "name": name,
            "type": "ssr",
            "server": server.strip("[]"),
            "port": port,
            "cipher": method,
            "password": password,
            "protocol": protocol,
            "obfs": obfs,
        }
        if query:
            q = urllib.parse.parse_qs(query)
            if q.get("obfsparam"):
                v = q["obfsparam"][0]
                o = _b64_decode(v) or urllib.parse.unquote(v)
                if o:
                    out["obfs-param"] = o
            if q.get("protoparam"):
                v = q["protoparam"][0]
                p = _b64_decode(v) or urllib.parse.unquote(v)
                if p:
                    out["protocol-param"] = p
            if q.get("remarks"):
                v = q["remarks"][0]
                r = _b64_decode(v) or urllib.parse.unquote(v)
                if r:
                    out["name"] = r
            if q.get("group"):
                v = q["group"][0]
                g = _b64_decode(v) or urllib.parse.unquote(v)
                if g:
                    out["group"] = g
        return out
    except Exception:
        return None


def _parse_vmess(uri: str) -> dict[str, Any] | None:
    try:
        payload = uri[8:].strip()
        if "#" in payload:
            payload = payload.split("#")[0]
        raw = _b64_decode(payload)
        if not raw:
            return None
        j = json.loads(raw)
        if not isinstance(j, dict) or "add" not in j or "id" not in j:
            return None
        name = j.get("ps") or _name_from_fragment(uri, "VMess")
        port = int(j.get("port", 443))
        net = (j.get("net") or "tcp").lower()
        tls = str(j.get("tls", "")).lower() in ("1", "tls", "true")
        out: dict[str, Any] = {
            "name": name,
            "type": "vmess",
            "server": j["add"].strip("[]"),
            "port": port,
            "uuid": j["id"],
            "alterId": int(j.get("aid", 0)),
            "cipher": "auto",
            "udp": True,
        }
        if tls:
            out["tls"] = True
            if j.get("host"):
                out["servername"] = j["host"]
        if net == "ws":
            out["network"] = "ws"
            out["ws-opts"] = {"path": j.get("path", "/")}
            if j.get("host"):
                out["ws-opts"]["headers"] = {"Host": j["host"]}
        elif net == "grpc":
            out["network"] = "grpc"
            if j.get("path"):
                out["grpc-opts"] = {"grpc-service-name": j["path"]}
        return out
    except Exception:
        return None


def _parse_vless(uri: str) -> dict[str, Any] | None:
    try:
        u = urllib.parse.urlparse(uri)
        if u.scheme.lower() != "vless" or not u.netloc:
            return None
        netloc = u.netloc
        if "@" not in netloc:
            return None
        uid, hostport = netloc.rsplit("@", 1)
        if ":" not in hostport:
            return None
        host, port_s = hostport.rsplit(":", 1)
        port = int(port_s)
        q = urllib.parse.parse_qs(u.query)
        name = _name_from_fragment(uri, "VLESS")

        def q1(key: str, default: str = "") -> str:
            v = q.get(key, [default])
            return v[0] if v else default

        sec = q1("security", "none").lower()
        out: dict[str, Any] = {
            "name": name,
            "type": "vless",
            "server": host.strip("[]"),
            "port": port,
            "uuid": urllib.parse.unquote(uid),
            "udp": True,
        }
        if sec in ("tls", "reality"):
            out["tls"] = True
            if q1("sni") or q1("peer"):
                out["servername"] = q1("sni") or q1("peer")
            if q1("fp"):
                out["client-fingerprint"] = q1("fp")
            if q1("flow"):
                out["flow"] = q1("flow")
        if q1("allowInsecure", "0") in ("1", "true", "True"):
            out["skip-cert-verify"] = True
        net = q1("type", "tcp").lower()
        if net == "ws":
            out["network"] = "ws"
            path = urllib.parse.unquote(q1("path", "/"))
            out["ws-opts"] = {"path": path or "/"}
            if q1("host"):
                out["ws-opts"]["headers"] = {"Host": q1("host")}
        return out
    except Exception:
        return None


def _parse_trojan(uri: str) -> dict[str, Any] | None:
    try:
        u = urllib.parse.urlparse(uri)
        if u.scheme.lower() != "trojan" or not u.netloc or "@" not in u.netloc:
            return None
        password, hostport = u.netloc.rsplit("@", 1)
        if ":" not in hostport:
            return None
        host, port_s = hostport.rsplit(":", 1)
        q = urllib.parse.parse_qs(u.query)
        name = _name_from_fragment(uri, "Trojan")

        def q1(key: str, default: str = "") -> str:
            v = q.get(key, [default])
            return v[0] if v else default

        out: dict[str, Any] = {
            "name": name,
            "type": "trojan",
            "server": host.strip("[]"),
            "port": int(port_s),
            "password": urllib.parse.unquote(password),
            "udp": True,
        }
        if q1("sni") or q1("peer"):
            out["sni"] = q1("sni") or q1("peer")
        if q1("allowInsecure", "0") in ("1", "true", "True"):
            out["skip-cert-verify"] = True
        return out
    except Exception:
        return None


def _parse_hysteria2(uri: str) -> dict[str, Any] | None:
    try:
        if uri.lower().startswith("hy2://"):
            uri = "hysteria2://" + uri.split("://", 1)[1]
        u = urllib.parse.urlparse(uri)
        if u.scheme.lower() != "hysteria2" or not u.netloc or "@" not in u.netloc:
            return None
        auth, hostport = u.netloc.rsplit("@", 1)
        if ":" not in hostport:
            return None
        host, port_s = hostport.rsplit(":", 1)
        q = urllib.parse.parse_qs(u.query)
        name = _name_from_fragment(uri, "Hysteria2")

        def q1(key: str, default: str = "") -> str:
            v = q.get(key, [default])
            return v[0] if v else default

        out: dict[str, Any] = {
            "name": name,
            "type": "hysteria2",
            "server": host.strip("[]"),
            "port": int(port_s),
            "password": urllib.parse.unquote(auth),
            "udp": True,
        }
        if q1("sni"):
            out["sni"] = q1("sni")
        if q1("insecure", "0") in ("1", "true", "True"):
            out["skip-cert-verify"] = True
        return out
    except Exception:
        return None
