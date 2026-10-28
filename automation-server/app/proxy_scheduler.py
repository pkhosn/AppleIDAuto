import asyncio
import ipaddress
import json
import re
import socket
import time
from urllib.parse import quote, unquote, urljoin, urlparse

import httpx

from .config import settings
from .panel_client import PanelClient


ENDPOINT_RE = re.compile(
    r"^(?:(?P<user>[^:@\s]+):(?P<password>[^@\s]+)@)?"
    r"(?P<host>\[[0-9a-fA-F:]+\]|[A-Za-z0-9._-]+):(?P<port>\d{1,5})$"
)
HOST_PORT_RE = re.compile(
    r"^(?P<host>\[[0-9a-fA-F:]+\]|[A-Za-z0-9._-]+):(?P<port>\d{1,5})$"
)
AUTH_RE = re.compile(r"^(?P<user>[^:@\s]+):(?P<password>[^@\s]+)$")


def _host_port(value: str):
    match = HOST_PORT_RE.fullmatch(value)
    if not match or not 1 <= int(match.group("port")) <= 65535:
        return None
    return match.group("host"), match.group("port")


def normalize_endpoint(value: str) -> str | None:
    value = value.strip().strip('"\'')
    if not value or len(value) > 512 or re.search(r"\s", value):
        return None
    if "@" not in value:
        direct = _host_port(value)
        if direct:
            return f"{direct[0]}:{direct[1]}"
        parts = value.split(":")
        if len(parts) != 4 or any(not part for part in parts):
            return None
        first, second, third, fourth = parts
        host_first = bool(re.fullmatch(r"[A-Za-z0-9._-]+", first)) and second.isdigit() and 1 <= int(second) <= 65535
        auth_first = bool(re.fullmatch(r"[A-Za-z0-9._-]+", third)) and fourth.isdigit() and 1 <= int(fourth) <= 65535
        if host_first and auth_first:
            if "." in third and "." not in first:
                host_first = False
            else:
                auth_first = False
        if host_first:
            return f"{third}:{fourth}@{first}:{second}"
        if auth_first:
            return f"{first}:{second}@{third}:{fourth}"
        return None
    if value.count("@") != 1:
        return None
    left, right = value.split("@", 1)
    left_auth, right_host = AUTH_RE.fullmatch(left), _host_port(right)
    left_host, right_auth = _host_port(left), AUTH_RE.fullmatch(right)
    if left_auth and right_host and left_host and right_auth:
        if "." in left_host[0] and "." not in right_host[0]:
            return f"{right_auth.group('user')}:{right_auth.group('password')}@{left_host[0]}:{left_host[1]}"
    if left_auth and right_host:
        return f"{left_auth.group('user')}:{left_auth.group('password')}@{right_host[0]}:{right_host[1]}"
    if left_host and right_auth:
        return f"{right_auth.group('user')}:{right_auth.group('password')}@{left_host[0]}:{left_host[1]}"
    return None


def parse_source_payload(body: bytes, max_endpoints: int = 500) -> list[str]:
    text = body.decode("utf-8", errors="ignore").strip()
    candidates = []
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            payload = next((payload[key] for key in ("data", "list", "proxies") if key in payload), [])
        if isinstance(payload, list):
            for item in payload:
                if isinstance(item, str):
                    candidates.append(item)
                elif isinstance(item, dict):
                    host, port = item.get("host", item.get("ip")), item.get("port")
                    if host and port:
                        auth = ""
                        if item.get("username") is not None:
                            auth = f"{item['username']}:{item.get('password', '')}@"
                        candidates.append(f"{auth}{host}:{port}")
    except (json.JSONDecodeError, TypeError):
        candidates = re.split(r"[\s,;]+", text)
    result = []
    seen = set()
    for candidate in candidates:
        endpoint = normalize_endpoint(str(candidate))
        if endpoint and endpoint not in seen:
            result.append(endpoint)
            seen.add(endpoint)
            if len(result) >= max_endpoints:
                break
    return result


def proxy_url(protocol: str, content: str) -> str:
    endpoint = normalize_endpoint(content)
    if protocol not in {"http", "socks5"} or not endpoint:
        raise ValueError("invalid_proxy")
    match = ENDPOINT_RE.fullmatch(endpoint)
    auth = ""
    if match.group("user") is not None:
        user = quote(unquote(match.group("user")), safe="")
        password = quote(unquote(match.group("password")), safe="")
        auth = f"{user}:{password}@"
    return f"{protocol}://{auth}{match.group('host')}:{match.group('port')}"


def proxy_error_code(exc: Exception) -> str:
    """Return a safe, actionable proxy health error code.

    Do not persist exception messages: proxy library messages can embed an
    endpoint and its credentials.  Bright Data (and similar providers) return
    an HTTP status during a CONNECT tunnel failure, so distinguish a blocked
    Apple destination from invalid proxy authentication.
    """
    if isinstance(exc, ValueError):
        return str(exc)
    if isinstance(exc, httpx.ProxyError):
        message = str(exc)
        if "407" in message:
            return "proxy_auth_failed"
        if "403" in message:
            return "proxy_target_forbidden"
        return "proxy_handshake_failed"
    if isinstance(exc, httpx.ProtocolError):
        return "proxy_protocol_error"
    if isinstance(exc, (httpx.ConnectTimeout, httpx.ReadTimeout, httpx.ConnectError)):
        return "proxy_connect_failed"
    if isinstance(exc, RuntimeError) and str(exc) == "target_unreachable":
        return "target_unreachable"
    return f"proxy_{type(exc).__name__.lower()}"


async def _assert_public_url(url: str):
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("source_url_invalid")
    if settings.allow_private_proxy_source:
        return
    infos = await asyncio.wait_for(
        asyncio.to_thread(
            socket.getaddrinfo, parsed.hostname, parsed.port or 443, type=socket.SOCK_STREAM
        ),
        timeout=5,
    )
    for info in infos:
        address = ipaddress.ip_address(info[4][0])
        if not address.is_global:
            raise ValueError("source_address_blocked")


async def _assert_public_proxy(content: str):
    endpoint = normalize_endpoint(content)
    match = ENDPOINT_RE.fullmatch(endpoint or "")
    if not match:
        raise ValueError("invalid_proxy")
    host = match.group("host").strip("[]")
    infos = await asyncio.wait_for(
        asyncio.to_thread(socket.getaddrinfo, host, int(match.group("port")), type=socket.SOCK_STREAM),
        timeout=5,
    )
    if not infos or any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise ValueError("proxy_address_blocked")


async def fetch_source(url: str) -> bytes:
    current = url
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for _ in range(4):
            await _assert_public_url(current)
            async with client.stream("GET", current, headers={"User-Agent": "AppleidProxyPool/1.0"}) as response:
                if response.status_code in {301, 302, 303, 307, 308}:
                    location = response.headers.get("location")
                    if not location:
                        raise ValueError("source_redirect_invalid")
                    current = urljoin(current, location)
                    continue
                response.raise_for_status()
                chunks, size = [], 0
                async for chunk in response.aiter_bytes():
                    size += len(chunk)
                    if size > settings.proxy_source_max_bytes:
                        raise ValueError("source_too_large")
                    chunks.append(chunk)
                return b"".join(chunks)
    raise ValueError("source_redirect_limit")


class ProxyScheduler:
    def __init__(self, panel: PanelClient | None = None):
        self.panel = panel or PanelClient()
        self.health_semaphore = asyncio.Semaphore(settings.proxy_health_concurrency)

    async def loop(self):
        while True:
            await self.run_once()
            await asyncio.sleep(settings.proxy_loop_interval)

    async def run_once(self):
        await self._sync_sources()
        await self._check_proxies()

    async def _sync_sources(self):
        try:
            sources = await self.panel.proxy_sources()
        except Exception:
            return
        for source in sources:
            source_id = int(source.get("id", 0))
            try:
                body = await fetch_source(str(source.get("source_url", "")))
                endpoints = parse_source_payload(body, settings.proxy_source_max_endpoints)
                if not endpoints:
                    raise ValueError("source_empty")
                await self.panel.sync_proxy_source(source_id, endpoints)
            except Exception as exc:
                code = str(exc) if isinstance(exc, ValueError) else f"source_{type(exc).__name__.lower()}"
                try:
                    await self.panel.sync_proxy_source(source_id, [], code)
                except Exception:
                    pass

    async def _check_proxies(self):
        try:
            proxies = await self.panel.proxy_health_tasks(settings.proxy_health_concurrency * 5)
        except Exception:
            return
        await asyncio.gather(*(self._check_one(proxy) for proxy in proxies))

    async def _check_one(self, proxy: dict):
        async with self.health_semaphore:
            proxy_id = int(proxy.get("id", 0))
            started = time.monotonic()
            try:
                content = str(proxy.get("content", ""))
                await _assert_public_proxy(content)
                url = proxy_url(str(proxy.get("protocol", "")), content)
                async with httpx.AsyncClient(proxy=url, timeout=15, follow_redirects=True) as client:
                    ip_response = await client.get(settings.proxy_check_url)
                    ip_response.raise_for_status()
                    try:
                        exit_ip = ip_response.json().get("ip", "")
                    except (json.JSONDecodeError, AttributeError):
                        exit_ip = ip_response.text.strip()
                    ipaddress.ip_address(exit_ip)
                    target = await client.get(settings.proxy_target_url)
                    if target.status_code >= 500:
                        raise RuntimeError("target_unreachable")
                latency = int((time.monotonic() - started) * 1000)
                await self.panel.update_proxy_health(proxy_id, True, exit_ip, latency)
                return {"status": True, "proxy_id": proxy_id, "exit_ip": exit_ip,
                        "latency_ms": latency, "health_status": "healthy"}
            except Exception as exc:
                code = proxy_error_code(exc)
                try:
                    await self.panel.update_proxy_health(proxy_id, False, error_code=code)
                except Exception:
                    pass
                return {"status": False, "proxy_id": proxy_id, "exit_ip": "",
                        "latency_ms": None, "health_status": "failed", "error_code": code}
