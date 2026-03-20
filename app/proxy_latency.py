"""
单节点延迟探测：尽量贴近 Clash「经代理访问测试 URL」的语义。

- http / socks5 / socks：httpx 走代理请求测试 URL（无需 Mihomo）
- ss / vmess / vless / trojan / hysteria2 / hysteria：需配置 Mihomo 可执行文件，由其做完整出站协议与延迟测量
- 兜底：TCP 建连（仅说明端口可连，不代表协议正确）
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import tempfile
import time
import urllib.parse
from typing import Any

import httpx
import yaml

logger = logging.getLogger("proxy_latency")

DEFAULT_URL_TEST = "https://www.gstatic.com/generate_204"

_TYPES_HTTPX = frozenset({"http", "socks5", "socks"})
_TYPES_MIHOMO_CORE = frozenset({"ss", "ssr", "vmess", "vless", "trojan", "hysteria2", "hysteria"})


def resolve_mihomo_executable(path: str) -> str | None:
    raw = (path or "").strip()
    p = raw.lstrip("\ufeff")
    if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
        p = p[1:-1].strip()
    p = os.path.normpath(os.path.expandvars(os.path.expanduser(p)))
    if not p:
        env = (os.environ.get("CLASH_HUB_MIHOMO") or os.environ.get("MIHOMO_PATH") or "").strip()
        if env:
            p = env.lstrip("\ufeff")
            if len(p) >= 2 and p[0] == p[-1] and p[0] in ("'", '"'):
                p = p[1:-1].strip()
            p = os.path.normpath(os.path.expandvars(os.path.expanduser(p)))
    if not p:
        w = shutil.which("mihomo") or shutil.which("mihomo.exe")
        return w
    ok = os.path.isfile(p)
    if ok:
        return p
    base = os.path.basename(p)
    w = shutil.which(base) if base else None
    return w


def _quote_proxy_component(s: str) -> str:
    return urllib.parse.quote(s, safe="")


def proxy_to_httpx_proxy_url(p: dict[str, Any]) -> str | None:
    """Clash 的 http / socks5 节点 -> httpx 的 proxy URL。"""
    t = (p.get("type") or "").lower()
    host = p.get("server")
    port = p.get("port")
    if not host or port is None:
        return None
    try:
        port_i = int(port)
    except (TypeError, ValueError):
        return None
    host_s = str(host).strip().strip("[]")
    user = str(p.get("username") or "").strip()
    pwd = str(p.get("password") or "").strip()

    auth = ""
    if user or pwd:
        auth = f"{_quote_proxy_component(user)}:{_quote_proxy_component(pwd)}@"

    if t == "http":
        return f"http://{auth}{host_s}:{port_i}"
    if t in ("socks5", "socks"):
        return f"socks5://{auth}{host_s}:{port_i}"
    return None


async def measure_url_test_httpx(
    proxy_url: str,
    test_url: str = DEFAULT_URL_TEST,
    timeout: float = 10.0,
) -> tuple[bool, float | None, str | None]:
    t0 = time.perf_counter()
    try:
        async with httpx.AsyncClient(
            proxy=proxy_url,
            timeout=httpx.Timeout(timeout),
            verify=True,
            follow_redirects=True,
        ) as client:
            resp = await client.get(test_url)
        t1 = time.perf_counter()
        ms = (t1 - t0) * 1000.0
        if resp.status_code in (200, 204):
            return True, ms, None
        return False, None, f"HTTP {resp.status_code}"
    except httpx.ProxyError as e:
        return False, None, f"代理错误: {e}"
    except httpx.HTTPError as e:
        return False, None, str(e)
    except Exception as e:
        return False, None, str(e)


def _pick_loopback_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _prepare_proxy_for_mihomo(p: dict[str, Any]) -> dict[str, Any]:
    import copy

    x = copy.deepcopy(p)
    x["name"] = "hub-probe"
    return x


def _build_mihomo_config(proxy: dict[str, Any], ec_port: int) -> dict[str, Any]:
    return {
        "port": 0,
        "socks-port": 0,
        "mixed-port": 0,
        "mode": "direct",
        "log-level": "error",
        "secret": "",
        "external-controller": f"127.0.0.1:{ec_port}",
        "proxies": [_prepare_proxy_for_mihomo(proxy)],
        "proxy-groups": [
            {
                "name": "default",
                "type": "select",
                "proxies": ["hub-probe", "DIRECT"],
            }
        ],
    }


async def measure_url_test_mihomo(
    proxy: dict[str, Any],
    exe: str,
    test_url: str = DEFAULT_URL_TEST,
    timeout: float = 12.0,
) -> tuple[bool, float | None, str | None]:
    """
    启动临时 Mihomo，调用 GET /proxies/hub-probe/delay 测量延迟（毫秒）。
    """
    ec_port = _pick_loopback_port()
    cfg = _build_mihomo_config(proxy, ec_port)
    tmp = tempfile.mkdtemp(prefix="clashhub_mihomo_")
    cfg_path = os.path.join(tmp, "config.yaml")
    proc: asyncio.subprocess.Process | None = None
    try:
        with open(cfg_path, "w", encoding="utf-8") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, default_flow_style=False, sort_keys=False)

        proc = await asyncio.create_subprocess_exec(
            exe,
            "-f",
            cfg_path,
            "-d",
            tmp,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )

        base = f"http://127.0.0.1:{ec_port}"
        boot_budget = min(45.0, max(25.0, float(timeout) + 10.0))
        deadline = time.monotonic() + boot_budget
        ready = False
        async with httpx.AsyncClient(timeout=httpx.Timeout(2.0)) as client:
            while time.monotonic() < deadline:
                try:
                    r = await client.get(f"{base}/version")
                    if r.status_code == 200:
                        ready = True
                        break
                except httpx.HTTPError:
                    pass
                if proc.returncode is not None:
                    err_b = await proc.stderr.read() if proc.stderr else b""
                    return False, None, f"Mihomo 已退出 (code={proc.returncode}): {err_b.decode(errors='replace')[:500]}"
                await asyncio.sleep(0.08)

        if not ready:
            return False, None, "Mihomo 控制端口未就绪（超时）"

        delay_timeout_ms = max(1000, min(int(timeout * 1000), 60000))
        params = {"url": test_url, "timeout": delay_timeout_ms}
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout + 5.0)) as client:
            r = await client.get(f"{base}/proxies/hub-probe/delay", params=params)

        try:
            data = r.json()
        except Exception:
            return False, None, f"Mihomo 返回非 JSON: HTTP {r.status_code} {r.text[:200]}"

        if r.status_code != 200:
            msg = data.get("message") or data.get("error") or r.text[:300]
            hint = ""
            if r.status_code == 503:
                hint = "（节点可能不可用，或协议字段与 Mihomo 不兼容；可在 Mihomo 客户端中单独验证该节点）"
            return False, None, f"Mihomo HTTP {r.status_code}: {msg}{hint}"

        if data.get("message"):
            return False, None, str(data["message"])

        d = data.get("delay")
        if d is None:
            return False, None, str(data)
        try:
            ms = float(d)
        except (TypeError, ValueError):
            return False, None, f"无效 delay: {d!r}"
        if ms <= 0:
            return False, None, data.get("message") or "delay<=0，可能未连通"
        return True, ms, None
    except FileNotFoundError:
        return False, None, f"找不到 Mihomo: {exe}"
    except Exception as e:
        logger.warning("Mihomo URL 测试异常: %s", e)
        return False, None, str(e)
    finally:
        if proc and proc.returncode is None:
            proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
        try:
            shutil.rmtree(tmp, ignore_errors=True)
        except Exception:
            pass


async def probe_single_proxy(
    proxy: dict[str, Any],
    timeout: float,
    mihomo_path: str,
) -> tuple[bool, float | None, str | None, str]:
    """
    返回 (ok, latency_ms, error, probe_kind)
    probe_kind: httpx | mihomo | tcp-fallback | none
    """
    from aggregator import measure_tcp_latency, proxy_tcp_endpoint

    ptype = (proxy.get("type") or "").lower()
    last_mihomo_err: str | None = None
    t_budget = min(float(timeout), 25.0)
    t_tcp = min(float(timeout), 8.0)

    if ptype in _TYPES_HTTPX:
        pu = proxy_to_httpx_proxy_url(proxy)
        if pu:
            ok, ms, err = await measure_url_test_httpx(pu, DEFAULT_URL_TEST, timeout=t_budget)
            if ok:
                return True, ms, None, "httpx"

    exe = resolve_mihomo_executable(mihomo_path)
    if exe:
        ok, ms, err = await measure_url_test_mihomo(proxy, exe, DEFAULT_URL_TEST, timeout=t_budget)
        last_mihomo_err = err
        if ok:
            return True, ms, None, "mihomo"
        if ptype in _TYPES_MIHOMO_CORE:
            return False, None, err or "Mihomo URL 测试失败", "mihomo"

    ep = proxy_tcp_endpoint(proxy)
    if not ep:
        if ptype in _TYPES_MIHOMO_CORE and not exe:
            return (
                False,
                None,
                "该协议需在「设置」中配置 Mihomo 可执行文件路径，或确保 mihomo 在 PATH 中",
                "none",
            )
        return False, None, "节点缺少 server/port", "none"

    ok_tcp, ms, terr = await measure_tcp_latency(ep[0], ep[1], t_tcp)
    if not ok_tcp:
        return False, None, terr or last_mihomo_err, "tcp"

    return True, ms, None, "tcp-fallback"
