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
    """是否为 http(s) 远程订阅地址（与本地粘贴的 URI/ YAML 区分）。"""
    u = (url or "").strip().lower()
    return u.startswith("http://") or u.startswith("https://")


def looks_like_proxy_uri_line(line: str) -> bool:
    """行首是否为已知分享链接协议前缀（ss/vmess/vless 等）。"""
    s = (line or "").strip().lower()
    return any(s.startswith(p) for p in _URI_PREFIXES)


def _b64_decode(s: str) -> str:
    """Base64 / URL-safe Base64 解码为 UTF-8 文本；失败返回空串。"""
    s = s.strip()
    pad = s + "=" * (-len(s) % 4)
    for decoder in (base64.urlsafe_b64decode, base64.b64decode):
        try:
            return decoder(pad).decode("utf-8", errors="ignore")
        except Exception:
            continue
    return ""


def parse_single_proxy_uri(uri: str) -> dict[str, Any] | None:
    """将单条分享链接解析为 Clash proxy 字典；不支持的协议返回 None。"""
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
    """从 `#` 片段解析节点备注名；无则返回 default。"""
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


def proxy_dict_to_uri(proxy: dict) -> str | None:
    """将 Clash proxy 字典转换回分享链接 URI 字符串；不支持的协议返回 None。"""
    t = (proxy.get("type") or "").lower()
    if t == "vmess":
        return _clash_vmess_to_uri(proxy)
    if t == "vless":
        return _clash_vless_to_uri(proxy)
    if t == "ss":
        return _clash_ss_to_uri(proxy)
    if t == "ssr":
        return _clash_ssr_to_uri(proxy)
    if t == "trojan":
        return _clash_trojan_to_uri(proxy)
    if t == "hysteria2":
        return _clash_hysteria2_to_uri(proxy)
    return None


def _clash_vmess_to_uri(p: dict) -> str | None:
    """Clash vmess 字典 → vmess:// URI。"""
    try:
        name = str(p.get("name") or "VMess")
        net = str(p.get("network") or "tcp").lower()
        tls_val = "tls" if p.get("tls") else ""
        host_val = ""
        path_val = ""
        ws = p.get("ws-opts")
        if isinstance(ws, dict):
            path_val = str(ws.get("path") or "")
            headers = ws.get("headers")
            if isinstance(headers, dict):
                host_val = str(headers.get("Host") or "")
        grpc = p.get("grpc-opts")
        if isinstance(grpc, dict):
            path_val = str(grpc.get("grpc-service-name") or "")
        if not host_val and p.get("servername"):
            host_val = str(p["servername"])
        j = {
            "v": "2",
            "ps": name,
            "add": str(p["server"]),
            "port": str(p["port"]),
            "id": str(p["uuid"]),
            "aid": str(p.get("alterId") or 0),
            "scy": str(p.get("cipher") or "auto"),
            "net": net,
            "type": "",
            "host": host_val,
            "path": path_val,
            "tls": tls_val,
        }
        payload = base64.b64encode(json.dumps(j, ensure_ascii=False).encode()).decode()
        return "vmess://" + payload
    except Exception:
        return None


def _clash_vless_to_uri(p: dict) -> str | None:
    """Clash vless 字典 → vless:// URI。"""
    try:
        name = urllib.parse.quote(str(p.get("name") or "VLESS"), safe="")
        server = str(p["server"])
        port = str(p["port"])
        uid = urllib.parse.quote(str(p["uuid"]), safe="")
        params: dict[str, str] = {}
        if p.get("tls"):
            params["security"] = "tls"
        if p.get("servername"):
            params["sni"] = str(p["servername"])
        if p.get("client-fingerprint"):
            params["fp"] = str(p["client-fingerprint"])
        if p.get("flow"):
            params["flow"] = str(p["flow"])
        if p.get("skip-cert-verify"):
            params["allowInsecure"] = "1"
        net = str(p.get("network") or "tcp")
        params["type"] = net
        ws = p.get("ws-opts")
        if net == "ws" and isinstance(ws, dict):
            params["path"] = urllib.parse.quote(str(ws.get("path") or "/"), safe="")
            headers = ws.get("headers")
            if isinstance(headers, dict) and headers.get("Host"):
                params["host"] = str(headers["Host"])
        query = "&".join(f"{k}={v}" for k, v in params.items())
        uri = f"vless://{uid}@{server}:{port}"
        if query:
            uri += "?" + query
        return uri + "#" + name
    except Exception:
        return None


def _clash_ss_to_uri(p: dict) -> str | None:
    """Clash ss 字典 → ss:// URI（SIP002 格式）。"""
    try:
        name = urllib.parse.quote(str(p.get("name") or "SS"), safe="")
        method = str(p.get("cipher") or "aes-256-gcm")
        password = str(p.get("password") or "")
        server = str(p["server"])
        port = str(p["port"])
        userinfo = base64.urlsafe_b64encode(f"{method}:{password}".encode()).decode().rstrip("=")
        return f"ss://{userinfo}@{server}:{port}#{name}"
    except Exception:
        return None


def _clash_ssr_to_uri(p: dict) -> str | None:
    """Clash ssr 字典 → ssr:// URI。"""
    try:
        server = str(p["server"])
        port = str(p["port"])
        protocol = str(p.get("protocol") or "origin")
        method = str(p.get("cipher") or "none")
        obfs = str(p.get("obfs") or "plain")
        password_b64 = base64.b64encode(str(p.get("password") or "").encode()).decode().rstrip("=")
        main = f"{server}:{port}:{protocol}:{method}:{obfs}:{password_b64}"
        name_b64 = base64.b64encode(str(p.get("name") or "SSR").encode()).decode().rstrip("=")
        params = f"remarks={name_b64}"
        if p.get("obfs-param"):
            ob = base64.b64encode(str(p["obfs-param"]).encode()).decode().rstrip("=")
            params += f"&obfsparam={ob}"
        if p.get("protocol-param"):
            pp = base64.b64encode(str(p["protocol-param"]).encode()).decode().rstrip("=")
            params += f"&protoparam={pp}"
        raw = f"{main}?{params}"
        encoded = base64.b64encode(raw.encode()).decode().rstrip("=")
        return f"ssr://{encoded}"
    except Exception:
        return None


def _clash_trojan_to_uri(p: dict) -> str | None:
    """Clash trojan 字典 → trojan:// URI。"""
    try:
        name = urllib.parse.quote(str(p.get("name") or "Trojan"), safe="")
        password = urllib.parse.quote(str(p.get("password") or ""), safe="")
        server = str(p["server"])
        port = str(p["port"])
        params: dict[str, str] = {}
        if p.get("sni"):
            params["sni"] = str(p["sni"])
        if p.get("skip-cert-verify"):
            params["allowInsecure"] = "1"
        query = "&".join(f"{k}={v}" for k, v in params.items())
        uri = f"trojan://{password}@{server}:{port}"
        if query:
            uri += "?" + query
        return uri + "#" + name
    except Exception:
        return None


def _clash_hysteria2_to_uri(p: dict) -> str | None:
    """Clash hysteria2 字典 → hysteria2:// URI。"""
    try:
        name = urllib.parse.quote(str(p.get("name") or "Hysteria2"), safe="")
        auth = urllib.parse.quote(str(p.get("password") or ""), safe="")
        server = str(p["server"])
        port = str(p["port"])
        params: dict[str, str] = {}
        if p.get("sni"):
            params["sni"] = str(p["sni"])
        if p.get("skip-cert-verify"):
            params["insecure"] = "1"
        query = "&".join(f"{k}={v}" for k, v in params.items())
        uri = f"hysteria2://{auth}@{server}:{port}"
        if query:
            uri += "?" + query
        return uri + "#" + name
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
