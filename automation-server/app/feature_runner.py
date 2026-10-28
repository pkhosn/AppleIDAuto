"""Independent cadence execution under Worker's existing Redis account lock."""
from .feature_schedule import FeatureSchedule
from datetime import datetime
import hashlib
from uuid import uuid4
from pathlib import Path
from scripts.controlled_recover import load_or_create_escrow, generate_recovery_password
from .config import settings
from .notifier import notify_account_event


class FeatureRunner:
    def __init__(self, worker):
        self.worker = worker
        self.features = FeatureSchedule(worker.store)

    @staticmethod
    def _feature_message(key, result, base_message, password=None, profile=None, recovery_kind=None):
        labels = {
            'two_factor': '双重认证检测', 'password_check': '账号密码正确性检测',
            'profile_restore': '账号资料检测与恢复', 'device_cleanup': '自动移除设备',
            'password_update': '自动更新密码',
        }
        label = labels.get(key, key)
        if result != 'success':
            states = {'failed': '失败', 'blocked': '未执行', 'uncertain': '结果待确认', 'cancelled': '已取消'}
            return f"{label}{states.get(result, result)}：{base_message}"
        if key == 'password_check':
            return '账号密码异常恢复成功：' + base_message if recovery_kind == 'email_password' else '账号密码正确性检测成功：账号登录状态正常'
        if key == 'password_update':
            if password:
                return '自动更新密码成功：新密码已同步到账号资产页面'
            if recovery_kind:
                return f'密码恢复成功并已同步到账号资产页面：{base_message}'
            return '自动更新密码成功'
        if key == 'two_factor':
            return f'自动解除双重认证成功：{base_message}' if recovery_kind == 'two_factor' else '双重认证检测成功：未发现需要解除的双重认证'
        if key == 'profile_restore':
            if isinstance(profile, dict) and profile.get('summary'):
                return str(profile['summary'])
            if isinstance(profile, dict) and profile:
                return '账号资料检测成功：旧驱动未返回逐字段结果'
            return '账号资料检测未完成：未返回四项资料结果'
        return f'{label}成功'

    async def run(self, account_id, info, lock_token=None):
        w, f = self.worker, self.features
        row = w.store.get(account_id)
        f.sync(account_id, info['schedule'], bool(info.get('enable')), dict(row) if row else None)
        reconciliation_due = f.reconciliation_due(account_id)
        if reconciliation_due:
            await self._reconcile(account_id, info, reconciliation_due[0], lock_token)
            return
        due = f.due(account_id)
        if not due:
            w.store.upsert(account_id, 'success', '暂无到期功能', f.next_run(account_id))
            return
        run_info = f.mask(info, due)
        run_id = uuid4().hex
        w.store.create_run(run_id, account_id, due, str(info.get('config_revision') or ''))
        rollout_bucket = int.from_bytes(hashlib.sha256(str(account_id).encode()).digest()[:4], 'big') % 100
        workflow_supported = hasattr(w.executor.client.__class__, 'run_workflow')
        run_info['_use_workflow'] = bool(
            settings.workflow_v2_enabled and rollout_bucket < settings.workflow_v2_percent
            and workflow_supported
        )
        run_info['_run_id'] = run_id
        run_info['_due_features'] = list(due)
        run_info['_feature_outcomes'] = {}
        run_info['_feature_messages'] = {}
        outcomes = run_info['_feature_outcomes']
        async def authorized(feature):
            if lock_token is not None and not await w.queue.owns_lock(account_id, lock_token):
                w.store.update_run(run_id, status='failed', stage='lock_lost', feature=feature,
                                   error_code='account_lock_lost', retry_decision='safe_retry')
                return False
            fresh = await w.panel.task_info(account_id)
            # Use the last completion to honor config changes during the login check.
            f.sync(account_id, fresh['schedule'], bool(fresh.get('enable')))
            current = f.rows(account_id)[feature]
            return bool(current['enabled'] and current['next_run_at'] and
                        datetime.fromisoformat(current['next_run_at']) <= f.clock())
        async def before_password_update():
            return await authorized('password_update')
        async def before_device_cleanup():
            return await authorized('device_cleanup')
        run_info['_before_password_update'] = before_password_update
        run_info['_before_device_cleanup'] = before_device_cleanup
        run_info['_authorize_feature'] = authorized

        async def workflow_progress(job):
            stage = str(job.get('stage') or 'running')
            feature = str(job.get('current_feature') or '')
            driver_job_id = str(job.get('job_id') or '')
            mutation_state = None
            mutation_feature = None
            if (feature == 'password_update' and stage == 'mutation_submitted'
                    or job.get('mutation_feature') == 'password_update' and job.get('mutation_state') in {'submitted', 'confirmed'}):
                run_info['_mutation_started'] = True
            if job.get('mutation_state') in {'submitted', 'confirmed'}:
                mutation_state = job['mutation_state']
                mutation_feature = str(job.get('mutation_feature') or feature)
            elif stage == 'mutation_submitted' and feature:
                mutation_state, mutation_feature = 'submitted', feature
            elif stage == 'result_confirmation' and feature:
                current = w.store.run(run_id) or {}
                if current.get('mutation_feature') == feature:
                    mutation_state, mutation_feature = 'confirmed', feature
            current_run = w.store.run(run_id) or {}
            w.store.update_run(
                run_id, status='running', stage=stage, feature=feature,
                driver_job_id=driver_job_id, mutation_state=mutation_state,
                mutation_feature=mutation_feature,
                started=bool(job.get('started_at') and not current_run.get('started_at')),
            )
            step_results = job.get('step_results') if isinstance(job.get('step_results'), dict) else {}
            for key, value in step_results.items():
                if not isinstance(value, dict):
                    continue
                status = str(value.get('status') or 'running')
                metadata = {key: value[key] for key in (
                    'device_target_hash', 'device_before_count', 'removed_count', 'no_devices',
                    'password_submitted', 'password_change_confirmed',
                ) if key in value}
                w.store.update_run_step(
                    run_id, key, status, stage=str(value.get('stage') or stage),
                    error_code=str(value.get('error_code') or ''),
                    message=str(value.get('message') or ''), finished=status not in {'queued', 'running'},
                    metadata=metadata,
                )
        run_info['_workflow_progress'] = workflow_progress
        escrow_path = None
        escrow_password = None
        def prepare_password():
            nonlocal escrow_path, escrow_password
            # Repeated preparation within this run must reuse the exact
            # candidate prepared for this run; an on-disk escrow from an older
            # uncertain run must still block a new mutation.
            if escrow_path is not None and escrow_password is not None:
                return escrow_password
            database_file = w.store.db.execute('PRAGMA database_list').fetchone()[2]
            directory = Path(database_file).parent / 'scheduled-password-escrow'
            if list(directory.glob(f'account-{account_id}-*.secret')):
                raise RuntimeError('unresolved_password_escrow')
            escrow_path, escrow_password = load_or_create_escrow(
                directory, account_id, generate_recovery_password,
            )
            return escrow_password
        run_info['_prepare_password'] = prepare_password
        f.start(account_id, due)
        w.store.update_run(
            run_id, status='running', stage='preflight',
            started=not run_info['_use_workflow'],
        )
        for key in due:
            w.store.update_run_step(run_id, key, 'running', stage='preflight', started=True)
        recovered = False
        recovery_kind = None
        try:
            # Mailbox check must be due, not merely enabled on the account.
            if 'two_factor' in due and await w._mailbox_has_security_rollback(account_id, run_info) and await authorized('two_factor'):
                ok, message, password, code = await w._auto_disable_two_factor(account_id, run_info)
                profile = None
                outcomes['two_factor'] = 'success' if ok else 'failed'
                # The recovery changes authentication state: reload before further actions.
                if ok:
                    # Security rollback also changes the password. Do not rotate it twice.
                    recovered = True
                    recovery_kind = 'two_factor'
                    outcomes['password_update'] = 'success'
                    f.finish(account_id, ['password_update'], 'success', '安全设置恢复已改密；重置定期改密周期')
                    run_info['enable_auto_update_password'] = False
                    info = await w.panel.task_info(account_id)
                    run_info.update({key: info[key] for key in ('password', 'enable') if key in info})
                    if info.get('enable'):
                        ok, message, password, code, profile = await w.executor.run(run_info)
            else:
                ok, message, password, code, profile = await w.executor.run(run_info)
            if escrow_path and outcomes.get('password_update') == 'success' and password is None:
                raise RuntimeError('confirmed_password_result_missing')
            # The driver gave a definitive pre-submission result. The prepared
            # candidate never reached Apple's password form and must not block
            # a later scheduled run. Unknown driver/report failures continue
            # to retain escrow through the exception path below.
            if escrow_path and not run_info.get('_mutation_started'):
                escrow_path.unlink(missing_ok=True)
                escrow_path = None
                escrow_password = None
            if not ok and code == 'two_factor' and 'two_factor' in due and await authorized('two_factor'):
                ok, message, password, code = await w._auto_disable_two_factor(account_id, run_info)
                outcomes['two_factor'] = 'success' if ok else 'failed'
                if ok:
                    recovered = True
                    recovery_kind = 'two_factor'
                    outcomes['password_update'] = 'success'
                    f.finish(account_id, ['password_update'], 'success', '安全设置恢复已改密；重置定期改密周期')
            elif not ok and code in {'password_invalid', 'authentication_challenge_unrendered'} and not run_info.get('_mutation_started') and w._email_recovery_available(run_info):
                fresh = await w.panel.task_info(account_id)
                if fresh.get('enable') and w._email_recovery_available(fresh):
                    ok, message, password, code = await w._auto_email_recovery(account_id, {**run_info, **fresh})
                recovered = ok
                if recovered:
                    recovery_kind = 'email_password'
                    outcomes['password_check'] = 'success'
                    outcomes['password_update'] = 'success'
                    f.finish(account_id, ['password_update'], 'success', '邮件异常恢复已改密；重置定期改密周期')
            uncertain = bool(run_info.get('_mutation_started') and not ok)
            summaries = []
            for key in due:
                result = outcomes.get(key, 'uncertain' if uncertain and key == 'password_update' else 'blocked')
                feature_message = run_info['_feature_messages'].get(key) or self._feature_message(
                    key, result, message, password, profile, recovery_kind,
                )
                f.finish(account_id, [key], result, feature_message)
                w.store.log(account_id, key, result, feature_message)
                w.store.update_run_step(
                    run_id, key, result, stage='completed' if result == 'success' else 'failed',
                    error_code=str(code or '') if result not in {'success', 'cancelled'} else '',
                    message=feature_message, finished=True,
                )
                summaries.append(feature_message)
            if summaries:
                message = '；'.join(summaries)
            if code in {'proxy_error', 'proxy_connect_failed', 'proxy_handshake_failed', 'proxy_unavailable'} and not run_info.get('_mutation_started') and not run_info.get('_device_mutation_started') and not run_info.get('_workflow_mutation_ever_started'):
                f.retry_technical(account_id, due, settings.technical_retry_delays, settings.max_retries)
            # Commit cadence before reporting: a report failure must not replay Apple actions.
            mutation_feature = ''
            mutation_state = 'none'
            for key in ('profile_restore', 'device_cleanup', 'password_update'):
                if outcomes.get(key) == 'uncertain':
                    mutation_feature, mutation_state = key, 'submitted'
                    break
                if outcomes.get(key) == 'success' and key in due:
                    mutation_feature, mutation_state = key, 'confirmed'
            w.store.update_run(
                run_id, status='reporting', stage='reporting', feature='',
                error_code=str(code or ''), message=message,
                mutation_feature=mutation_feature, mutation_state=mutation_state,
                retry_decision=('reconcile' if mutation_state == 'submitted' else
                                'safe_retry' if not ok else 'none'),
            )
            await w.panel.report(info['username'], ok, message, password, profile=profile)
            fresh = await w.panel.task_info(account_id)
            if password and escrow_path:
                if fresh.get('password') != password:
                    raise RuntimeError('password_writeback_unconfirmed')
                escrow_path.unlink()
                escrow_path = None
            f.sync(account_id, fresh['schedule'], bool(fresh.get('enable')))
            status = ('success' if ok else 'failed') if fresh.get('enable') else 'disabled'
            w.store.upsert(account_id, status, message, f.next_run(account_id))
            w.store.log(account_id, 'independent_run', status, message)
            w.store.update_run(
                run_id, status='success' if ok else 'failed', stage='completed',
                error_code=str(code or ''), message=message, finished=True,
            )
            try:
                if info.get('proxy_id'):
                    if code in {'proxy_error', 'proxy_connect_failed', 'proxy_handshake_failed'}:
                        await w.panel.report_proxy_error(int(info['proxy_id']))
                    else:
                        await w.panel.report_proxy_success(int(info['proxy_id']))
                await notify_account_event(str(info.get('username', '')), '自动化任务', ok, message,
                    state=str(code or ('normal' if ok else 'unknown')),
                    bot_token=str(info.get('tg_bot_token') or ''), chat_id=str(info.get('tg_chat_id') or ''),
                    notify_mode=str(info.get('tg_notify_mode') or 'all'))
            except Exception as exc:
                w.store.log(account_id, 'notify', 'failed', type(exc).__name__)
        except Exception as exc:
            journal = w.store.run(run_id) or {}
            writeback_recovery = False
            uncertain_feature = (
                str(journal.get('mutation_feature') or '')
                if journal.get('mutation_state') == 'submitted' else ''
            )
            for key in due:
                if f.rows(account_id)[key]['result'] == 'running':
                    result = outcomes.get(key, 'uncertain' if key == uncertain_feature else 'failed')
                    message = ('执行结果待只读核验：' if result == 'uncertain' else '执行中断，已安全重新排期：') + type(exc).__name__
                    f.finish(account_id, [key], result, message)
                    w.store.update_run_step(
                        run_id, key, result, stage='interrupted', error_code=type(exc).__name__,
                        message=message, finished=True,
                    )
            # Apple already confirmed the password change, but the panel did
            # not persist it. Keep the escrow and enqueue a writeback-only
            # recovery; never log in to Apple or submit another change.
            if (run_info.get('_mutation_started') and escrow_path
                    and journal.get('mutation_state') == 'confirmed'
                    and journal.get('mutation_feature') == 'password_update'):
                recovery_message = 'Apple 已确认改密，主站回写失败；将仅重试回写预存凭据'
                f.finish(account_id, ['password_update'], 'uncertain', recovery_message)
                f.force_reconciliation(account_id, 'password_update')
                w.store.update_run_step(
                    run_id, 'password_update', 'uncertain', stage='reporting',
                    error_code=type(exc).__name__, message=recovery_message, finished=True,
                    metadata={'writeback_only': True},
                )
                writeback_recovery = True
            elif run_info.get('_mutation_started') and escrow_path:
                f.finish(account_id, ['password_update'], 'uncertain', '改密结果待确认，预存凭据已保留；不会自动重做')
            w.store.upsert(account_id, 'failed', '执行或回报异常，未自动重复操作', f.next_run(account_id))
            w.store.log(account_id, 'independent_run', 'failed', type(exc).__name__)
            w.store.update_run(
                run_id, status='failed', stage='interrupted', error_code=type(exc).__name__,
                message='执行或回报异常',
                retry_decision=('writeback' if writeback_recovery else
                                'reconcile' if uncertain_feature else 'safe_retry'), finished=True,
            )

    async def _reconcile(self, account_id, info, feature, lock_token=None):
        """Read-only reconciliation for an uncertain mutation; never repeats it."""
        w, f = self.worker, self.features
        run_id = uuid4().hex
        w.store.create_run(run_id, account_id, [feature], str(info.get('config_revision') or ''))
        w.store.update_run(run_id, status='reconciling', stage='preflight', feature=feature, started=True)
        w.store.update_run_step(run_id, feature, 'running', stage='preflight', started=True)
        if lock_token is not None and not await w.queue.owns_lock(account_id, lock_token):
            f.finish_reconciliation(account_id, feature, 'uncertain', '只读核验取消：账号锁已失效')
            w.store.update_run(run_id, status='failed', stage='lock_lost', error_code='account_lock_lost',
                               retry_decision='reconcile', finished=True)
            return

        prior = None
        for old_run in w.store.recent_runs(account_id, 20):
            if old_run['run_id'] == run_id:
                continue
            prior = next((step for step in old_run['steps']
                          if step['feature'] == feature and step['status'] == 'uncertain'), None)
            if prior:
                break
        metadata = dict((prior or {}).get('metadata') or {})
        payload = w.executor._payload(info)
        payload.update({
            'run_id': run_id, 'config_revision': str(info.get('config_revision') or ''),
            'features': ['password_check'], 'reconciliation': {'kind': feature},
        })
        if feature == 'device_cleanup':
            payload['reconciliation'].update({
                'target_hash': str(metadata.get('device_target_hash') or ''),
                'before_count': int(metadata.get('device_before_count') or 0),
            })
        elif feature == 'profile_restore':
            payload['profile_restore'] = dict(info.get('profile_restore') or {})

        escrow_path = None
        checking_candidate = False
        if feature == 'password_update':
            database_file = w.store.db.execute('PRAGMA database_list').fetchone()[2]
            directory = Path(database_file).parent / 'scheduled-password-escrow'
            files = sorted(directory.glob(f'account-{account_id}-*.secret')) if directory.exists() else []
            attempts = int(f.rows(account_id)[feature].get('reconcile_attempts') or 0)
            if metadata.get('writeback_only') and len(files) == 1:
                escrow_path = files[0]
            elif attempts == 0 and len(files) == 1:
                escrow_path = files[0]
                payload['password'] = escrow_path.read_text(encoding='utf-8')
                checking_candidate = True
            elif attempts > 0 and len(files) == 1:
                escrow_path = files[0]
            elif attempts == 0:
                message = '改密结果待确认，但预存凭据不存在或不唯一，需人工处理'
                f.finish_reconciliation(account_id, feature, 'uncertain', message)
                w.store.update_run_step(run_id, feature, 'uncertain', stage='reconciling',
                                        error_code='escrow_unavailable', message=message, finished=True)
                w.store.update_run(run_id, status='failed', stage='completed', error_code='escrow_unavailable',
                                   retry_decision='manual', message=message, finished=True)
                w.store.upsert(account_id, 'failed', message, f.next_run(account_id))
                return

        try:
            if feature == 'password_update' and metadata.get('writeback_only') and escrow_path:
                password = escrow_path.read_text(encoding='utf-8')
                await w.panel.report(info['username'], True, '重试主站回写：Apple 已确认新密码生效', password)
                refreshed = await w.panel.task_info(account_id)
                if refreshed.get('password') != password:
                    raise RuntimeError('password_writeback_unconfirmed')
                escrow_path.unlink(missing_ok=True)
                message = '主站回写重试成功；未再次访问 Apple 或提交改密'
                f.finish_reconciliation(account_id, feature, 'success', message)
                w.store.update_run_step(run_id, feature, 'success', stage='reporting', message=message, finished=True)
                w.store.update_run(run_id, status='success', stage='completed', message=message,
                                   retry_decision='none', finished=True)
                w.store.log(account_id, feature + '_writeback', 'success', message)
                w.store.upsert(account_id, 'success', message, f.next_run(account_id))
                return

            if not info.get('enable', True):
                raise RuntimeError('account_disabled')
            if info.get('proxy_required') and not info.get('proxy_available'):
                raise RuntimeError('proxy_unavailable')

            async def progress(job):
                w.store.update_run(
                    run_id, status='reconciling', stage=str(job.get('stage') or 'reconciling'),
                    feature=feature, driver_job_id=str(job.get('job_id') or ''),
                )

            result = await w.executor.client.run_workflow(payload, progress_callback=progress)
            state = str(result.get('state') or 'unknown')
            reconciliation = result.get('reconciliation') if isinstance(result.get('reconciliation'), dict) else {}
            observed = str(reconciliation.get('reconciliation_status') or '')
            final = 'uncertain'
            if feature == 'password_update':
                if state == 'normal' and checking_candidate and escrow_path:
                    password = payload['password']
                    await w.panel.report(info['username'], True, '只读核验确认新密码已生效', password)
                    refreshed = await w.panel.task_info(account_id)
                    if refreshed.get('password') != password:
                        raise RuntimeError('password_writeback_unconfirmed')
                    escrow_path.unlink(missing_ok=True)
                    final, message = 'success', '只读核验确认候选密码已生效并完成回写'
                elif state == 'normal' and not checking_candidate:
                    if escrow_path:
                        escrow_path.unlink(missing_ok=True)
                    final, message = 'failed', '只读核验确认原密码仍有效，前次改密未生效'
                elif state == 'password_invalid' and checking_candidate:
                    message = '候选密码未生效；冷却后将只读核验原密码'
                else:
                    message = '密码状态仍无法确认，等待人工处理'
            elif observed == 'confirmed':
                final, message = 'success', '只读核验确认前次操作已生效'
            elif observed == 'not_applied':
                final, message = 'failed', '只读核验确认前次操作未生效，已恢复正常周期'
                if feature == 'profile_restore':
                    message = '只读核验完整读取资料，仍存在差异，已安排立即自动恢复'
            else:
                message = '只读核验仍无法唯一确认结果，等待人工处理'
                if feature == 'profile_restore':
                    message = '资料未能完整读取，保留待确认状态；核验次数未耗尽时自动重试'

            f.finish_reconciliation(account_id, feature, final, message)
            w.store.update_run_step(run_id, feature, final, stage='reconciling', message=message, finished=True)
            w.store.update_run(
                run_id, status='success' if final in {'success', 'failed'} else 'failed',
                stage='completed', message=message,
                retry_decision=('none' if final in {'success', 'failed'} else
                                'reconcile' if f.rows(account_id)[feature].get('reconcile_at') else 'manual'), finished=True,
            )
            w.store.log(account_id, feature + '_reconciliation', final, message)
            w.store.upsert(account_id, 'success' if final == 'success' else 'failed', message, f.next_run(account_id))
        except Exception as exc:
            message = '只读核验异常，未执行任何修改：' + type(exc).__name__
            f.finish_reconciliation(account_id, feature, 'uncertain', message)
            w.store.update_run_step(run_id, feature, 'uncertain', stage='reconciling',
                                    error_code=type(exc).__name__, message=message, finished=True)
            w.store.update_run(run_id, status='failed', stage='completed', error_code=type(exc).__name__,
                               retry_decision='manual', message=message, finished=True)
            w.store.upsert(account_id, 'failed', message, f.next_run(account_id))
