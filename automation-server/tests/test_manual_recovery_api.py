import asyncio
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app import api
from app.models import TaskStore


class FakeStore:
    def __init__(self):
        self.logs = []

    def log(self, account_id, operation, status, message):
        self.logs.append((account_id, operation, status, message))


class ManualRecoveryApiTest(unittest.TestCase):
    def setUp(self):
        api.manual_recovery_runs.clear()
        api.manual_recovery_accounts.clear()

    def tearDown(self):
        api.manual_recovery_runs.clear()
        api.manual_recovery_accounts.clear()

    def test_retired_dynamic_pool_endpoints_return_not_found(self):
        app = FastAPI()
        app.include_router(api.router)
        with TestClient(app) as client:
            for method, path in (
                ('GET', '/localProxyPoolStatus'),
                ('POST', '/localProxyPoolTest'),
                ('POST', '/localProxyPoolNode'),
            ):
                with self.subTest(path=path):
                    self.assertEqual(404, client.request(method, path).status_code)

    def test_unsaved_mailbox_test_never_echoes_credentials(self):
        payload = api.MailboxConfigRequest(id=0, email="rescue@example.test", host="imap.example.test",
                                           port=993, username="rescue", password="TOP_SECRET", tls=True)
        with patch.object(api.BillionMailClient, "test_connection", new=AsyncMock(return_value=None)):
            result = asyncio.run(api.test_mailbox_config(payload, api.settings.token))
        self.assertTrue(result["status"])
        self.assertNotIn("TOP_SECRET", repr(result))

    def test_immediate_proxy_test_returns_safe_summary(self):
        class Panel:
            async def proxy_health_target(self, proxy_id): return {"id": proxy_id, "protocol": "http", "content": "secret@proxy.test:8080"}
        class Scheduler:
            def __init__(self, panel): pass
            async def _check_one(self, target):
                return {"status": False, "proxy_id": target["id"], "error_code": "proxy_auth_failed", "latency_ms": None}
        with patch.object(api, "PanelClient", return_value=Panel()), patch.object(api, "ProxyScheduler", Scheduler):
            result = asyncio.run(api.test_proxy(api.TaskRequest(id=9), api.settings.token))
        self.assertFalse(result["status"])
        self.assertEqual("静态 IP 认证失败", result["msg"])
        self.assertNotIn("secret", repr(result))

    def test_completed_run_keeps_only_safe_status_data_for_polling(self):
        async def fake_run(account_id, escrow_dir):
            self.assertEqual(7, account_id)
            return {
                "status": "success", "stage": "complete", "error_code": "",
                "job_ids": ["job-1", "job-2"], "password": "MUST_NOT_BE_EXPOSED",
            }

        store = FakeStore()
        with patch.object(api, "store", store), patch.object(api, "run_controlled_email_password_change", fake_run):
            asyncio.run(api._run_manual_recovery("a" * 32, 7, "email_password_recovery"))

        result = api.manual_recovery_runs["a" * 32]
        self.assertEqual("success", result["status"])
        self.assertEqual("complete", result["stage"])
        self.assertEqual(["job-1", "job-2"], result["job_ids"])
        self.assertNotIn("password", result)
        self.assertNotIn(7, api.manual_recovery_accounts)

    def test_failed_run_is_reported_as_terminal_and_releases_account_lock(self):
        async def fake_run(account_id, escrow_dir):
            return {"status": "failed", "stage": "mailbox_wait", "error_code": "recovery_email_timeout", "job_ids": []}

        api.manual_recovery_accounts.add(9)
        with patch.object(api, "store", FakeStore()), patch.object(api, "run_controlled_email_password_change", fake_run):
            asyncio.run(api._run_manual_recovery("b" * 32, 9, "email_password_recovery"))

        result = api.manual_recovery_runs["b" * 32]
        self.assertEqual("failed", result["status"])
        self.assertEqual("recovery_email_timeout", result["error_code"])
        self.assertNotIn(9, api.manual_recovery_accounts)

    def test_recovery_history_is_persistent_and_excludes_other_actions(self):
        with tempfile.TemporaryDirectory() as directory:
            store = TaskStore(str(Path(directory) / "tasks.sqlite3"))
            store.log(5, "email_password_recovery", "success", "阶段: complete")
            store.log(5, "restart", "success", "任务已进入队列")
            store.log(5, "security_rollback", "failed", "阶段: mailbox_wait; 原因: recovery_email_timeout")
            store.log(6, "email_password_recovery", "success", "其他账号")
            records = store.manual_recovery_logs(5)
        self.assertEqual(["security_rollback", "email_password_recovery"], [record["action"] for record in records])
        self.assertNotIn("其他账号", repr(records))


if __name__ == "__main__":
    unittest.main()
