import asyncio
import copy
import tempfile
import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from app.models import TaskStore
from app.feature_schedule import FeatureSchedule, ORDER
from app.feature_runner import FeatureRunner
from app.executor import AccountExecutor
from app.scheduler import Scheduler
from app.worker import Worker
from app.config import settings as app_settings


def configuration():
    return {'version': 1, 'active': True, 'features': {
        key: {'enabled': True, 'source': 'global', 'effective_minutes': minutes}
        for key, minutes in zip(ORDER, (30, 30, 360, 30, 1440))}}


class FeatureScheduleTest(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = str(Path(self.directory.name) / 'tasks.sqlite3')
        self.store = TaskStore(self.path)
        self.now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        self.f = FeatureSchedule(self.store, lambda: self.now)
        self.config = configuration()
        self.f.sync(1, self.config)

    def tearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    def complete(self):
        self.f.start(1, ORDER)
        self.f.finish(1, ORDER, 'success')

    def test_schedule_sync_does_not_end_recovery_when_account_is_frozen(self):
        self.store.upsert(1, 'running', '正在关闭双重认证')
        scheduler = Scheduler(self.store, type('Queue', (), {'remove': AsyncMock()})())
        snapshots = [{'id': 1, 'enable': False, 'schedule': self.config}]
        with patch('app.scheduler.PanelClient') as panel:
            panel.return_value.automation_schedules = AsyncMock(return_value=snapshots)
            panel.return_value.report_schedule_states = AsyncMock()
            asyncio.run(scheduler.sync())
        self.assertEqual(self.store.get(1)['status'], 'running')
        self.assertEqual(self.store.get(1)['message'], '正在关闭双重认证')
        self.store.upsert(1, 'failed', '恢复超时')
        with patch('app.scheduler.PanelClient') as panel:
            panel.return_value.automation_schedules = AsyncMock(return_value=snapshots)
            panel.return_value.report_schedule_states = AsyncMock()
            asyncio.run(scheduler.sync())
        self.assertEqual(self.store.get(1)['status'], 'failed')
        self.assertEqual(self.store.get(1)['message'], '恢复超时')

    def test_legacy_profile_uncertain_is_reconciled_once_only_when_enabled(self):
        self.store.db.execute("UPDATE feature_schedule SET result='uncertain',next_run_at=NULL WHERE account_id=1")
        self.f.sync(1, self.config, False)
        self.assertIsNone(self.f.rows(1)['profile_restore']['reconcile_at'])
        self.f.sync(1, self.config, True)
        scheduled = self.f.rows(1)['profile_restore']['reconcile_at']
        self.assertIsNotNone(scheduled)
        self.assertIsNone(self.f.rows(1)['device_cleanup']['reconcile_at'])
        self.now += timedelta(minutes=1)
        self.f.sync(1, self.config)
        self.assertEqual(scheduled, self.f.rows(1)['profile_restore']['reconcile_at'])
        self.f.finish_reconciliation(1, 'profile_restore', 'uncertain', 'incomplete')
        self.f.finish_reconciliation(1, 'profile_restore', 'uncertain', 'incomplete')
        self.f.sync(1, self.config)
        self.assertIsNone(self.f.rows(1)['profile_restore']['reconcile_at'])

    def test_profile_mismatch_after_reconciliation_runs_immediately(self):
        self.f.finish(1, ['profile_restore'], 'uncertain')
        self.f.finish_reconciliation(1, 'profile_restore', 'failed', 'confirmed mismatch')
        self.assertIn('profile_restore', self.f.due(1))
        self.f.finish(1, ['device_cleanup'], 'uncertain')
        self.f.finish_reconciliation(1, 'device_cleanup', 'failed', 'target present')
        self.assertNotIn('device_cleanup', self.f.due(1))

    def test_independent_cadence_and_completion_clock(self):
        self.f.start(1, ORDER)
        self.now += timedelta(minutes=7)
        self.f.finish(1, ORDER, 'success')
        self.now += timedelta(minutes=29)
        self.assertEqual([], self.f.due(1))
        self.now += timedelta(minutes=4)
        self.assertEqual(['two_factor', 'password_check', 'device_cleanup'], self.f.due(1))
        self.now += timedelta(days=10)
        self.assertEqual(list(ORDER), self.f.due(1))
        self.complete()
        self.assertEqual([], self.f.due(1))

    def test_restart_retains_timestamps(self):
        self.complete()
        saved = self.f.rows(1)
        other = TaskStore(self.path)
        restarted = FeatureSchedule(other, lambda: self.now)
        restarted.recover_interrupted()
        restarted.sync(1, self.config)
        self.assertEqual(saved, restarted.rows(1))
        self.assertEqual([], restarted.due(1))
        other.db.close()

    def test_global_changes_recalculate_from_completion_custom_isolated(self):
        custom = copy.deepcopy(self.config)
        custom['features']['password_update'].update(source='custom', effective_minutes=17)
        self.f.sync(2, custom)
        self.f.finish(2, ORDER, 'success')
        self.complete()
        self.now += timedelta(minutes=10)
        self.config['features']['password_update']['effective_minutes'] = 5
        self.f.sync(1, self.config)
        self.f.sync(2, custom)
        self.assertIn('password_update', self.f.due(1))
        self.assertNotIn('password_update', self.f.due(2))
        self.assertEqual(17, self.f.rows(2)['password_update']['minutes'])

    def test_disable_cancel_and_enable_immediate(self):
        self.f.force(1)
        self.config['features']['password_update']['enabled'] = False
        self.f.sync(1, self.config)
        row = self.f.rows(1)['password_update']
        self.assertIsNone(row['next_run_at'])
        self.assertEqual(1440, row['minutes'])
        self.assertNotIn('password_update', self.f.due(1))
        self.config['features']['password_update']['enabled'] = True
        self.f.sync(1, self.config)
        self.assertIn('password_update', self.f.due(1))

    def test_crash_does_not_replay_uncertain_mutation(self):
        self.f.start(1, ['password_update'])
        self.store.create_run('submitted-run', 1, ['password_update'])
        self.store.update_run('submitted-run', status='running', stage='mutation_submitted',
                              mutation_feature='password_update', mutation_state='submitted')
        self.f.recover_interrupted()
        self.now += timedelta(days=100)
        self.f.sync(1, self.config)
        self.assertNotIn('password_update', self.f.due(1))
        self.assertEqual('uncertain', self.f.rows(1)['password_update']['result'])

    def test_later_login_never_bypasses_candidate_password_reconciliation(self):
        self.f.start(1, ['password_update'])
        self.f.finish(1, ['password_update'], 'uncertain', 'Apple 未明确确认密码更新结果')
        self.assertIsNone(self.f.rows(1)['password_update']['next_run_at'])
        self.now += timedelta(minutes=10)
        self.f.start(1, ['password_check'])
        self.f.finish(1, ['password_check'], 'success', '账号密码正确性检测成功')
        self.f.sync(1, self.config)
        row = self.f.rows(1)['password_update']
        self.assertEqual('uncertain', row['result'])
        self.assertIsNone(row['next_run_at'])
        self.assertIsNotNone(row['reconcile_at'])

    def test_crash_before_mutation_is_safely_requeued(self):
        self.f.start(1, ['device_cleanup'])
        self.store.create_run('login-run', 1, ['device_cleanup'])
        self.store.update_run('login-run', status='running', stage='login',
                              mutation_feature='', mutation_state='none')
        self.f.recover_interrupted()
        row = self.f.rows(1)['device_cleanup']
        self.assertEqual('failed', row['result'])
        self.assertEqual(self.now, datetime.fromisoformat(row['next_run_at']))

    def test_older_password_check_cannot_clear_newer_uncertain_update(self):
        self.f.start(1, ['password_check'])
        self.f.finish(1, ['password_check'], 'success')
        self.now += timedelta(minutes=1)
        self.f.start(1, ['password_update'])
        self.f.finish(1, ['password_update'], 'uncertain')
        self.f.sync(1, self.config)
        self.assertEqual('uncertain', self.f.rows(1)['password_update']['result'])
        self.assertIsNone(self.f.rows(1)['password_update']['next_run_at'])

    def test_first_migration_preserves_legacy_due(self):
        due = (self.now + timedelta(minutes=21)).isoformat()
        self.f.sync(9, self.config, legacy={'next_run_at': due})
        self.assertTrue(all(row['next_run_at'] == due for row in self.f.rows(9).values()))

    def test_frozen_never_reenabled_by_sync(self):
        self.f.sync(1, self.config, account_enabled=False)
        self.now += timedelta(days=100)
        self.f.sync(1, self.config, account_enabled=False)
        self.assertEqual([], self.f.due(1))

    def test_mask_prevents_undue_profile_and_password_actions(self):
        info = {'profile_restore': {'name': 'baseline'}, 'enable_auto_update_password': True}
        result = self.f.mask(info, ['password_check'])
        self.assertFalse(result['enable_auto_update_password'])
        self.assertFalse(result['enable_auto_restore_profile'])
        self.assertFalse(result['enable_delete_devices'])
        self.assertNotIn('profile_restore', result)
        self.assertIn('profile_restore', info)

    def test_technical_retry_is_bounded(self):
        for attempt in range(4):
            self.f.finish(1, ['password_update'], 'failed')
            self.f.retry_technical(1, ['password_update'], (30, 120, 300), 3)
            next_run = datetime.fromisoformat(self.f.rows(1)['password_update']['next_run_at'])
            if attempt < 3:
                self.assertEqual(self.now + timedelta(seconds=(30, 120, 300)[attempt]), next_run)
            else:
                self.assertGreaterEqual(next_run, self.now + timedelta(days=1))
                self.assertLessEqual(next_run, self.now + timedelta(days=1, minutes=3))

    def test_scheduler_sync_preserves_future_and_cancels_disabled_queue(self):
        self.complete()
        self.store.upsert(1, 'success', next_run_at=self.f.next_run(1))
        queue = MagicMock(put=AsyncMock(), remove=AsyncMock())
        panel = MagicMock(automation_schedules=AsyncMock(return_value=[{'id': 1, 'enable': True, 'schedule': self.config}]), report_schedule_states=AsyncMock())
        scheduler = Scheduler(self.store, queue)
        scheduler.features = self.f
        with patch('app.scheduler.PanelClient', return_value=panel):
            asyncio.run(scheduler.sync())
            queue.put.assert_not_awaited()
            self.now += timedelta(minutes=33)
            asyncio.run(scheduler.sync())
            queue.put.assert_awaited_once_with(1)
            panel.automation_schedules.return_value[0]['enable'] = False
            asyncio.run(scheduler.sync())
        self.assertEqual('disabled', self.store.get(1)['status'])
        self.assertEqual([], self.f.due(1))


class FeatureRunnerTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.store = TaskStore(str(Path(self.directory.name) / 'tasks.sqlite3'))
        self.info = {'enable': True, 'username': 'fixture@example.test', 'password': 'fixture-only',
                     'schedule': configuration(), 'profile_restore': {'name': 'baseline'}}
        self.w = MagicMock()
        self.w.store = self.store
        self.w.panel.task_info = AsyncMock(side_effect=lambda _: copy.deepcopy(self.info))
        async def report(username, ok, message, password=None, **kwargs):
            if password: self.info['password'] = password
        self.w.panel.report = AsyncMock(side_effect=report)
        self.w.panel.disable_account = AsyncMock()
        self.w.panel.report_proxy_error = AsyncMock()
        self.w.panel.report_proxy_success = AsyncMock()
        self.w._mailbox_has_security_rollback = AsyncMock(return_value=False)
        self.w._email_recovery_available.return_value = True
        self.w._auto_email_recovery = AsyncMock(return_value=(True, 'recovered', None, None))
        self.w._auto_disable_two_factor = AsyncMock(return_value=(True, 'rolled back', None, None))
        self.client = MagicMock()
        self.actions = []

        async def drive(action, payload):
            self.actions.append((action, copy.deepcopy(payload)))
            if action == 'check':
                return {'status': True, 'state': 'normal', 'profile': {'name': 'baseline'}}
            if action == 'delete_devices':
                return {'status': True, 'removed_count': 2, 'device_removal_confirmed': True,
                        'device_removal_started': True}
            return {'status': True, 'password': payload['new_password']}
        self.client.run = AsyncMock(side_effect=drive)
        self.w.executor = AccountExecutor(self.client)
        self.runner = FeatureRunner(self.w)

    async def asyncTearDown(self):
        self.store.db.close()
        self.directory.cleanup()

    async def test_profile_reconciliation_uses_proxy_and_never_requests_mutation(self):
        self.runner.features.sync(1, self.info['schedule'])
        self.runner.features.finish(1, ['profile_restore'], 'uncertain')
        self.info.update(proxy_id=7, proxy_required=True, proxy_available=True,
                         proxy_protocol='http', proxy_content='fixture:1234')
        self.client.run_workflow = AsyncMock(return_value={
            'status': True, 'state': 'normal', 'reconciliation': {'reconciliation_status': 'not_applied'}})
        await self.runner._reconcile(1, self.info, 'profile_restore')
        payload = self.client.run_workflow.call_args.args[0]
        self.assertEqual('fixture:1234', payload['proxy_content'])
        self.assertEqual(['password_check'], payload['features'])
        self.assertEqual({'kind': 'profile_restore'}, payload['reconciliation'])
        self.assertIn('profile_restore', self.runner.features.due(1))
        self.client.run.assert_not_awaited()

    async def test_future_password_update_keeps_original_due_time(self):
        self.runner.features.sync(1, self.info['schedule'])
        due = (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()
        self.store.db.execute("UPDATE feature_schedule SET next_run_at=? WHERE account_id=1 AND feature='password_update'", (due,))
        self.store.db.commit()
        await self.runner.run(1, self.info)
        row = self.runner.features.rows(1)['password_update']
        self.assertEqual(due, row['next_run_at'])
        self.assertEqual('never', row['result'])
        self.assertNotIn('update_password', [action for action, _ in self.actions])

    async def test_reconciliation_waits_for_required_proxy(self):
        self.runner.features.sync(1, self.info['schedule'])
        self.runner.features.finish(1, ['profile_restore'], 'uncertain')
        self.info.update(proxy_required=True, proxy_available=False)
        self.client.run_workflow = AsyncMock()
        await self.runner._reconcile(1, self.info, 'profile_restore')
        self.client.run_workflow.assert_not_awaited()
        self.assertEqual('uncertain', self.runner.features.rows(1)['profile_restore']['result'])

    async def test_workflow_password_writeback_failure_retains_and_replays_only_writeback(self):
        calls = []
        class Client:
            async def run_workflow(client, payload, authorization_probe=None, progress_callback=None):
                calls.append(copy.deepcopy(payload))
                return {'status': True, 'state': 'normal', 'password': payload['new_password'],
                        'password_change_confirmed': True, 'password_submitted': False,
                        'step_results': {key: {'status': 'success'} for key in payload['features']}}
        self.w.executor = AccountExecutor(Client())
        working_report = self.w.panel.report
        self.w.panel.report = AsyncMock(side_effect=RuntimeError('fixture writeback outage'))
        config = replace(app_settings, workflow_v2_enabled=True, workflow_v2_percent=100)
        with patch('app.feature_runner.settings', config):
            await self.runner.run(1, self.info)
        files = list((Path(self.directory.name) / 'scheduled-password-escrow').glob('*.secret'))
        self.assertEqual(1, len(files))
        self.assertEqual(calls[0]['new_password'], files[0].read_text())
        self.assertEqual('uncertain', self.runner.features.rows(1)['password_update']['result'])
        self.w.panel.report = working_report
        # A panel-only writeback may succeed even when Apple egress is unavailable.
        self.info.update(proxy_required=True, proxy_available=False)
        await self.runner._reconcile(1, self.info, 'password_update')
        self.assertEqual(calls[0]['new_password'], self.info['password'])
        self.assertFalse(files[0].exists())
        self.assertEqual(1, len(calls))
        self.assertEqual('success', self.runner.features.rows(1)['password_update']['result'])

    async def test_confirmed_terminal_progress_survives_lost_driver_response(self):
        class Client:
            async def run_workflow(client, payload, authorization_probe=None, progress_callback=None):
                await progress_callback({'stage':'completed', 'current_feature':'',
                    'mutation_state':'confirmed', 'mutation_feature':'password_update',
                    'step_results':{'password_update':{'status':'success', 'password_change_confirmed':True}}})
                raise ConnectionError('fixture lost response')
        self.w.executor = AccountExecutor(Client())
        with patch('app.feature_runner.settings', replace(app_settings, workflow_v2_enabled=True, workflow_v2_percent=100)):
            await self.runner.run(1, self.info)
        self.assertEqual('uncertain', self.runner.features.rows(1)['password_update']['result'])
        run = self.store.recent_runs(1, 1)[0]
        self.assertEqual('writeback', run['retry_decision'])
        self.assertEqual(1, len(list((Path(self.directory.name)/'scheduled-password-escrow').glob('*.secret'))))

    async def test_due_all_merge_check_then_update(self):
        await self.runner.run(1, self.info)
        self.assertEqual(['check', 'delete_devices', 'update_password'], [x[0] for x in self.actions])
        self.assertTrue(all(r['result'] == 'success' for r in self.runner.features.rows(1).values()))
        report = self.w.panel.report.await_args
        self.assertEqual(self.actions[2][1]['new_password'], report.args[3])
        self.assertEqual(report.args[3], self.info['password'])
        for text in ('双重认证检测成功', '账号密码正确性检测成功',
                     '账号资料检测成功', '已从账户移除 2 台设备', '自动更新密码成功'):
            self.assertIn(text, report.args[2])
        feature_logs = self.store.db.execute(
            "SELECT action,message FROM task_logs WHERE account_id=? AND action IN (?,?,?,?,?)",
            (1, *ORDER),
        ).fetchall()
        self.assertEqual(set(ORDER), {row['action'] for row in feature_logs})
        self.assertTrue(all(row['action'] != 'password_update' or
                            '新密码已同步到账号资产页面' in row['message']
                            for row in feature_logs))
        await self.runner.run(1, self.info)
        self.assertEqual(3, len(self.actions))

    async def test_missing_assigned_proxy_blocks_run_steps_without_calling_driver(self):
        self.info.update(proxy_required=True, proxy_available=False)

        await self.runner.run(1, self.info)

        run = self.store.db.execute(
            "SELECT * FROM task_runs WHERE account_id=1 ORDER BY queued_at DESC LIMIT 1"
        ).fetchone()
        steps = self.store.db.execute(
            "SELECT status,stage,error_code FROM task_run_steps WHERE run_id=?", (run["run_id"],)
        ).fetchall()
        self.assertEqual("proxy_unavailable", run["error_code"])
        self.assertTrue(steps)
        self.client.run.assert_not_awaited()
        self.assertTrue(all(row["status"] == "blocked" for row in steps))
        self.assertTrue(all(row["stage"] == "failed" for row in steps))
        self.assertTrue(all(row["error_code"] == "proxy_unavailable" for row in steps))

    async def test_composite_workflow_uses_one_driver_job_and_persists_timeline(self):
        class CompositeClient:
            def __init__(self):
                self.calls = []

            async def run_workflow(client, payload, authorization_probe=None, progress_callback=None):
                client.calls.append(copy.deepcopy(payload))
                await progress_callback({
                    'job_id': 'driver-job', 'stage': 'login', 'current_feature': 'password_check',
                    'step_results': {},
                })
                steps = {}
                for key in ('profile_restore', 'device_cleanup', 'password_update'):
                    if key in payload['features']:
                        approved = await authorization_probe(key)
                        steps[key] = {'status': 'success' if approved else 'cancelled',
                                      'stage': 'result_confirmation'}
                for key in ('two_factor', 'password_check'):
                    if key in payload['features']:
                        steps[key] = {'status': 'success', 'stage': 'login'}
                steps.get('device_cleanup', {}).update(removed_count=0, no_devices=True)
                await progress_callback({
                    'job_id': 'driver-job', 'stage': 'result_confirmation',
                    'current_feature': 'password_update', 'step_results': steps,
                })
                return {
                    'status': True, 'state': 'normal', 'step_results': steps,
                    'password': payload.get('new_password'), 'password_change_confirmed': True,
                }

        client = CompositeClient()
        self.w.executor = AccountExecutor(client)
        runner = FeatureRunner(self.w)
        with patch('app.feature_runner.settings', replace(
                app_settings, workflow_v2_enabled=True, workflow_v2_percent=100)):
            await runner.run(1, self.info)
        self.assertEqual(1, len(client.calls))
        self.assertEqual(list(ORDER), client.calls[0]['features'])
        runs = self.store.recent_runs(1)
        self.assertEqual('success', runs[0]['status'])
        self.assertEqual('driver-job', runs[0]['driver_job_id'])
        self.assertEqual(set(ORDER), {step['feature'] for step in runs[0]['steps']})

    async def test_lock_loss_cancels_every_not_yet_started_mutation(self):
        class CompositeClient:
            async def run_workflow(client, payload, authorization_probe=None, progress_callback=None):
                steps = {'password_check': {'status': 'success', 'stage': 'login'}}
                for key in ('profile_restore', 'device_cleanup', 'password_update'):
                    steps[key] = {'status': 'success' if await authorization_probe(key) else 'cancelled',
                                  'stage': 'authorization'}
                return {'status': True, 'state': 'normal', 'step_results': steps}

        self.w.queue.owns_lock = AsyncMock(return_value=False)
        self.w.executor = AccountExecutor(CompositeClient())
        runner = FeatureRunner(self.w)
        with patch('app.feature_runner.settings', replace(
                app_settings, workflow_v2_enabled=True, workflow_v2_percent=100)):
            await runner.run(1, self.info, lock_token='lost-lock')
        rows = runner.features.rows(1)
        self.assertEqual('cancelled', rows['profile_restore']['result'])
        self.assertEqual('cancelled', rows['device_cleanup']['result'])
        self.assertEqual('cancelled', rows['password_update']['result'])

    async def test_only_check_never_updates_or_restores(self):
        for key in ORDER:
            self.info['schedule']['features'][key]['enabled'] = key == 'password_check'
        await self.runner.run(1, self.info)
        self.assertEqual(['check'], [x[0] for x in self.actions])
        self.assertNotIn('profile_restore', self.actions[0][1])

    async def test_email_recovery_resets_password_timer_without_duplicate(self):
        self.client.run = AsyncMock(return_value={'status': True, 'state': 'password_invalid'})
        await self.runner.run(1, self.info)
        self.w._auto_email_recovery.assert_awaited_once()
        self.assertEqual(1, self.client.run.await_count)
        self.assertEqual('success', self.runner.features.rows(1)['password_update']['result'])
        self.assertNotIn('password_update', self.runner.features.due(1))

    async def test_security_rollback_also_avoids_duplicate_password_rotation(self):
        self.w._mailbox_has_security_rollback.return_value = True
        await self.runner.run(1, self.info)
        self.w._auto_disable_two_factor.assert_awaited_once()
        self.assertEqual(['check', 'delete_devices'], [x[0] for x in self.actions])
        self.assertEqual('success', self.runner.features.rows(1)['password_update']['result'])

    async def test_timeout_after_update_never_replays(self):
        async def driver(action, payload):
            if action == 'check': return {'status': True, 'state': 'normal', 'profile': {'name': 'baseline'}}
            if action == 'delete_devices':
                return {'status': True, 'removed_count': 0, 'device_removal_confirmed': True}
            raise TimeoutError()
        self.client.run.side_effect = driver
        await self.runner.run(1, self.info)
        self.assertEqual('uncertain', self.runner.features.rows(1)['password_update']['result'])
        self.w.panel.disable_account.assert_not_awaited()
        await self.runner.run(1, self.info)
        self.assertEqual(3, self.client.run.await_count)

    async def test_unconfirmed_device_removal_does_not_replay_or_freeze_other_features(self):
        for key in ORDER:
            self.info['schedule']['features'][key]['enabled'] = key == 'device_cleanup'

        async def driver(action, payload):
            if action == 'check':
                return {'status': True, 'state': 'normal'}
            return {
                'status': False, 'error_code': 'device_removal_unconfirmed',
                'message': 'Apple 未明确确认设备已移除',
                'device_removal_started': True,
            }

        self.client.run.side_effect = driver
        await self.runner.run(1, self.info)
        row = self.runner.features.rows(1)['device_cleanup']
        self.assertEqual('uncertain', row['result'])
        self.assertIsNone(row['next_run_at'])
        self.w.panel.disable_account.assert_not_awaited()
        await self.runner.run(1, self.info)
        self.assertEqual(2, self.client.run.await_count)

    async def test_device_transport_uncertainty_keeps_password_update_schedulable(self):
        async def driver(action, payload):
            if action == 'check':
                return {'status': True, 'state': 'normal'}
            if action == 'delete_devices':
                raise TimeoutError('driver disconnected')
            return {'status': True, 'password': 'SHOULD_NOT_RUN'}

        self.client.run.side_effect = driver
        await self.runner.run(1, self.info)
        rows = self.runner.features.rows(1)
        self.assertEqual('uncertain', rows['device_cleanup']['result'])
        self.assertIsNone(rows['device_cleanup']['next_run_at'])
        self.assertEqual('blocked', rows['password_update']['result'])
        self.assertIsNotNone(rows['password_update']['next_run_at'])
        self.w.panel.disable_account.assert_not_awaited()

    async def test_proxy_failure_ends_run_without_switching_assigned_endpoint(self):
        self.info.update(proxy_id=7, proxy_required=True, proxy_available=True,
                         proxy_protocol='http', proxy_content='assigned.test:80')
        update_payloads = []

        async def driver(action, payload):
            if action == 'check':
                return {'status': True, 'state': 'normal', 'profile': {'name': 'baseline'}}
            if action == 'delete_devices':
                return {'status': True, 'removed_count': 0, 'device_removal_confirmed': True}
            update_payloads.append(copy.deepcopy(payload))
            return {
                'status': False, 'error_code': 'proxy_error',
                'message': '密保问题验证请求失败（网络或代理异常）',
                'stage': 'login', 'password_submitted': True,
            }

        self.client.run.side_effect = driver
        await self.runner.run(1, self.info)
        self.assertEqual(1, len(update_payloads))
        self.assertEqual('assigned.test:80', update_payloads[0]['proxy_content'])
        self.w.panel.report_proxy_error.assert_awaited_once_with(7)
        escrow = Path(self.directory.name) / 'scheduled-password-escrow'
        self.assertEqual([], list(escrow.glob('account-1-*.secret')))
        self.assertEqual('blocked', self.runner.features.rows(1)['password_update']['result'])

    async def test_disabled_config_cancels_queued_execution(self):
        self.runner.features.sync(1, self.info['schedule'])
        for item in self.info['schedule']['features'].values(): item['enabled'] = False
        await self.runner.run(1, self.info)
        self.client.run.assert_not_awaited()

    async def test_edit_during_login_cancels_not_yet_submitted_password_update(self):
        async def check(action, payload):
            self.info['schedule']['features']['password_update']['enabled'] = False
            return {'status': True, 'state': 'normal', 'profile': {'name': 'baseline'}}
        self.client.run.side_effect = check
        await self.runner.run(1, self.info)
        self.assertEqual(2, self.client.run.await_count)
        self.assertEqual('cancelled', self.runner.features.rows(1)['password_update']['result'])

    async def test_edit_during_login_cancels_not_yet_submitted_device_cleanup(self):
        for key in ORDER:
            self.info['schedule']['features'][key]['enabled'] = key == 'device_cleanup'

        async def check(action, payload):
            self.info['schedule']['features']['device_cleanup']['enabled'] = False
            return {'status': True, 'state': 'normal'}

        self.client.run.side_effect = check
        await self.runner.run(1, self.info)
        self.assertEqual(['check'], [call.args[0] for call in self.client.run.await_args_list])
        self.assertEqual('cancelled', self.runner.features.rows(1)['device_cleanup']['result'])

    async def test_existing_account_lock_serializes_same_account(self):
        queue = MagicMock()
        locked = set()
        async def acquire(account_id, ttl):
            if account_id in locked: return None
            locked.add(account_id)
            return 'fixture-lock'
        async def release(account_id, token): locked.remove(account_id)
        queue.acquire_lock = AsyncMock(side_effect=acquire)
        queue.release_lock = AsyncMock(side_effect=release)
        worker = Worker(self.store, queue)
        started, finish = asyncio.Event(), asyncio.Event()
        calls = []
        async def execute(account_id, token=None):
            calls.append(account_id)
            started.set()
            await finish.wait()
        worker._run_locked = execute
        first = asyncio.create_task(worker.run_one(1))
        await started.wait()
        await worker.run_one(1)
        self.assertEqual([1], calls)
        finish.set()
        await first
        self.assertEqual(set(), locked)
