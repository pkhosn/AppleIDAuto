import asyncio
import unittest
from unittest.mock import patch

import httpx

from app.notifier import notify_account_event


class NotifierTest(unittest.TestCase):
    def test_missing_configuration_is_skipped(self):
        self.assertEqual("not_configured", asyncio.run(notify_account_event(
            "fixture@example.test", "自动化任务", False, "失败",
        )))

    def test_chat_not_found_is_reported_without_credentials(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(
            400, json={"ok": False, "description": "Bad Request: chat not found"}, request=request,
        ))

        class Client(httpx.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=transport, **kwargs)

        with patch("app.notifier.httpx.AsyncClient", Client):
            result = asyncio.run(notify_account_event(
                "fixture@example.test", "自动化任务", False, "页面异常",
                bot_token="test-token", chat_id="123",
            ))
        self.assertEqual("chat_not_found", result)

    def test_notification_modes_filter_without_network_request(self):
        with patch("app.notifier.httpx.AsyncClient") as client:
            self.assertEqual("filtered", asyncio.run(notify_account_event(
                "full-account@example.test", "自动化任务", True, "成功",
                bot_token="test-token", chat_id="123", notify_mode="failure",
            )))
            self.assertEqual("filtered", asyncio.run(notify_account_event(
                "full-account@example.test", "自动化任务", False, "失败",
                bot_token="test-token", chat_id="123", notify_mode="success",
            )))
            client.assert_not_called()

    def test_matching_and_all_modes_send_full_account_and_redact_message_secrets(self):
        requests = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, json={"ok": True}, request=request)

        transport = httpx.MockTransport(handler)

        class Client(httpx.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=transport, **kwargs)

        with patch("app.notifier.httpx.AsyncClient", Client):
            self.assertEqual("sent", asyncio.run(notify_account_event(
                "complete-account@example.test", "自动化任务", True,
                "password=secret https://example.test/once", bot_token="test-token", chat_id="123",
                notify_mode="success",
            )))
            self.assertEqual("sent", asyncio.run(notify_account_event(
                "complete-account@example.test", "自动化任务", False,
                "普通失败", bot_token="test-token", chat_id="123", notify_mode="all",
            )))

        first_payload = requests[0].content.decode("utf-8")
        self.assertIn("complete-account@example.test", first_payload)
        self.assertNotIn("password=secret", first_payload)
        self.assertNotIn("https://example.test/once", first_payload)

    def test_unknown_mode_defaults_to_all(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(
            200, json={"ok": True}, request=request,
        ))

        class Client(httpx.AsyncClient):
            def __init__(self, **kwargs):
                super().__init__(transport=transport, **kwargs)

        with patch("app.notifier.httpx.AsyncClient", Client):
            self.assertEqual("sent", asyncio.run(notify_account_event(
                "fixture@example.test", "自动化任务", False, "失败",
                bot_token="test-token", chat_id="123", notify_mode="legacy-value",
            )))
