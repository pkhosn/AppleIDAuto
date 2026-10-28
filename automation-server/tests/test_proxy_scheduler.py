import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import httpx

from app.proxy_scheduler import (
    ProxyScheduler,
    _assert_public_proxy,
    _assert_public_url,
    normalize_endpoint,
    parse_source_payload,
    proxy_error_code,
    proxy_url,
)


class ProxySchedulerTest(unittest.TestCase):
    def test_plain_and_json_sources_are_normalized_and_deduplicated(self):
        self.assertEqual(
            ["127.0.0.1:8080", "user:pass@proxy.test:1080"],
            parse_source_payload(b"127.0.0.1:8080\nuser:pass@proxy.test:1080\n127.0.0.1:8080"),
        )
        body = b'{"data":[{"ip":"proxy.test","port":8080},"proxy2.test:1080"]}'
        self.assertEqual(["proxy.test:8080", "proxy2.test:1080"], parse_source_payload(body))

    def test_invalid_port_is_rejected_and_credentials_are_encoded(self):
        self.assertIsNone(normalize_endpoint("proxy.test:99999"))
        self.assertEqual("http://user:p%40ss@proxy.test:8080",
                         proxy_url("http", "user:p%40ss@proxy.test:8080"))

    def test_supported_static_proxy_formats_are_canonicalized(self):
        for password in ("secret", "1234"):
            expected = f"user:{password}@proxy.test:8080"
            for endpoint in (
                f"proxy.test:8080:user:{password}",
                f"user:{password}:proxy.test:8080",
                f"user:{password}@proxy.test:8080",
                f"proxy.test:8080@user:{password}",
            ):
                with self.subTest(endpoint=endpoint):
                    self.assertEqual(expected, normalize_endpoint(endpoint))
                    self.assertEqual(f"socks5://user:{password}@proxy.test:8080", proxy_url("socks5", endpoint))

    def test_private_source_address_is_blocked(self):
        settings = SimpleNamespace(allow_private_proxy_source=False)
        resolved = [(2, 1, 6, "", ("127.0.0.1", 80))]
        with patch("app.proxy_scheduler.settings", settings), patch(
            "app.proxy_scheduler.socket.getaddrinfo", return_value=resolved
        ):
            with self.assertRaisesRegex(ValueError, "source_address_blocked"):
                asyncio.run(_assert_public_url("http://source.test/list"))

    def test_private_proxy_address_is_blocked_before_connection(self):
        for address in ("127.0.0.1", "10.0.0.5", "169.254.169.254", "::1", "fc00::1"):
            resolved = [(2, 1, 6, "", (address, 8080))]
            with self.subTest(address=address), patch(
                "app.proxy_scheduler.socket.getaddrinfo", return_value=resolved
            ):
                with self.assertRaisesRegex(ValueError, "proxy_address_blocked"):
                    asyncio.run(_assert_public_proxy("proxy.test:8080"))

    def test_proxy_errors_are_safe_and_actionable(self):
        self.assertEqual("proxy_target_forbidden", proxy_error_code(httpx.ProxyError("403 Forbidden")))
        self.assertEqual("proxy_auth_failed", proxy_error_code(httpx.ProxyError("407 Proxy Authentication Required")))
        self.assertEqual("proxy_handshake_failed", proxy_error_code(httpx.ProxyError("bad gateway")))
        self.assertEqual("proxy_protocol_error", proxy_error_code(httpx.ProtocolError("unexpected reply")))

    def test_immediate_test_returns_safe_failure_and_records_health(self):
        class Panel:
            def __init__(self): self.calls = []
            async def update_proxy_health(self, *args, **kwargs): self.calls.append((args, kwargs))
        panel = Panel()
        result = asyncio.run(ProxyScheduler(panel)._check_one({"id": 7, "protocol": "http", "content": "invalid"}))
        self.assertFalse(result["status"])
        self.assertEqual("invalid_proxy", result["error_code"])
        self.assertEqual(7, panel.calls[0][0][0])
        self.assertNotIn("content", result)


if __name__ == "__main__":
    unittest.main()
