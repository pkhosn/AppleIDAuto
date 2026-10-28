import asyncio
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.worker import Worker


class WorkerAutoDisableTwoFactorTest(unittest.TestCase):
    def _worker(self):
        store = MagicMock()
        queue = MagicMock()
        worker = Worker(store, queue)
        worker.panel = MagicMock()
        worker.panel.disable_account = AsyncMock(return_value={"status": True})
        worker.store.rollback_seen_message_ids = MagicMock(return_value=set())
        return worker

    def test_auto_disable_two_factor_success(self):
        worker = self._worker()
        info = {"username": "fixture@example.test"}
        with patch("app.worker.run_controlled_security_rollback",
                   AsyncMock(return_value={"status": "success"})) as rollback:
            ok, message, password, code = asyncio.run(worker._auto_disable_two_factor(1, info))
        self.assertTrue(ok)
        self.assertIsNone(code)
        worker.panel.disable_account.assert_awaited_once_with("fixture@example.test")
        rollback.assert_awaited_once_with(1, store=worker.store)

    def test_auto_disable_two_factor_fails_closed(self):
        worker = self._worker()
        info = {"username": "fixture@example.test"}
        with patch("app.worker.run_controlled_security_rollback",
                   AsyncMock(return_value={
                       "status": "failed", "stage": "mailbox_wait",
                       "error_code": "recovery_email_timeout",
                   })):
            ok, message, password, code = asyncio.run(worker._auto_disable_two_factor(1, info))
        self.assertFalse(ok)
        self.assertEqual("recovery_email_timeout", code)
        self.assertIn("已冻结", message)

    def test_auto_disable_two_factor_freeze_failure(self):
        worker = self._worker()
        worker.panel.disable_account = AsyncMock(side_effect=RuntimeError("panel down"))
        info = {"username": "fixture@example.test"}
        with patch("app.worker.run_controlled_security_rollback") as rollback:
            ok, message, password, code = asyncio.run(worker._auto_disable_two_factor(1, info))
        self.assertFalse(ok)
        self.assertEqual("auto_disable_freeze_failed", code)
        rollback.assert_not_awaited()

    def test_mailbox_has_security_rollback(self):
        worker = self._worker()
        info = {
            "username": "fixture@example.test",
            "email_recovery": {"configured": True, "account_match_required": False},
        }
        with patch("app.worker.BillionMailClient") as mailbox_cls:
            link = MagicMock()
            link.message_id = "<new>"
            mailbox_cls.return_value.find_link = AsyncMock(return_value=link)
            self.assertTrue(asyncio.run(worker._mailbox_has_security_rollback(1, info)))
        with patch("app.worker.BillionMailClient") as mailbox_cls:
            mailbox_cls.return_value.find_link = AsyncMock(return_value=None)
            self.assertFalse(asyncio.run(worker._mailbox_has_security_rollback(1, info)))

    def test_mailbox_check_ignores_already_processed_newest(self):
        # 最新一封回退邮件已处理过时，不再触发（旧邮件/陈旧链接会被挡住）。
        worker = self._worker()
        worker.store.rollback_seen_message_ids = MagicMock(return_value={"<new>"})
        info = {
            "username": "fixture@example.test",
            "email_recovery": {"configured": True, "account_match_required": False},
        }
        with patch("app.worker.BillionMailClient") as mailbox_cls:
            link = MagicMock()
            link.message_id = "<new>"
            mailbox_cls.return_value.find_link = AsyncMock(return_value=link)
            self.assertFalse(asyncio.run(worker._mailbox_has_security_rollback(1, info)))

    def test_mailbox_check_skips_when_not_configured(self):
        worker = self._worker()
        info = {"username": "fixture@example.test", "email_recovery": {"configured": False}}
        with patch("app.worker.BillionMailClient") as mailbox_cls:
            self.assertFalse(asyncio.run(worker._mailbox_has_security_rollback(1, info)))
            mailbox_cls.return_value.find_link.assert_not_called()


class TaskStoreRollbackSeenTest(unittest.TestCase):
    def test_rollback_seen_roundtrip(self):
        import os
        import tempfile
        from app.models import TaskStore

        fd, path = tempfile.mkstemp(suffix=".sqlite3")
        os.close(fd)
        try:
            store = TaskStore(path)
            self.assertEqual(set(), store.rollback_seen_message_ids(1))
            store.mark_rollback_seen(1, "<a>")
            store.mark_rollback_seen(1, "<a>")  # 幂等
            store.mark_rollback_seen(1, "<b>")
            store.mark_rollback_seen(1, "")     # 空值忽略
            self.assertEqual({"<a>", "<b>"}, store.rollback_seen_message_ids(1))
            self.assertEqual(set(), store.rollback_seen_message_ids(2))
        finally:
            os.remove(path)


if __name__ == "__main__":
    unittest.main()
