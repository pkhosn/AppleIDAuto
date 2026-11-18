import asyncio
from dataclasses import replace
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from app.worker import Worker, settings


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


class LegacyPasswordEscrowTest(unittest.TestCase):
    def test_candidate_survives_uncertain_result_and_writeback_failure(self):
        import tempfile
        from pathlib import Path
        from app.models import TaskStore
        for outcome in ('uncertain', 'success', 'report_failure'):
            with self.subTest(outcome=outcome), tempfile.TemporaryDirectory() as tmp:
                store = TaskStore(str(Path(tmp) / 'tasks.sqlite3'))
                worker = Worker(store, MagicMock())
                worker.panel = MagicMock()
                worker.panel.task_info = AsyncMock(return_value={
                    'username': 'fixture@example.test', 'enable': True,
                })
                worker.panel.disable_account = AsyncMock()
                worker.panel.report = AsyncMock(side_effect=RuntimeError('offline') if outcome == 'report_failure' else None)
                async def execute(info):
                    password = info['_prepare_password']()
                    files = list((Path(tmp) / 'scheduled-password-escrow').glob('*.secret'))
                    self.assertEqual(password, files[0].read_text())
                    self.assertEqual(0o600, files[0].stat().st_mode & 0o777)
                    info['_mutation_started'] = True
                    return outcome != 'uncertain', 'result', password if outcome != 'uncertain' else None, None, None
                worker.executor.run = execute
                with (
                    patch('app.worker.settings', replace(settings, database_path=str(Path(tmp) / 'tasks.sqlite3'))),
                    patch('app.worker.notify_account_event', AsyncMock(return_value='not_configured')),
                ):
                    asyncio.run(worker._run_locked(1))
                files = list((Path(tmp) / 'scheduled-password-escrow').glob('*.secret'))
                self.assertEqual(outcome != 'success', bool(files))
                if outcome != 'success':
                    worker.panel.disable_account.assert_awaited_once()
                else:
                    worker.panel.report.assert_awaited_once()

    def test_unresolved_candidate_blocks_new_browser_operation(self):
        import tempfile
        from pathlib import Path
        from app.models import TaskStore
        from scripts.controlled_recover import load_or_create_escrow
        with tempfile.TemporaryDirectory() as tmp:
            load_or_create_escrow(Path(tmp) / 'scheduled-password-escrow', 1, lambda: 'FixtureNewPass1@')
            worker = Worker(TaskStore(str(Path(tmp) / 'tasks.sqlite3')), MagicMock())
            worker.panel = MagicMock()
            worker.panel.task_info = AsyncMock(return_value={'username': 'fixture@example.test', 'enable': True})
            worker.panel.disable_account = AsyncMock()
            worker.executor.run = AsyncMock()
            with patch('app.worker.settings', replace(settings, database_path=str(Path(tmp) / 'tasks.sqlite3'))):
                asyncio.run(worker._run_locked(1))
            worker.executor.run.assert_not_awaited()
            worker.panel.disable_account.assert_awaited_once()


class LegacyEmailRecoveryTriggerTest(unittest.IsolatedAsyncioTestCase):
    async def test_only_explicit_invalid_password_triggers_recovery(self):
        import tempfile
        from pathlib import Path
        from app.models import TaskStore
        for code in ('password_invalid', 'authentication_challenge_unrendered'):
            with self.subTest(code=code), tempfile.TemporaryDirectory() as directory:
                store = TaskStore(str(Path(directory) / 'tasks.sqlite3'))
                worker = Worker(store, MagicMock())
                worker.panel = MagicMock()
                worker.panel.task_info = AsyncMock(return_value={
                    'username': 'fixture@example.test', 'enable': True,
                    'recovery_methods': ['email_password'],
                    'email_recovery': {'configured': True},
                })
                worker.panel.report = AsyncMock()
                worker.panel.disable_account = AsyncMock()
                worker.executor.run = AsyncMock(return_value=(False, code, None, code, None))
                worker._auto_email_recovery = AsyncMock(return_value=(True, 'recovered', None, None))
                with (
                    patch('app.worker.settings', replace(settings, database_path=str(Path(directory) / 'tasks.sqlite3'))),
                    patch('app.worker.notify_account_event', AsyncMock(return_value='not_configured')),
                ):
                    await worker._run_locked(1)
                if code == 'password_invalid':
                    worker._auto_email_recovery.assert_awaited_once()
                    self.assertEqual('success', store.get(1)['status'])
                else:
                    worker._auto_email_recovery.assert_not_awaited()
                    worker.panel.disable_account.assert_not_awaited()
                    worker.panel.report.assert_awaited_once_with('fixture@example.test', False, code, None, profile=None)
                    self.assertEqual('failed', store.get(1)['status'])
                store.db.close()


class LockedEmailRecoveryTest(unittest.IsolatedAsyncioTestCase):
    async def test_unlock_requires_separate_permission_and_valid_mailbox(self):
        import tempfile
        from pathlib import Path
        from app.models import TaskStore
        for methods, configured in [(['email_password'], True), (['email_unlock'], False), (['email_unlock'], True)]:
            with self.subTest(methods=methods, configured=configured), tempfile.TemporaryDirectory() as tmp:
                worker = Worker(TaskStore(str(Path(tmp) / 'tasks.sqlite3')), MagicMock())
                info = {'username': 'fixture@example.test', 'enable': True, 'recovery_methods': methods,
                        'email_recovery': {'configured': configured}}
                worker.panel.task_info = AsyncMock(return_value=info)
                worker.panel.report = AsyncMock()
                worker.executor.run = AsyncMock(return_value=(False, 'locked', None, 'locked', None))
                worker._auto_email_recovery = AsyncMock(return_value=(True, 'unlocked', None, None))
                with (patch('app.worker.settings', replace(settings, database_path=str(Path(tmp) / 'tasks.sqlite3'))),
                      patch('app.worker.notify_account_event', AsyncMock(return_value='not_configured'))):
                    await worker._run_locked(1)
                if methods == ['email_unlock'] and configured:
                    worker._auto_email_recovery.assert_awaited_once()
                    self.assertEqual('locked', worker._auto_email_recovery.call_args.args[1]['_recovery_reason'])
                else:
                    worker._auto_email_recovery.assert_not_awaited()
                worker.store.db.close()

    async def test_lock_recovery_reports_failure_reason_and_preserves_frozen_state(self):
        worker = Worker(MagicMock(), MagicMock())
        info = {'username': 'fixture@example.test', 'enable': True, 'recovery_methods': ['email_unlock'],
                'email_recovery': {'configured': True}, '_recovery_reason': 'locked'}
        worker.panel.task_info = AsyncMock(return_value=info)
        worker.panel.disable_account = AsyncMock()
        with patch('app.worker.run_controlled_email_password_change', AsyncMock(return_value={
            'status': 'failed', 'error_code': 'recovery_email_timeout', 'stage': 'mailbox_wait',
        })):
            ok, message, password, code = await worker._auto_email_recovery(1, info)
        self.assertFalse(ok)
        self.assertIn('账号安全锁定', message)
        self.assertNotIn('密码校验失败', message)
        self.assertEqual('recovery_email_timeout', code)
        worker.panel.disable_account.assert_awaited_once()

    async def test_revoked_unlock_permission_stops_before_freezing(self):
        worker = Worker(MagicMock(), MagicMock())
        info = {'username': 'fixture@example.test', 'enable': True, 'recovery_methods': ['email_unlock'],
                'email_recovery': {'configured': True}, '_recovery_reason': 'locked'}
        worker.panel.task_info = AsyncMock(return_value={**info, 'recovery_methods': []})
        worker.panel.disable_account = AsyncMock()
        with patch('app.worker.run_controlled_email_password_change', AsyncMock()) as recover:
            result = await worker._auto_email_recovery(1, info)
        self.assertFalse(result[0])
        recover.assert_not_awaited()
        worker.panel.disable_account.assert_not_awaited()


class TwoFactorRecoveryRoutingTest(unittest.IsolatedAsyncioTestCase):
    def worker(self):
        worker = Worker(MagicMock(), MagicMock())
        self.info = {'username': 'fixture@example.test', 'enable': False, 'enable_auto_disable_two_factor': True,
                     'recovery_methods': ['email_password', 'email_security'], 'email_recovery': {'configured': True}}
        worker.panel.disable_account = AsyncMock()
        worker.panel.task_info = AsyncMock(return_value=self.info)
        worker._auto_email_recovery = AsyncMock(return_value=(True, 'email recovered', None, None))
        return worker

    async def test_missing_or_expired_link_uses_one_authorized_email_recovery(self):
        for code, stage, jobs in [('security_rollback_previous_attempt_unconfirmed', 'mailbox_lookup', []),
                                  ('recovery_email_timeout', 'mailbox_wait', []),
                                  ('security_rollback_link_expired', 'security_rollback', ['expired-job'])]:
            with self.subTest(code=code):
                worker = self.worker()
                with patch('app.worker.run_controlled_security_rollback', AsyncMock(return_value={
                    'status': 'failed', 'error_code': code, 'stage': stage, 'job_ids': jobs,
                })):
                    result = await worker._auto_disable_two_factor(1, self.info)
                self.assertTrue(result[0])
                worker._auto_email_recovery.assert_awaited_once()
                self.assertEqual('two_factor', worker._auto_email_recovery.call_args.args[1]['_recovery_reason'])
                self.assertEqual('email_password', self.info['_two_factor_recovery_path'])

    async def test_uncertain_submission_never_starts_a_second_password_change(self):
        worker = self.worker()
        with patch('app.worker.run_controlled_security_rollback', AsyncMock(return_value={
            'status': 'failed', 'error_code': 'recovery_email_timeout',
            'stage': 'mailbox_confirmation', 'job_ids': ['submitted-job'],
        })):
            result = await worker._auto_disable_two_factor(1, self.info)
        self.assertFalse(result[0])
        worker._auto_email_recovery.assert_not_awaited()

    async def test_revoked_password_permission_prevents_fallback(self):
        worker = self.worker()
        worker.panel.task_info.return_value = {**self.info, 'recovery_methods': ['email_security']}
        with patch('app.worker.run_controlled_security_rollback', AsyncMock(return_value={
            'status': 'failed', 'error_code': 'recovery_email_timeout', 'stage': 'mailbox_wait',
        })):
            result = await worker._auto_disable_two_factor(1, self.info)
        self.assertFalse(result[0])
        worker._auto_email_recovery.assert_not_awaited()

    async def test_trusted_device_requirement_is_terminal_and_not_reported_as_password_failure(self):
        worker = self.worker()
        # Exercise the real email recovery method, with Apple's operation mocked.
        del worker._auto_email_recovery
        with patch('app.worker.run_controlled_email_password_change', AsyncMock(return_value={
            'status': 'failed', 'error_code': 'two_factor', 'stage': 'email_request',
        })):
            result = await worker._auto_email_recovery(1, {**self.info, '_recovery_reason': 'two_factor'})
        self.assertFalse(result[0])
        self.assertEqual('two_factor_verification_required', result[3])
        self.assertIn('受信任设备或电话号码', result[1])
